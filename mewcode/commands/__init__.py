"""命令层公共出口。"""

from mewcode.commands.builtin import register_builtins
from mewcode.commands.registry import (
    COMMANDS,
    Command,
    CommandContext,
    CommandResult,
    dispatch,
    register,
)

__all__ = [
    "COMMANDS",
    "Command",
    "CommandContext",
    "CommandResult",
    "dispatch",
    "register",
    "register_builtins",
]
