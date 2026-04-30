qd-agents tools init的逻辑有问题：

1. 重新安装所有内置的和默认的工具：除唯一的bash工具外；
2. 它在重新安装cli,http,mcp,skill时都要调用cli add, http add ,mcp add, skill add安装；不是像现在这样。
3. 当使用--keep时保留用户user增加的工具，否则删除用户定义的工具。