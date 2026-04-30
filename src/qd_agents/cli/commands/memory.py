"""
记忆管理命令

负责查看长期记忆内容。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config
from qd_agents.memory.store import MemoryStore

memory_app = typer.Typer(name="memory", help="长期记忆管理")


@memory_app.command("list")
def memory_list(


def _parse_interval(interval: str) -> tuple[datetime, datetime]:
    """
    解析多种时间区间格式，返回 (start, end)。

    支持格式：
    - 相对时长: 1d, 3h, 30m, 2w
    - 自然语言: today, yesterday, this_week, this_month
    - 日期时间范围: 2026-04-25 10:00~2026-04-27 14:30, 04-25 10:00~04-27 14:30
    - 日期范围: 2026-04-25~2026-04-27, 04-25~04-27
    - 当天时间范围: 10:00~14:30
    - 单日期时间: 2026-04-25 10:00, 04-25 10:00
    - 单日期: 2026-04-25, 04-25
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 自然语言
    natural = {
        "today": (today_start, now),
        "yesterday": (today_start - timedelta(days=1), today_start),
        "this_week": (today_start - timedelta(days=today_start.weekday()), now),
        "this_month": (today_start.replace(day=1), now),
    }
    key = interval.strip().lower()
    if key in natural:
        return natural[key]

    # 相对时长: 1d, 3h, 30m, 2w
    m = re.match(r"^(\d+)([dhmw])$", key)
    if m:
        value, unit = int(m.group(1)), m.group(2)
        deltas = {"d": timedelta(days=value), "h": timedelta(hours=value),
                  "m": timedelta(minutes=value), "w": timedelta(weeks=value)}
        return (now - deltas[unit], now)

    # 当天时间范围: 10:00~14:30, ~14:30, 10:00~
    m = re.match(r"^(?:(\d{2}:\d{2}))?~(?:(\d{2}:\d{2}))?$", key)
    if m and (m.group(1) or m.group(2)):
        start = datetime.combine(now.date(), datetime.strptime(m.group(1), "%H:%M").time()) if m.group(1) else today_start
        end = datetime.combine(now.date(), datetime.strptime(m.group(2), "%H:%M").time()) if m.group(2) else now
        return (start, end)

    # 日期时间范围: 2026-04-25 10:00~2026-04-27 14:30, 2026-04-25 10:00~, ~2026-04-27 14:30
    m = re.match(r"^(?:(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}))?~(?:(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}))?$", key)
    if m and (m.group(1) or m.group(2)):
        start = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") if m.group(1) else datetime.min
        end = datetime.strptime(m.group(2), "%Y-%m-%d %H:%M") if m.group(2) else now
        return (start, end)

    # 短日期时间范围: 04-25 10:00~04-27 14:30, 04-25 10:00~, ~04-27 14:30
    m = re.match(r"^(?:(\d{2}-\d{2}\s+\d{2}:\d{2}))?~(?:(\d{2}-\d{2}\s+\d{2}:\d{2}))?$", key)
    if m and (m.group(1) or m.group(2)):
        year = now.year
        start = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M") if m.group(1) else datetime.min
        end = datetime.strptime(f"{year}-{m.group(2)}", "%Y-%m-%d %H:%M") if m.group(2) else now
        return (start, end)

    # 日期范围: 2026-04-25~2026-04-27, 2026-04-25~, ~2026-04-27
    m = re.match(r"^(?:(\d{4}-\d{2}-\d{2}))?~(?:(\d{4}-\d{2}-\d{2}))?$", key)
    if m and (m.group(1) or m.group(2)):
        start = datetime.strptime(m.group(1), "%Y-%m-%d") if m.group(1) else datetime.min
        end = datetime.strptime(m.group(2), "%Y-%m-%d").replace(hour=23, minute=59, second=59) if m.group(2) else now
        return (start, end)

    # 短日期范围: 04-25~04-27, 04-25~, ~04-27
    m = re.match(r"^(?:(\d{2}-\d{2}))?~(?:(\d{2}-\d{2}))?$", key)
    if m and (m.group(1) or m.group(2)):
        year = now.year
        start = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d") if m.group(1) else datetime.min
        end = datetime.strptime(f"{year}-{m.group(2)}", "%Y-%m-%d").replace(hour=23, minute=59, second=59) if m.group(2) else now
        return (start, end)

    # 单日期时间: 2026-04-25 10:00
    m = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})$", key)
    if m:
        start = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
        end = start.replace(hour=23, minute=59, second=59)
        return (start, end)

    # 短日期时间: 04-25 10:00
    m = re.match(r"^(\d{2}-\d{2}\s+\d{2}:\d{2})$", key)
    if m:
        start = datetime.strptime(f"{now.year}-{m.group(1)}", "%Y-%m-%d %H:%M")
        end = start.replace(hour=23, minute=59, second=59)
        return (start, end)

    # 单日期: 2026-04-25
    m = re.match(r"^(\d{4}-\d{2}-\d{2})$", key)
    if m:
        start = datetime.strptime(m.group(1), "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
        return (start, end)

    # 短日期: 04-25
    m = re.match(r"^(\d{2}-\d{2})$", key)
    if m:
        start = datetime.strptime(f"{now.year}-{m.group(1)}", "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
        return (start, end)

    raise ValueError(
        f"无法解析时间区间: '{interval}'\n"
        "支持格式: 1d/3h/30m/2w, today/yesterday/this_week/this_month, "
        "10:00~14:30, 2026-04-25 10:00~2026-04-27 14:30, "
        "04-25 10:00~04-27 14:30, 2026-04-25~2026-04-27, "
        "04-25~04-27, 2026-04-25, 04-25"
    )


def recall_memories(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    asc: bool = False,
    interval: Optional[str] = None,
    session: Optional[str] = None,
) -> None:
    """显示所有永久记忆"""
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.memory.db_path if config.memory else Path("data/memory.db")
    store = MemoryStore(db_path=db_path, vec_dim=config.memory.vec_dim if config.memory else 1024)

    try:
        count = store.count()
        if count == 0:
            console.print("[yellow]永久记忆为空[/]")
            return

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        query = (
            "SELECT id, session_id, question, answer, tags, source, model, token_count, created_at "
            "FROM memories"
        )
        params: list = []
        conditions: list[str] = []

        if interval:
            try:
                start, end = _parse_interval(interval)
            except ValueError as e:
                console.print(f"[red]{e}[/]")
                return
            conditions.append("created_at >= ? AND created_at <= ?")
            params.extend([start.isoformat(), end.isoformat()])

        if session:
            conditions.append("session_id = ?")
            params.append(session)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        order = "ASC" if asc else "DESC"
        query += f" ORDER BY created_at {order}"

        rows = conn.execute(query, params).fetchall()

        if not rows:
            console.print("[yellow]指定时间范围内无记忆[/]")
            return

        table = Table(title=f"永久记忆 ({len(rows)} 条)")
        table.add_column("#", style="dim", width=4)
        table.add_column("时间", style="cyan", width=19)
        table.add_column("Session", style="blue")
        table.add_column("问题", style="white", max_width=40, no_wrap=False)
        table.add_column("回答", style="dim", max_width=60, no_wrap=False)

        for i, r in enumerate(rows, 1):
            dt = r["created_at"][:19].replace("T", " ") if r["created_at"] else "?"
            question = r["question"].replace("\n", " ")
            answer = r["answer"].replace("\n", " ")
            if len(question) > 40:
                question = question[:37] + "..."
            if len(answer) > 60:
                answer = answer[:57] + "..."
            table.add_row(
                str(i),
                dt,
                r["session_id"] or "-",
                question,
                answer,
            )

        console.print(table)
        conn.close()

    except Exception as e:
        console.print(f"[red]读取记忆失败: {e}[/]")
    finally:
        store.close()


def recall_memory(
    console: Console,
    query: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """语义召回永久记忆"""
    config = load_config(base_dir=base_dir, config_file=config_file)

    if not config.memory:
        console.print("[red]记忆服务未配置[/]")
        return

    try:
        from qd_agents.memory.service import MemoryService
        service = MemoryService(config.memory)
        records = service.recall(query=query)
        service.close()

        if not records:
            console.print(f"[yellow]未找到与「{query}」相关的记忆[/]")
            return

        console.print(f"[bold]找到 {len(records)} 条相关记忆：[/]\n")
        for record in records:
            dt = record.created_at[:19].replace("T", " ") if record.created_at else "?"
            console.print(f"[cyan]{dt}[/]  [blue]{record.session_id}[/]")
            console.print(f"  [bold]Q:[/bold] {record.question}")
            console.print(f"  [dim]A: {record.answer[:200]}{'...' if len(record.answer) > 200 else ''}[/]")
            console.print()

    except Exception as e:
        console.print(f"[red]召回记忆失败: {e}[/]")
