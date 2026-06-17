"""tools 模块公共出口。

对外暴露：
- Tool 抽象、ToolResult、DangerLevel
- 8 个 ToolError 子类
- Sandbox（路径校验）
- Confirmer（用户确认）+ ConfirmCancelled
- ToolRegistry + register_builtins（T16 后启用）

具体工具类（ReadTool / WriteTool / ...）不在顶层暴露——上层只通过
ToolRegistry 与之交互。
"""

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.confirmer import ConfirmCancelled, Confirmer
from mewcode.tools.errors import (
    CommandTimeoutError,
    EditAmbiguousError,
    EditNotFoundError,
    FileDecodeError,
    FileTooLargeError,
    PathOutOfSandboxError,
    ToolError,
    ToolInterruptedError,
)
from mewcode.tools.registry import ToolRegistry, register_builtins
from mewcode.tools.sandbox import Sandbox

__all__ = [
    "CommandTimeoutError",
    "ConfirmCancelled",
    "Confirmer",
    "DangerLevel",
    "EditAmbiguousError",
    "EditNotFoundError",
    "FileDecodeError",
    "FileTooLargeError",
    "PathOutOfSandboxError",
    "Sandbox",
    "Tool",
    "ToolError",
    "ToolInterruptedError",
    "ToolRegistry",
    "ToolResult",
    "register_builtins",
]
