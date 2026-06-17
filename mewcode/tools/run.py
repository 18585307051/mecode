"""run 工具：在工作目录下执行 shell 命令。

spec F7：
- 接收 command（必填，字符串）
- 在 sandbox.cwd 下用系统 shell 执行
- 60 秒超时；超时则强制 kill 子进程
- 返回 stdout、stderr、退出码（拼接成单文本块）
- stdout+stderr 总长度超过 32KB 时按字节截断
- DANGEROUS：执行前向用户征求确认（展示完整命令）
"""

import asyncio

from mewcode.tools.base import DangerLevel, Tool, ToolResult
from mewcode.tools.errors import CommandTimeoutError, ToolError
from mewcode.tools.sandbox import Sandbox

# spec D17：run 工具 60s 超时（其他工具 30s）
DEFAULT_TIMEOUT = 60.0
# spec D12：stdout+stderr 总输出 32KB 截断
_MAX_OUTPUT_BYTES = 32 * 1024

# 命令字符串在 render_call_summary 中的最大显示长度
_CMD_SUMMARY_MAX = 60


class RunTool(Tool):
    """在工作目录下用系统 shell 执行一条命令。

    注：本阶段策略调整——run 默认 SAFE 自动执行（用户在 spec Q3 之后
    放宽了确认要求，只保留 edit 一个 DANGEROUS 工具）。沙盒以 sandbox.cwd
    限制子进程工作目录；超时 60s 兜底。如需恢复确认，把 danger_level
    改回 DangerLevel.DANGEROUS 即可。
    """

    name = "run"
    description = (
        "在工作目录下用系统 shell 执行一条命令，60 秒超时。返回 stdout、"
        "stderr 与退出码。stdout+stderr 总输出超过 32KB 时按字节截断。"
        "自动执行不需用户确认。"
        "优先使用 read / glob / search 等专用工具读取信息，"
        "而非通过 run 调用 cat / dir / grep 等命令。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "完整命令字符串（会通过系统 shell 执行）",
            },
        },
        "required": ["command"],
    }
    danger_level = DangerLevel.SAFE
    readonly = False  # 有副作用：执行命令

    # 允许测试通过实例属性 monkey-patch 覆盖超时（不改环境变量）
    timeout: float = DEFAULT_TIMEOUT

    async def execute(self, params: dict, sandbox: Sandbox) -> ToolResult:
        # run 不用 wait_for 套 _run，超时由 _run 内部对 communicate 控制
        try:
            return await self._run(params, sandbox)
        except ToolError as e:
            return ToolResult(success=False, text=str(e), error_category=e.category)
        except Exception as e:
            return ToolResult(
                success=False,
                text=f"未预期错误：{type(e).__name__}: {e}",
                error_category="未预期错误",
            )

    async def _run(self, params: dict, sandbox: Sandbox) -> ToolResult:
        command = params.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(
                success=False,
                text="参数 command 缺失或为空",
                error_category="参数错误",
            )

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(sandbox.cwd),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError as e:
            # 超时：强制 kill 子进程
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            raise CommandTimeoutError(
                f"命令超时（{self.timeout:.0f}s）：{command}"
            ) from e

        exit_code = process.returncode if process.returncode is not None else -1
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # 拼装文本
        text = (
            f"$ {command}\n"
            f"退出码：{exit_code}\n"
            f"--- stdout ---\n{stdout}"
        )
        if stderr.strip():
            text += f"\n--- stderr ---\n{stderr}"

        # 32KB 截断
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_OUTPUT_BYTES:
            text = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
            text += f"\n... [输出超过 {_MAX_OUTPUT_BYTES // 1024}KB，已截断]"

        # 命令成功完成 ≠ exit_code == 0；spec D 选择 "命令失败" 语义：
        # exit_code != 0 时 success=False，让模型据此调整
        success = exit_code == 0
        error_category = None if success else "非零退出"

        return ToolResult(
            success=success, text=text, error_category=error_category
        )

    # ---------- UI 渲染 ----------

    def render_call_summary(self, params: dict) -> str:
        cmd = str(params.get("command", ""))
        if len(cmd) > _CMD_SUMMARY_MAX:
            cmd = cmd[: _CMD_SUMMARY_MAX - 3] + "..."
        return f"Bash {cmd}"

    def render_confirm_detail(self, params: dict) -> str:
        cmd = params.get("command", "?")
        return f"即将执行命令：\n  {cmd}"

    def render_result_summary(self, result: ToolResult) -> str:
        # 从 text 中抽 "退出码：N"
        if result.error_category == "超时":
            return "失败：超时"
        for line in result.text.splitlines():
            if line.startswith("退出码：") or line.startswith("退出码:"):
                return line.strip()
        if result.success:
            return "退出码 0"
        return f"失败：{result.error_category or '未知错误'}"
