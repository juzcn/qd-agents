"""
重试与熔断机制
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Generic, TypeVar

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BackoffStrategy(str, Enum):
    """退避策略"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_WITH_JITTER = "exponential_with_jitter"


class RetryConfig(BaseModel):
    """重试配置"""
    max_attempts: int = 3
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL_WITH_JITTER
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30000
    multiplier: float = 2.0
    jitter: bool = True
    # Pydantic 无法直接序列化 type[Exception]，运行时使用
    retryable_exceptions: list = Field(default_factory=list, exclude=True)


class CircuitBreakerConfig(BaseModel):
    """熔断器配置"""
    enabled: bool = True
    error_rate_threshold: float = 0.5
    minimum_requests: int = 10
    half_open_timeout_ms: int = 30000
    half_open_max_requests: int = 3


@dataclass
class CircuitStats:
    """熔断器统计"""
    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_failure_time: float | None = None


class CircuitBreaker:
    """
    熔断器

    状态流转：
    CLOSED -> OPEN (失败率超过阈值)
    OPEN -> HALF_OPEN (冷却时间后)
    HALF_OPEN -> CLOSED (成功)
    HALF_OPEN -> OPEN (失败)
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.stats = CircuitStats()
        self._half_open_requests = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """
        获取执行权限

        Returns:
            是否允许执行
        """
        async with self._lock:
            if not self.config.enabled:
                return True

            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                # 检查是否可以进入半开状态
                if (
                    self.stats.last_failure_time
                    and (time.time() * 1000 - self.stats.last_failure_time) > self.config.half_open_timeout_ms
                ):
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_requests = 0
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_requests < self.config.half_open_max_requests:
                    self._half_open_requests += 1
                    return True
                return False

        return False

    async def record_success(self) -> None:
        """记录成功"""
        async with self._lock:
            self.stats.total_requests += 1
            self.stats.success_count += 1

            if self.state == CircuitState.HALF_OPEN:
                # 半开状态下成功，恢复到关闭状态
                self.state = CircuitState.CLOSED
                self._reset_stats()
                logger.info("Circuit breaker transitioning to CLOSED")

    async def record_failure(self) -> None:
        """记录失败"""
        async with self._lock:
            self.stats.total_requests += 1
            self.stats.failure_count += 1
            self.stats.last_failure_time = time.time() * 1000

            if self.state == CircuitState.HALF_OPEN:
                # 半开状态下失败，回到打开状态
                self.state = CircuitState.OPEN
                self.stats.last_failure_time = time.time() * 1000
                logger.info("Circuit breaker transitioning to OPEN")
                return

            if self.state == CircuitState.CLOSED:
                # 检查是否需要打开熔断器
                if self.stats.total_requests >= self.config.minimum_requests:
                    error_rate = self.stats.failure_count / self.stats.total_requests
                    if error_rate >= self.config.error_rate_threshold:
                        self.state = CircuitState.OPEN
                        logger.warning(
                            "Circuit breaker OPEN: error rate %.2f%%",
                            error_rate * 100
                        )

    def _reset_stats(self) -> None:
        """重置统计"""
        self.stats = CircuitStats()
        self._half_open_requests = 0


class RetryExecutor(Generic[T]):
    """
    重试执行器

    支持多种退避策略和熔断器
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self.config = config or RetryConfig()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def calculate_delay(self, attempt: int) -> float:
        """
        计算延迟时间（秒）

        Args:
            attempt: 尝试次数（从 0 开始）

        Returns:
            延迟秒数
        """
        strategy = self.config.backoff_strategy
        initial = self.config.initial_delay_ms / 1000
        max_delay = self.config.max_delay_ms / 1000
        multiplier = self.config.multiplier

        if strategy == BackoffStrategy.FIXED:
            delay = initial
        elif strategy == BackoffStrategy.LINEAR:
            delay = initial * (attempt + 1)
        elif strategy == BackoffStrategy.EXPONENTIAL:
            delay = initial * (multiplier ** attempt)
        elif strategy == BackoffStrategy.EXPONENTIAL_WITH_JITTER:
            base_delay = initial * (multiplier ** attempt)
            jitter = random.uniform(0, base_delay * 0.1)
            delay = base_delay + jitter
        else:
            delay = initial

        return min(delay, max_delay)

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        执行函数，带重试和熔断

        Args:
            func: 异步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数返回值

        Raises:
            Exception: 所有重试都失败后抛出最后一个异常
        """
        last_exception: Exception | None = None

        for attempt in range(self.config.max_attempts):
            # 检查熔断器
            if not await self.circuit_breaker.acquire():
                raise RuntimeError("Circuit breaker is OPEN")

            try:
                result = await func(*args, **kwargs)
                await self.circuit_breaker.record_success()
                return result

            except Exception as e:
                await self.circuit_breaker.record_failure()
                last_exception = e

                # 检查是否可重试
                if self.config.retryable_exceptions:
                    if not any(isinstance(e, exc_type) for exc_type in self.config.retryable_exceptions):
                        logger.debug("Exception not retryable, giving up")
                        raise

                # 最后一次尝试，直接抛出
                if attempt == self.config.max_attempts - 1:
                    logger.warning(
                        "All %d attempts failed",
                        self.config.max_attempts
                    )
                    raise

                # 等待后重试
                delay = self.calculate_delay(attempt)
                logger.warning(
                    "Attempt %d failed, retrying in %.2fs: %s",
                    attempt + 1,
                    delay,
                    e
                )
                await asyncio.sleep(delay)

        # 这里应该不会到达，但为了类型安全
        raise last_exception or RuntimeError("Unexpected error")


    Args:
        config: 重试配置
        circuit_breaker: 熔断器

    Returns:
        装饰器
    """
    def decorator(func: Callable[..., Awaitable[T]]):
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            executor = RetryExecutor[T](config=config, circuit_breaker=circuit_breaker)
            return await executor.execute(func, *args, **kwargs)
        return wrapper
    return decorator
