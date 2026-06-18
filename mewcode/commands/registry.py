"""命令注册表与分发。

数据结构：
- Command         —— 命令定义（name + aliases + description + handler）
- CommandContext  —— 传给 handler 的上下文（session、app_config、args、renderer）
- CommandResult   —— handler 返回值，目前只有 should_exit 一个字段

工作流：
1. 启动时 builtin.register_builtins() 把所有内置命令写入 COMMANDS 字典。
2. REPL 主循环对每行输入调 dispatch(line, ctx)。
3. dispatch：
   - 不以 / 开头 → 返回 None（落到对话分支）
   - 命令存在 → 执行 handler → 返回 CommandResult
   - 命令未知 → 调 renderer.print_unknown_command → 返回 CommandResult()
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.chat import Session
    from mewcode.config import AppConfig
    from mewcode.render import Renderer


@dataclass(frozen=True)
class Command:
    """单个命令的定义。"""

    name: str                                # 不含 / 前缀
    aliases: tuple[str, ...]                 # 别名列表
    description: str                         # /help 中展示
    handler: Callable[["CommandContext"], Awaitable["CommandResult"]]


@dataclass
class CommandContext:
    """命令执行时收到的上下文。"""

    session: "Session"
    app_config: "AppConfig"
    args: list[str] = field(default_factory=list)
    renderer: "Renderer" = field(default=None)  # type: ignore[assignment]
    # 第五阶段：权限策略实例（可选）。/permissions 命令族会用到。
    policy: object = field(default=None)  # PermissionPolicy | None


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果。"""

    should_exit: bool = False


# 全局命令表：name 或 alias → Command。
# 内置命令通过 register_builtins() 写入；测试中可手动操作（注意隔离）。
COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> None:
    """把命令及其所有别名注册到全局表。重复注册同名命令会覆盖。"""
    COMMANDS[cmd.name] = cmd
    for alias in cmd.aliases:
        COMMANDS[alias] = cmd


async def dispatch(line: str, ctx: CommandContext) -> CommandResult | None:
    """把一行输入按命令分发。

    Returns:
        None              —— 不是命令（行不以 / 开头），调用方应当对话处理。
        CommandResult     —— 命令已执行（含未知命令的情形），按 should_exit
            决定是否退出 REPL。
    """
    if not line.startswith("/"):
        return None

    parts = line[1:].split()
    available = sorted({c.name for c in COMMANDS.values()})

    if not parts:
        # 仅输入 "/" → 视作未知命令
        ctx.renderer.print_unknown_command("", available)
        return CommandResult()

    name, *args = parts
    cmd = COMMANDS.get(name)
    if cmd is None:
        ctx.renderer.print_unknown_command(name, available)
        return CommandResult()

    ctx.args = args
    return await cmd.handler(ctx)
