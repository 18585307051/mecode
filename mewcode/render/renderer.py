"""终端渲染器。

所有写到终端的输出都经此模块的 Renderer 类，封装 rich 的细节，
对外只暴露语义化方法（print_banner / begin_assistant / push_text / ...）。
其他模块禁止直接调用 rich 的 Console.print / Live 等 API（spec N6 模块边界）。

api_key 保护（spec N9）：本模块的所有方法绝不输出 ProviderConfig.api_key，
即使是 print_provider_list 也只展示 name / protocol / model / base_url。

流式渲染策略（重要）：
    本实现**不使用 rich Live**，而是用 sys.stdout 直接 write 纯文本。
    原因：rich Live 在部分 Windows 终端组合（如经典 Windows PowerShell 5.x、
    嵌入式终端）下会绕过 colorama 的 stdout wrapper 直接发 ANSI 控制码，
    导致清行/光标移动转义被显示为字面 `?[2K` `?[1A`，流式输出退化为
    "逐次堆积"且夹杂乱码。
    代价：失去 Markdown 实时渲染（spec AC6 在这些终端上降级为纯文本）；
    保留：流式逐字（AC5）、token 用量、错误红字、命令回显等所有非 Live
    输出。在原生支持 ANSI 的终端（Windows Terminal、PowerShell 7+、
    Linux/macOS）下，简单 ANSI 颜色码仍正常显示。
"""

import sys

from rich.console import Console
from rich.text import Text

from mewcode.config import ProviderConfig
from mewcode.providers.events import Usage


