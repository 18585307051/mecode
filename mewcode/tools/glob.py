"""glob 工具：用 glob 模式在工作目录下递归查找文件。

spec F8：
- 接收 pattern（必填，glob 模式如 `**/*.py`）
- 在 sandbox.cwd 下递归匹配文件路径
- 自动排除噪声目录（_noise.NOISE_DIRS）
- 返回相对工作目录的路径列表，按字母序排列
- 模式不能以 `/` 开头、不能含 `..`（视为路径越界）
- 30s 超时
- 结果超过 1000 项时截断
"""

import asyncio
from pathlib import Path

from mewcode.tools._noise import has_noise_part
from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import (
    PathOutOfSandboxError,
    ToolError,
)
from mewcode.tools.sandbox import Sandbox

_TIMEOUT = 30.0
_MAX_RESULTS = 1000


def _validate_pattern(pattern: str) -> None:
    """校验 pattern 不越界。

    Raises:
        PathOutOfSandboxError: pattern 以 / 开头或含 ..
    """
    if pattern.startswith("/") or pattern.startswith("\\"):
        raise PathOutOfSandboxError(
            f"glob 模式不能以 / 开头：{pattern}"
        )
    if ".." in pattern.replace("\\", "/").split("/"):
        raise PathOutOfSandboxError(
            f"glob 模式不能含 .. 上溯：{pattern}"
        )


def _search_files(sandbox: Sandbox, pattern: str) -> list[Path]:
    """内部 helper：rglob + 噪声目录过滤；按字母序返回。

    供 SearchTool 复用以避免重复逻辑。
    """
    _validate_pattern(pattern)
    base = sandbox.cwd
    candidates: list[Path] = []
    # pathlib 不直接支持 ** 在某些场景下的递归——用 rglob(pattern) 时，
    # 若 pattern 不含 **，rglob 仍按递归子目录的方式去匹配每个目录下的
    # 模式；这里直接信任 pathlib 行为
    for p in base.rglob(pattern):
        if not p.is_file():
            continue
        if has_noise_part(p, base):
            continue
        candidates.append(p)
    candidates.sort()
    return candidates


class GlobTool(Tool):
    """用 glob 模式（如 `**/*.py`）在工作目录下递归查找文件。"""

    name = "glob"
    description = (
        "用 glob 模式（如 `**/*.py`、`src/*.md`）在工作目录下递归查找文件。"
        "自动排除常见噪声目录（.git / __pycache__ / node_modules / .venv 等）。"
        "返回相对工作目录的路径列表，按字母序排列。最多返回 1000 项。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式，例如 `**/*.py`",
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
                success=False, text=f"glob 超时（{_TIMEOUT:.0f}s）",
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

        files = _search_files(sandbox, pattern)
        truncated = False
        if len(files) > _MAX_RESULTS:
            files = files[:_MAX_RESULTS]
            truncated = True

        rel_paths = [
            p.relative_to(sandbox.cwd).as_posix() for p in files
        ]

        n = len(rel_paths)
        if n == 0:
            return ToolResult(
                success=True, text=f"未匹配到任何文件（pattern={pattern}）"
            )

        head = f"匹配 {n} 项："
        if truncated:
            head += f"（已截断到前 {_MAX_RESULTS} 项）"
        text = head + "\n" + "\n".join(rel_paths)
        return ToolResult(success=True, text=text)

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        return f"Glob {params.get('pattern', '?')}"

    def render_result_summary(self, result: ToolResult) -> str:
        if not result.success:
            return f"失败：{result.error_category or '未知错误'}"
        # 从 text 第一行 "匹配 N 项" 中抽
        first = result.text.splitlines()[0] if result.text else ""
        if first.startswith("匹配 "):
            return first.rstrip("：")
        return "成功"
