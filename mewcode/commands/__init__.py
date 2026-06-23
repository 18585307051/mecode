"""命令层公共出口。"""

from mewcode.commands.builtin import register_builtins
from mewcode.commands.registry import (
    COMMANDS,
    Command,
    CommandContext,
    CommandRegistrationError,
    CommandResult,
    CommandType,
    commands_by_type,
    dispatch,
    register,
    unregister_all,
    visible_command_names,
)

__all__ = [
    "COMMANDS",
    "Command",
    "CommandContext",
    "CommandRegistrationError",
    "CommandResult",
    "CommandType",
    "commands_by_type",
    "dispatch",
    "register",
    "register_builtins",
    "unregister_all",
    "visible_command_names",
]