class Renderer:
    """终端渲染器。

    线程模型：单线程异步使用，不需要锁。
    """

    def __init__(self, console: Console) -> None:
        self._console = console

        # 当前流式正文累计文本（仅供 chat.run_turn 在流结束时访问，
        # 实际渲染靠 push_text 即时 write）
        self._buffer: str = ""
        self._thinking_buffer: str = ""
        self._in_assistant: bool = False
        self._in_thinking: bool = False

    # ---------- 启动/横幅 ----------

    def print_banner(
        self, provider_name: str, protocol: str, model: str
    ) -> None:
        """启动时展示当前供应商信息（spec F3）。"""
        self._console.print(
            Text("MewCode v0.1.0", style="bold cyan")
        )
        self._console.print(
            f"当前供应商: [bold]{provider_name}[/]  "
            f"协议: [bold]{protocol}[/]  "
            f"模型: [bold]{model}[/]"
        )

    def print_help_hint(self, commands: list[str]) -> None:
        """启动横幅下方的简短命令提示。"""
        hint = " ".join(f"/{c.lstrip('/')}" for c in commands)
        self._console.print(
            f"[dim]输入 {hint} 等命令；直接输入文字开始对话。[/]"
        )

    # ---------- 普通信息 ----------

    def print_info(self, text: str) -> None:
        """默认色一行信息提示。"""
        self._console.print(text)

    def print_command_list(self, commands: list) -> None:
        """/help 输出。"""
        self._console.print("[bold]可用命令：[/]")
        for cmd in commands:
            name = f"/{cmd.name}"
            if cmd.aliases:
                aliases = ", ".join(f"/{a}" for a in cmd.aliases)
                line = f"  [cyan]{name}[/] (别名: {aliases})  {cmd.description}"
            else:
                line = f"  [cyan]{name}[/]  {cmd.description}"
            self._console.print(line)

    def print_provider_list(
        self,
        providers: dict[str, ProviderConfig],
        current_name: str,
    ) -> None:
        """/providers 输出：列出所有供应商，标记当前生效项。

        重要：只展示 name / protocol / model / base_url；绝不输出 api_key。
        """
        self._console.print("[bold]已配置的供应商：[/]")
        for name, cfg in providers.items():
            mark = "[green]*[/]" if name == current_name else " "
            self._console.print(
                f"  {mark} [cyan]{name}[/]  "
                f"protocol=[bold]{cfg.protocol}[/]  "
                f"model=[bold]{cfg.model}[/]  "
                f"base_url=[dim]{cfg.base_url}[/]"
            )

    def print_unknown_command(self, name: str, available: list[str]) -> None:
        """未知命令提示。"""
        self._console.print(f"[red]未知命令: /{name}[/]")
        self._console.print(
            "[dim]可用命令: " + " ".join(f"/{a}" for a in available) + "[/]"
        )

    # ---------- 错误 ----------

    def print_error(self, category: str, message: str) -> None:
        """红字错误（spec F14、N3）。"""
        self._console.print(
            f"[bold red]\\[{category}][/] [red]{message}[/]"
        )

    # ---------- 流式正文（朴素 stdout write，不用 Live）----------

    def begin_assistant(self) -> None:
        """开始一条 AI 回复的流式渲染。仅做状态标记。"""
        self._buffer = ""
        self._in_assistant = True

    def push_text(self, text: str) -> None:
        """追加正文增量。直接写 sys.stdout，不发任何 ANSI 转义。"""
        if not self._in_assistant:
            self.begin_assistant()
        self._buffer += text
        # 直接 write 原始字符——保证在任何终端下都能正确显示
        sys.stdout.write(text)
        sys.stdout.flush()

    def end_assistant(self) -> None:
        """结束流式渲染，换行分隔。"""
        if self._in_assistant:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._in_assistant = False

    # ---------- 流式思考（朴素 stdout write）----------

    def begin_thinking(self) -> None:
        """开始思考流式渲染。"""
        self._thinking_buffer = ""
        self._in_thinking = True
        # 起始标记走 rich（一次性输出，不进流式 write 路径）
        self._console.print("[dim italic]▎思考中…[/]")

    def push_thinking(self, text: str) -> None:
        """追加思考增量。直接写 sys.stdout。"""
        if not self._in_thinking:
            self.begin_thinking()
        self._thinking_buffer += text
        sys.stdout.write(text)
        sys.stdout.flush()

    def end_thinking(self) -> None:
        """结束思考渲染，加空行分隔。"""
        if self._in_thinking:
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            self._in_thinking = False

    # ---------- 用量行 ----------

    def print_usage(self, usage: Usage) -> None:
        """打印 token 用量（spec F13）。

        thinking_tokens 为 None 时跳过该项。

        实现：用 sys.stdout 直接 write 纯文本，不带 ANSI 样式。
        原因：rich 的 [dim] 标签会展开成复合 SGR 序列（如 `\\x1b[2m`），
        老版 Windows PowerShell 5.x 的 conhost 不解释，会显示为字面
        `?[2m`。spec F13 只要求"灰色文字"是视觉建议，行内容正确显示
        优先于颜色。
        """
        parts = [
            f"↑ {usage.input_tokens} tokens",
            f"↓ {usage.output_tokens} tokens",
        ]
        if usage.thinking_tokens is not None:
            parts.append(f"思考 {usage.thinking_tokens} tokens")
        line = " · ".join(parts)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    # ---------- 异常堆栈（兜底）----------

    def print_exception(self) -> None:
        """打印当前未预期异常的完整堆栈。

        仅用于 main 入口的 last-resort catch-all；业务路径上的异常应由
        chat.run_turn 通过 print_error 红字呈现，不走此方法。
        """
        self._console.print_exception()

    # ---------- 工具调用相关（第二阶段） ----------

    def print_tool_call(self, name: str, summary: str) -> None:
        """工具调用前一行灰字提示（spec F19）。

        格式：'▸ <name>(<summary>)'。沿用朴素 sys.stdout.write，避免
        rich 复合 SGR 在老 conhost 上乱码。
        """
        sys.stdout.write(f"▸ {name}({summary})\n")
        sys.stdout.flush()

    def print_tool_confirm_detail(self, detail: str) -> None:
        """打印 DANGEROUS 工具确认提示中的详细内容（spec F19）。

        detail 由 Tool.render_confirm_detail 提供，可能含多行：
        - WriteTool：路径 + 内容前 N 行
        - EditTool：路径 + difflib.unified_diff
        - RunTool：完整命令字符串
        Confirmer.ask 紧随其后打印 '执行 <name>？[y/N] ' 提示并等待输入。
        """
        sys.stdout.write(detail)
        if not detail.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    def print_tool_result_summary(self, summary: str) -> None:
        """工具执行后一行简略反馈（spec F19）。

        summary 由 Tool.render_result_summary 提供，例如：
        - read：'读取 N 行'
        - glob/search：'匹配 N 项'
        - run：'退出码 N'
        - write/edit：'已写入' / '已替换 1 处'
        - 失败：'失败：<error_category>'
        """
        sys.stdout.write(f"  {summary}\n")
        sys.stdout.flush()

    def print_tool_rejected(self, name: str) -> None:
        """用户拒绝执行 DANGEROUS 工具时的提示（spec F20）。

        chat 层会同时把 '用户拒绝执行此工具' 作为 ToolResultBlock 反馈
        给模型，让模型在 Round 2 据此调整答复。
        """
        sys.stdout.write(f"已拒绝执行 {name}\n")
        sys.stdout.flush()

    def print_usage_combined(
        self, r1: Usage | None, r2: Usage | None
    ) -> None:
        """累计两次请求的 token 用量并打印一行（spec D21 / AC35）。

        Round 1 与 Round 2 的 input/output tokens 各自累加；任一含
        thinking_tokens 时累加显示思考项。完全没有任何 usage 时此方法
        不输出。
        """
        if r1 is None and r2 is None:
            return

        inp = (r1.input_tokens if r1 else 0) + (r2.input_tokens if r2 else 0)
        out = (r1.output_tokens if r1 else 0) + (r2.output_tokens if r2 else 0)
        thk1 = r1.thinking_tokens if r1 else None
        thk2 = r2.thinking_tokens if r2 else None

        parts = [f"↑ {inp} tokens", f"↓ {out} tokens"]
        if thk1 is not None or thk2 is not None:
            thk = (thk1 or 0) + (thk2 or 0)
            parts.append(f"思考 {thk} tokens")

        sys.stdout.write(" · ".join(parts) + "\n")
        sys.stdout.flush()

    # ---------- Agent 事件订阅（第三阶段） ----------

    def on_agent_event(self, ev) -> None:
        """订阅 AgentEvent，按事件类型输出终端文本。

        这是 Renderer 的统一入口——chat.engine 通过 _emit(renderer, ev)
        调用此方法，内部按 isinstance 分派。所有输出沿用朴素
        sys.stdout.write 策略，不用 rich Live。
        """
        # isinstance 分派（避免循环 import，用 duck typing 检查类型名）
        cls_name = type(ev).__name__

        if cls_name == "IterationStart":
            # 用户反馈：进度行噪音过多，本阶段静默
            # 如需恢复显示：取消下面注释
            # sys.stdout.write(
            #     f"── 迭代 {ev.iteration}/{ev.max_iterations} ──\n"
            # )
            # sys.stdout.flush()
            pass

        elif cls_name == "IterationEnd":
            pass  # 静默——仅用于事件流完整性

        elif cls_name == "ToolBatchStart":
            pass  # 静默——避免噪音

        elif cls_name == "ToolCall":
            # 工具自身的 render_call_summary 已经返回完整动词式描述
            # （Read xxx / Wrote xxx / Edited xxx +N-M / Bash xxx /
            # Glob xxx / Search xxx），直接打印，不再用 ▸ name(...) 包装
            summary = ev.summary
            if len(summary) > 120:
                summary = summary[:117] + "..."
            sys.stdout.write(f"{summary}\n")
            sys.stdout.flush()

        elif cls_name == "ToolResultEvent":
            # 用户反馈：调用后的简略反馈行（✓/✗ name: summary）噪音过多——
            # 用户已经能从模型回复中得知工具结果。本阶段静默；如需恢复
            # 显示，去掉下面的 pass 改回原实现。
            # icon = "✓" if ev.success else "✗"
            # sys.stdout.write(f"  {icon} {ev.name}: {ev.summary}\n")
            # sys.stdout.flush()
            pass

        elif cls_name == "Stopped":
            reason_map = {
                "natural": "",
                "max_iterations": "（已达迭代上限）",
                "user_cancel": "（已取消）",
                "unknown_tools": "（连续调用未知工具，已停止）",
                "error": "（流出错）",
            }
            msg = reason_map.get(ev.reason, "")
            if msg:
                sys.stdout.write(f"{msg}\n")
                sys.stdout.flush()

        elif cls_name == "UsageTotal":
            parts = [
                f"↑ {ev.input_tokens} tokens",
                f"↓ {ev.output_tokens} tokens",
            ]
            if ev.thinking_tokens is not None:
                parts.append(f"思考 {ev.thinking_tokens} tokens")
            # 用户反馈：去掉末尾的 "N 轮"
            sys.stdout.write(" · ".join(parts) + "\n")
            sys.stdout.flush()

    # ---------- 中断收尾 ----------

    def abort_streaming(self) -> None:
        """Ctrl+C 中断或 ProviderError 出错时调用。

        因为不再使用 Live，不需要主动关闭流式资源；只重置状态标志并
        追加换行，让后续输出（如错误红字、新一轮 prompt）从干净行开始。
        spec N5：已打印部分保留显示，不打印任何"已中断"标记。
        """
        if self._in_assistant or self._in_thinking:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._in_assistant = False
        self._in_thinking = False
