"""write 工具：写入文件。

spec F5：
- 接收 path、content（均必填）
- 文件不存在则新建（含必要的父目录）；存在则整体覆盖
- DANGEROUS：执行前向用户征求确认
- 路径越界 → 结构化错误
- 文件以 UTF-8 编码写入
"""

import asyncio

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import (
    PathOutOfSandboxError,
    ToolError,
)
from mewcode.tools.sandbox import Sandbox

_TIMEOUT = 30.0
_PREVIEW_LINES = 20  # 确认提示展示的内容前 N 行


class WriteTool(Tool):
    """写入文件（不存在则新建，存在则整体覆盖）。

    注：本阶段策略调整——write 默认 SAFE 自动执行（用户在 spec Q3 之后
    放宽了确认要求，只保留 edit 一个 DANGEROUS 工具）。沙盒仍然有效，
    越界路径会被 Sandbox.resolve 拒绝。如需恢复确认，把 danger_level
    改回 DangerLevel.DANGEROUS 即可。
    """

    name = "write"
    description = (
        "写入文件到工作目录。文件不存在则新建（含必要的父目录），存在则"
        "整体覆盖。文件以 UTF-8 编码写入。自动执行不需用户确认。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标文件路径，必须在工作目录内",
            },
            "content": {
                "type": "string",
                "description": "完整文件内容，会整体覆盖现有文件",
            },
        },
        "required": ["path", "content"],
    }
    danger_level = DangerLevel.SAFE

    async def execute(self, params: dict, sandbox: Sandbox) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self._run(params, sandbox), timeout=_TIMEOUT
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, text=f"写入超时（{_TIMEOUT:.0f}s）",
                error_category="超时",
            )
        except ToolError as e:
            return ToolResult(success=False, text=str(e), error_category=e.category)
        except Exception as e:
            return ToolResult(
                success=False,
                text=f"未预期错误：{type(e).__name__}: {e}",
                error_category="未预期错误",
            )

    async def _run(self, params: dict, sandbox: Sandbox) -> ToolResult:
        path_arg = params.get("path")
        content = params.get("content")
        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                success=False,
                text="参数 path 缺失或非字符串",
                error_category="参数错误",
            )
        if not isinstance(content, str):
            return ToolResult(
                success=False,
                text="参数 content 缺失或非字符串",
                error_category="参数错误",
            )

        try:
            resolved = sandbox.resolve(path_arg)
        except PathOutOfSandboxError as e:
            return ToolResult(success=False, text=str(e), error_category=e.category)

        # 自动创建父目录
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        n = len(content)
        return ToolResult(
            success=True,
            text=f"已写入：{path_arg}（{n} 字符）",
        )

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        path = params.get("path", "?")
        content = params.get("content", "")
        n = len(content) if isinstance(content, str) else 0
        return f"path={path}, {n} chars"

    def render_confirm_detail(self, params: dict) -> str:
        """确认提示：路径 + 内容前 20 行预览。"""
        path = params.get("path", "?")
        content = params.get("content", "")
        if not isinstance(content, str):
            return f"即将写入：{path}\n（content 不是字符串，无法预览）"

        lines = content.splitlines()
        total = len(lines)
        head = lines[:_PREVIEW_LINES]
        body = "\n".join(head)
        suffix = ""
        if total > _PREVIEW_LINES:
            suffix = f"\n... 共 {total} 行（仅显示前 {_PREVIEW_LINES} 行）"
        return f"即将写入：{path}（{len(content)} 字符）\n--- 内容预览 ---\n{body}{suffix}"

    def render_result_summary(self, result: ToolResult) -> str:
        if result.success:
            return "已写入"
        return f"失败：{result.error_category or '未知错误'}"
