"""read 工具：读取工作目录内的文本文件。

spec F4：
- 接收 path（必填）、offset（1-based 起始行，可选）、limit（读取行数，
  可选）。
- 默认读取整个文件；超过 256KB 时按字节截断到 256KB 并标注"已截断"。
- 文件不存在 / 路径越界 / 非 utf-8 编码 → 结构化错误。

行号语义：
- offset=1 表示从第 1 行（即首行）开始（1-based，与编辑器习惯一致）。
- limit=N 表示读 N 行；省略 limit 则读到文件末尾。
- offset 超出文件总行数 → 返回空文本（不报错）。
"""

import asyncio
from pathlib import Path

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import (
    FileDecodeError,
    PathOutOfSandboxError,
    ToolError,
)
from mewcode.tools.sandbox import Sandbox

# spec F4：单文件 256KB 上限（按字节）
_MAX_BYTES = 256 * 1024

# 工具自身超时（spec D17）
_TIMEOUT = 30.0


class ReadTool(Tool):
    """读取工作目录内一个文本文件的内容。"""

    name = "read"
    description = (
        "读取工作目录内一个文本文件的内容。可选 offset（1-based 起始行）"
        "与 limit（读取行数）。文件超过 256KB 时按字节截断并标注。"
        "仅支持 UTF-8 文本文件；不支持二进制或非 UTF-8 编码文件。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对或绝对路径，必须在工作目录内",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（1-based），默认 1",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "读取行数，省略则读到文件末尾",
                "minimum": 1,
            },
        },
        "required": ["path"],
    }
    danger_level = DangerLevel.SAFE

    async def execute(self, params: dict, sandbox: Sandbox) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self._run(params, sandbox), timeout=_TIMEOUT
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                text=f"读取文件超时（{_TIMEOUT:.0f}s）",
                error_category="超时",
            )
        except ToolError as e:
            return ToolResult(
                success=False, text=str(e), error_category=e.category
            )
        except Exception as e:  # 兜底：任何异常都不让冒到 chat 层
            return ToolResult(
                success=False,
                text=f"未预期错误：{type(e).__name__}: {e}",
                error_category="未预期错误",
            )

    async def _run(self, params: dict, sandbox: Sandbox) -> ToolResult:
        # 1) 参数校验
        path_arg = params.get("path")
        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                success=False,
                text="参数 path 缺失或非字符串",
                error_category="参数错误",
            )

        offset = params.get("offset", 1)
        limit = params.get("limit")
        if not isinstance(offset, int) or offset < 1:
            return ToolResult(
                success=False,
                text="offset 必须是 ≥ 1 的整数",
                error_category="参数错误",
            )
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return ToolResult(
                success=False,
                text="limit 必须是 ≥ 1 的整数",
                error_category="参数错误",
            )

        # 2) 路径沙盒校验
        try:
            resolved = sandbox.resolve(path_arg)
        except PathOutOfSandboxError as e:
            return ToolResult(
                success=False, text=str(e), error_category=e.category
            )

        # 3) 存在性 / 类型
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

        # 4) 读取文件（utf-8）
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise FileDecodeError(
                f"文件 {path_arg} 不是 UTF-8 文本：{e}"
            ) from e

        # 5) 应用 offset / limit（1-based）
        lines = raw_text.splitlines(keepends=True)
        total_lines = len(lines)

        # offset > total_lines 时返回空内容（不报错）
        start = offset - 1  # 0-based 切片起点
        if limit is None:
            sliced = lines[start:]
        else:
            sliced = lines[start : start + limit]

        text = "".join(sliced)

        # 6) 256KB 截断（按字节）
        truncated = False
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            # 按字节截断到 _MAX_BYTES 边界；用 errors='ignore' 防止
            # 切到多字节字符中间导致解码失败
            text = encoded[:_MAX_BYTES].decode("utf-8", errors="ignore")
            truncated = True

        # 7) 拼装输出（带元信息）
        actual_lines = len(text.splitlines())
        header_parts = [f"# 文件：{path_arg}"]
        if offset > 1 or limit is not None:
            end_line = offset + actual_lines - 1 if actual_lines else offset
            header_parts.append(f"# 行范围：{offset}–{end_line}（共 {total_lines} 行）")
        else:
            header_parts.append(f"# 共 {total_lines} 行")
        if truncated:
            header_parts.append(f"# [文件超过 {_MAX_BYTES // 1024}KB，已截断]")
        header = "\n".join(header_parts) + "\n"

        return ToolResult(success=True, text=header + text)

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        path = params.get("path", "?")
        offset = params.get("offset")
        limit = params.get("limit")
        suffix = ""
        if offset is not None or limit is not None:
            suffix = f", offset={offset or 1}, limit={limit or 'all'}"
        return f"path={path}{suffix}"

    def render_result_summary(self, result: ToolResult) -> str:
        if not result.success:
            return f"失败：{result.error_category or '未知错误'}"
        # 数 text 中实际有多少行（去掉 header 后）
        lines = result.text.splitlines()
        # header 占前若干行（以 # 开头）；从第一个非 # 行开始算
        body_count = 0
        for ln in lines:
            if not ln.startswith("#"):
                body_count = len(lines) - lines.index(ln)
                break
        return f"读取 {body_count} 行"


# 暴露 _MAX_BYTES 给单测使用
ReadTool._MAX_BYTES = _MAX_BYTES  # type: ignore[attr-defined]
