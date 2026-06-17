"""edit 工具：原文唯一匹配替换。

spec F6：
- 接收 path、old_text、new_text（均必填）
- 读取目标文件 → text.count(old_text) 校验
  - count == 0 → "未找到匹配"
  - count > 1  → "匹配 N 次需更多上下文"
  - count == 1 → text.replace(old, new, 1) 写回
- DANGEROUS：执行前向用户征求确认（展示 difflib.unified_diff）
- 字符精确匹配（spec D13），不做模糊匹配
"""

import asyncio
import difflib

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import (
    EditAmbiguousError,
    EditNotFoundError,
    FileDecodeError,
    PathOutOfSandboxError,
    ToolError,
)
from mewcode.tools.sandbox import Sandbox

_TIMEOUT = 30.0
_OLD_PREVIEW_LEN = 80   # 错误信息中 old_text 的截断长度


class EditTool(Tool):
    """在文件中按字节精确匹配 old_text 并替换为 new_text（要求唯一匹配）。"""

    name = "edit"
    description = (
        "在文件中按字节精确匹配 old_text 并替换为 new_text。要求 old_text "
        "在文件中出现且仅出现一次（否则报错请求更多上下文）。"
        "执行前需要用户确认。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标文件路径，必须在工作目录内",
            },
            "old_text": {
                "type": "string",
                "description": "需替换的原文片段，必须在文件中精确匹配且唯一",
            },
            "new_text": {
                "type": "string",
                "description": "替换后的文本",
            },
        },
        "required": ["path", "old_text", "new_text"],
    }
    danger_level = DangerLevel.DANGEROUS

    async def execute(self, params: dict, sandbox: Sandbox) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self._run(params, sandbox), timeout=_TIMEOUT
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, text=f"编辑超时（{_TIMEOUT:.0f}s）",
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
        old_text = params.get("old_text")
        new_text = params.get("new_text")
        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                success=False, text="参数 path 缺失", error_category="参数错误"
            )
        if not isinstance(old_text, str) or not old_text:
            return ToolResult(
                success=False,
                text="参数 old_text 缺失或为空",
                error_category="参数错误",
            )
        if not isinstance(new_text, str):
            return ToolResult(
                success=False,
                text="参数 new_text 缺失",
                error_category="参数错误",
            )

        try:
            resolved = sandbox.resolve(path_arg)
        except PathOutOfSandboxError as e:
            return ToolResult(success=False, text=str(e), error_category=e.category)

        if not resolved.exists():
            return ToolResult(
                success=False,
                text=f"文件不存在：{path_arg}",
                error_category="文件不存在",
            )
        if not resolved.is_file():
            return ToolResult(
                success=False,
                text=f"不是文件：{path_arg}",
                error_category="不是文件",
            )

        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise FileDecodeError(
                f"文件 {path_arg} 不是 UTF-8 文本：{e}"
            ) from e

        count = text.count(old_text)
        old_preview = old_text[:_OLD_PREVIEW_LEN]
        if len(old_text) > _OLD_PREVIEW_LEN:
            old_preview += "..."

        if count == 0:
            raise EditNotFoundError(
                f"在 {path_arg} 中未找到 old_text：{old_preview!r}"
            )
        if count > 1:
            raise EditAmbiguousError(
                f"old_text 在 {path_arg} 中匹配 {count} 次，"
                f"需提供更多上下文使匹配唯一（前 {_OLD_PREVIEW_LEN} 字符：{old_preview!r}）"
            )

        # count == 1：替换并写回
        new_full = text.replace(old_text, new_text, 1)
        resolved.write_text(new_full, encoding="utf-8")

        return ToolResult(
            success=True,
            text=f"已在 {path_arg} 中替换 1 处。",
        )

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        path = params.get("path", "?")
        return f"path={path}"

    def render_confirm_detail(self, params: dict) -> str:
        """确认提示：展示路径 + difflib.unified_diff 的 diff 文本。

        若文件不存在或读取失败，回退到展示 old/new 的截断片段。
        """
        path = params.get("path", "?")
        old_text = params.get("old_text", "")
        new_text = params.get("new_text", "")

        # 用 splitlines(keepends=True) 让 unified_diff 行为正确
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        # 至少保证以 \n 结尾，否则 diff 显示会奇怪
        if old_lines and not old_lines[-1].endswith("\n"):
            old_lines[-1] += "\n"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        diff_iter = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{path} (原)",
            tofile=f"{path} (新)",
            n=3,
        )
        diff_text = "".join(diff_iter)
        if not diff_text:
            diff_text = "（new_text 与 old_text 完全相同）"
        return f"即将在 {path} 中替换：\n{diff_text}"

    def render_result_summary(self, result: ToolResult) -> str:
        if result.success:
            return "已替换 1 处"
        return f"失败：{result.error_category or '未知错误'}"
