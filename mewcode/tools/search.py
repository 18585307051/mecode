"""search 工具：用正则在工作目录下递归搜索文件内容。

spec F9：
- 接收 pattern（必填，正则模式）、file_glob（可选，默认 `**/*`）、
  is_literal（可选，默认 false——按字面量匹配则 re.escape）
- 复用 GlobTool 的候选文件列表（含噪声目录排除）
- 单条匹配行截断到 500 字符
- 返回 (file, line, content) 三元组的多行字符串
- 收集 200 条匹配后停止（防爆炸）
- 30s 超时
"""

import asyncio
import re

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import (
    PathOutOfSandboxError,
    ToolError,
)
from mewcode.tools.glob import _search_files
from mewcode.tools.sandbox import Sandbox

_TIMEOUT = 30.0
_MAX_MATCHES = 200
_LINE_TRUNCATE = 500


class SearchTool(Tool):
    """用正则在工作目录下递归搜索文件内容。"""

    name = "search"
    description = (
        "用正则表达式（默认）或字面量字符串在工作目录下递归搜索文件内容。"
        "可选 file_glob 参数限定搜索文件范围（默认 **/*）。返回每条匹配的"
        "(文件路径、行号 1-based、行内容)。单行匹配截断到 500 字符。"
        "最多返回 200 条匹配。自动排除常见噪声目录。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则模式；is_literal=True 时按字面量匹配",
            },
            "file_glob": {
                "type": "string",
                "description": "限定搜索文件的 glob 模式，默认 **/*",
            },
            "is_literal": {
                "type": "boolean",
                "description": "是否按字面量匹配（不解析正则），默认 false",
            },
        },
        "required": ["pattern"],
    }
    danger_level = DangerLevel.SAFE

    async def execute(self, params: dict, sandbox: Sandbox) -> ToolResult:
        try:
            return await asyncio.wait_for(
                self._run(params, sandbox), timeout=_TIMEOUT
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, text=f"search 超时（{_TIMEOUT:.0f}s）",
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
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult(
                success=False,
                text="参数 pattern 缺失或非字符串",
                error_category="参数错误",
            )

        file_glob = params.get("file_glob", "**/*")
        if not isinstance(file_glob, str):
            return ToolResult(
                success=False,
                text="参数 file_glob 必须是字符串",
                error_category="参数错误",
            )

        is_literal = bool(params.get("is_literal", False))

        try:
            regex = re.compile(re.escape(pattern) if is_literal else pattern)
        except re.error as e:
            return ToolResult(
                success=False,
                text=f"无效的正则表达式：{e}",
                error_category="正则错误",
            )

        try:
            files = _search_files(sandbox, file_glob)
        except PathOutOfSandboxError as e:
            return ToolResult(success=False, text=str(e), error_category=e.category)

        matches: list[tuple[str, int, str]] = []
        scanned = 0
        truncated = False

        for f in files:
            scanned += 1
            try:
                # 直接读全文：spec 规定 search 不处理二进制，解码失败跳过该文件
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            rel = f.relative_to(sandbox.cwd).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    truncated_line = (
                        line[:_LINE_TRUNCATE]
                        if len(line) > _LINE_TRUNCATE
                        else line
                    )
                    matches.append((rel, lineno, truncated_line))
                    if len(matches) >= _MAX_MATCHES:
                        truncated = True
                        break
            if truncated:
                break

        n = len(matches)
        if n == 0:
            return ToolResult(
                success=True,
                text=(
                    f"未匹配（搜索 {scanned} 个文件，pattern={pattern!r}, "
                    f"file_glob={file_glob}）"
                ),
            )

        head = f"匹配 {n} 处（搜索 {scanned} 个文件）："
        if truncated:
            head += f"（已截断到前 {_MAX_MATCHES} 条）"
        body_lines = [f"{path}:{ln}: {content}" for path, ln, content in matches]
        text_out = head + "\n" + "\n".join(body_lines)
        return ToolResult(success=True, text=text_out)

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        pattern = str(params.get("pattern", ""))
        file_glob = params.get("file_glob")
        is_literal = params.get("is_literal", False)
        if len(pattern) > 40:
            pattern = pattern[:37] + "..."
        suffix = ""
        if file_glob and file_glob != "**/*":
            suffix += f" in {file_glob}"
        if is_literal:
            suffix += " (literal)"
        return f"Search {pattern}{suffix}"

    def render_result_summary(self, result: ToolResult) -> str:
        if not result.success:
            return f"失败：{result.error_category or '未知错误'}"
        first = result.text.splitlines()[0] if result.text else ""
        if first.startswith("匹配 "):
            return first.rstrip("：")
        if first.startswith("未匹配"):
            return "匹配 0 处"
        return "成功"
