"""对话引擎：跑一轮"一次工具 + 一次答复"的单轮闭环。

spec F12-F15 单轮闭环：

    用户 prompt
        │
        ▼ Round 1（流式）
    模型回复：text + tool_use × N（混合块）
        │
        ▼ 串行执行所有 tool_use（DANGEROUS 工具执行前用户确认）
        │
        ▼ tool_results × N 包成 user 消息回灌历史
        ▼ Round 2（流式）
    模型回复：最终文本答复
        │
        ▼ 若 R2 仍含 tool_use → 硬停（剥离 tool_use 块 + 灰字提示）
        │
        ▼ 用量行：R1 + R2 累计
        │
        ▼ 回到主输入提示符

退化路径：R1 不含 tool_use → 直接 print_usage(R1) + return（与第一阶段一致）。

中断处理（spec N5 / N2）：
- R1 / R2 流式中按 Ctrl+C：abort_streaming + return False
- 工具执行中按 Ctrl+C：当前工具 cancel + 跳过剩余 tool_use + 回滚 R1
  assistant 入历史（避免协议层"孤儿 tool_use"）+ return False
- 确认提示中按 Ctrl+C：Confirmer 抛 ConfirmCancelled，同上回滚处理

异步生成器清理：所有路径 finally 显式 aclose 流（继承第一阶段防线）。
"""

import asyncio
import signal
import threading

from mewcode.chat.session import Session
from mewcode.providers import (
    ContentBlock,
    Done,
    ProviderError,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
)
from mewcode.render import Renderer
from mewcode.tools import (
    ConfirmCancelled,
    Confirmer,
    DangerLevel,
    Sandbox,
    ToolRegistry,
)


async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
    registry: ToolRegistry | None = None,
    confirmer: Confirmer | None = None,
    sandbox: Sandbox | None = None,
) -> bool:
    """跑一轮对话（含 R1 + 工具 + R2 单轮闭环）。

    Args:
        session:    会话状态。
        user_input: 本轮用户文本。
        renderer:   终端渲染器。
        registry:   工具注册中心。None 时不携带 tools，行为退化为第一阶段。
        confirmer:  用户确认器。registry 非 None 但有 DANGEROUS 工具被
                    模型调用时必须提供；否则抛 RuntimeError。
        sandbox:    工作目录沙盒。registry 非 None 时必须提供。

    Returns:
        True  —— 正常完成（含 R1 直答 / 完整闭环 / R2 含 tool_use 硬停）
        False —— 用户中断 / Provider 错误（已自处理）
    """
    session.append_user_text(user_input)

    # ---------- Round 1 ----------
    r1_blocks, r1_usage = await _consume_round(
        session, renderer, registry, register_sigint=True
    )
    if r1_blocks is None:
        return False

    # 提取 tool_use 列表
    tool_uses = [b for b in r1_blocks if isinstance(b, ToolUseBlock)]

    # 整条 R1 assistant 消息入历史（含 text/thinking/tool_use 全部块）
    session.append_assistant(r1_blocks)

    # 无工具调用 → 退化为第一阶段
    if not tool_uses:
        if r1_usage:
            renderer.print_usage(r1_usage)
        return True

    # 有工具调用：必须有 registry / sandbox / confirmer
    if registry is None or sandbox is None:
        # 这种情况不应发生（registry=None 时 stream_chat 不带 tools，
        # 模型理论上不应返回 tool_use）。兜底：直接退化
        renderer.print_info("（收到工具调用但未注入 registry/sandbox，已忽略）")
        if r1_usage:
            renderer.print_usage(r1_usage)
        return True

    # ---------- 工具执行（串行 + DANGEROUS 工具确认）----------
    tool_results: list[ToolResultBlock] = []
    try:
        for tu in tool_uses:
            tool = registry.get(tu.name)
            if tool is None:
                # 模型调用了未注册的工具（理论不应发生，因 tools_format 取自 registry）
                renderer.print_tool_call(tu.name, "(未知工具)")
                renderer.print_tool_result_summary("失败：未知工具")
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=tu.id,
                        content=f"未知工具：{tu.name}",
                        is_error=True,
                    )
                )
                continue

            # 调用前提示（spec F19）
            renderer.print_tool_call(tool.name, tool.render_call_summary(tu.input))

            # DANGEROUS 工具确认
            if tool.danger_level == DangerLevel.DANGEROUS:
                if confirmer is None:
                    # 没注入 Confirmer 但模型调用了 DANGEROUS 工具——
                    # 安全起见拒绝执行并反馈给模型
                    renderer.print_tool_rejected(tool.name)
                    tool_results.append(
                        ToolResultBlock(
                            tool_use_id=tu.id,
                            content="用户拒绝执行此工具（系统未配置确认器）",
                            is_error=True,
                        )
                    )
                    continue

                # 打印详细参数预览
                detail = tool.render_confirm_detail(tu.input)
                renderer.print_tool_confirm_detail(detail)

                # 等待用户 y/N
                approved = await confirmer.ask(tool.name)
                if not approved:
                    renderer.print_tool_rejected(tool.name)
                    tool_results.append(
                        ToolResultBlock(
                            tool_use_id=tu.id,
                            content="用户拒绝执行此工具",
                            is_error=True,
                        )
                    )
                    continue

            # 执行工具
            result = await tool.execute(tu.input, sandbox)
            renderer.print_tool_result_summary(
                tool.render_result_summary(result)
            )
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=tu.id,
                    content=result.text,
                    is_error=not result.success,
                )
            )

    except (KeyboardInterrupt, ConfirmCancelled, asyncio.CancelledError):
        # 整个 turn 取消：回滚 R1 assistant 入历史，避免协议层孤儿 tool_use
        renderer.print_info("（已取消本轮）")
        # session.messages 末尾是刚 append 的 R1 assistant，pop 掉
        if session.messages and session.messages[-1].role == "assistant":
            session.messages.pop()
        return False

    # 把 tool_results 入历史
    session.append_tool_results(tool_results)

    # ---------- Round 2 ----------
    r2_blocks, r2_usage = await _consume_round(
        session, renderer, registry, register_sigint=True
    )
    if r2_blocks is None:
        return False

    # F15 硬停：剥离 R2 中的 tool_use 块
    cleaned_blocks: list[ContentBlock] = []
    leftover_tool_names: list[str] = []
    for b in r2_blocks:
        if isinstance(b, ToolUseBlock):
            leftover_tool_names.append(b.name)
        else:
            cleaned_blocks.append(b)

    # R2 整理后的块入历史（不含 tool_use）
    if cleaned_blocks:
        session.append_assistant(cleaned_blocks)

    if leftover_tool_names:
        names = "、".join(leftover_tool_names)
        renderer.print_info(
            f"模型在最终答复中还想调用工具 {names}（共 {len(leftover_tool_names)} 个），"
            "本阶段不再继续；下一轮可以追问。"
        )

    # 累计用量行（spec D21 / AC35）
    renderer.print_usage_combined(r1_usage, r2_usage)
    return True


async def _consume_round(
    session: Session,
    renderer: Renderer,
    registry: ToolRegistry | None,
    register_sigint: bool = True,
) -> tuple[list[ContentBlock] | None, Usage | None]:
    """跑一次流式请求，按事件类型边渲染边累积块。

    Returns:
        (blocks, usage) —— 正常结束。blocks 含 TextBlock / ThinkingBlock /
                            ToolUseBlock 任意混合。
        (None, None)    —— 用户中断 / ProviderError（已渲染过）

    内部职责：
    - 装 SIGINT handler 把 Ctrl+C 转为 sub-task cancel
    - 按事件类型分派与累积
    - 流结束后把累积的文本/思考块封装入 blocks
    - finally 显式 aclose stream
    """
    # 准备 tools_format（按当前 protocol 选格式）
    tools_format: list[dict] | None = None
    if registry is not None:
        if session.provider.protocol == "anthropic":
            tools_format = registry.to_anthropic_format()
        elif session.provider.protocol == "openai":
            tools_format = registry.to_openai_format()
        if tools_format == []:
            tools_format = None

    stream = session.provider.stream_chat(
        session.messages,
        session.thinking_enabled,
        tools_format=tools_format,
        system=session.system_prompt or None,
    )

    # 累积状态
    blocks: list[ContentBlock] = []
    text_buf = ""              # 当前 text 块累计
    thinking_buf = ""          # 当前 thinking 块累计
    in_text = False
    in_thinking = False
    pending_usage: Usage | None = None
    finished = False

    # tool_use 累积：id → {"name": str}（input 已由 Provider 在 ToolUseEnd
    # 时整体送出，这里只需要把 ToolUseBlock 放入 blocks）

    def _flush_text() -> None:
        nonlocal text_buf, in_text
        if in_text and text_buf:
            blocks.append(TextBlock(text=text_buf))
        text_buf = ""
        in_text = False
        # UI 收尾
        renderer.end_assistant() if hasattr(renderer, "end_assistant") else None

    def _flush_thinking() -> None:
        nonlocal thinking_buf, in_thinking
        if in_thinking and thinking_buf:
            blocks.append(ThinkingBlock(text=thinking_buf))
        thinking_buf = ""
        in_thinking = False
        renderer.end_thinking() if hasattr(renderer, "end_thinking") else None

    async def _consume() -> None:
        nonlocal text_buf, thinking_buf, in_text, in_thinking
        nonlocal pending_usage, finished

        async for event in stream:
            if finished:
                continue

            if isinstance(event, ThinkingDelta):
                # 切换到 thinking 模式
                if in_text:
                    blocks.append(TextBlock(text=text_buf))
                    text_buf = ""
                    in_text = False
                    renderer.end_assistant()
                if not in_thinking:
                    renderer.begin_thinking()
                    in_thinking = True
                thinking_buf += event.text
                renderer.push_thinking(event.text)

            elif isinstance(event, TextDelta):
                if in_thinking:
                    blocks.append(ThinkingBlock(text=thinking_buf))
                    thinking_buf = ""
                    in_thinking = False
                    renderer.end_thinking()
                if not in_text:
                    renderer.begin_assistant()
                    in_text = True
                text_buf += event.text
                renderer.push_text(event.text)

            elif isinstance(event, ToolUseStart):
                # 工具调用开始：先收尾当前 text/thinking 块
                if in_text:
                    blocks.append(TextBlock(text=text_buf))
                    text_buf = ""
                    in_text = False
                    renderer.end_assistant()
                if in_thinking:
                    blocks.append(ThinkingBlock(text=thinking_buf))
                    thinking_buf = ""
                    in_thinking = False
                    renderer.end_thinking()
                # ToolUseStart 本身不立即产生 ToolUseBlock；等 ToolUseEnd

            elif isinstance(event, ToolUseInputDelta):
                # UI 不消费（D5）；input 已由 Provider 在 End 时整体提供
                pass

            elif isinstance(event, ToolUseEnd):
                blocks.append(
                    ToolUseBlock(id=event.id, name=event.name, input=event.input)
                )

            elif isinstance(event, Usage):
                pending_usage = event

            elif isinstance(event, Done):
                finished = True
                # 不 break：让底层 SSE 流自然走到 EOF（防 GeneratorExit）

    sub_task = asyncio.create_task(_consume())
    interrupted = False

    def _on_sigint(sig: int, frame) -> None:
        nonlocal interrupted
        interrupted = True
        sub_task.cancel()

    can_install = (
        register_sigint
        and threading.current_thread() is threading.main_thread()
    )
    old_handler: signal.Handlers | object = signal.SIG_DFL
    handler_installed = False
    if can_install:
        try:
            old_handler = signal.signal(signal.SIGINT, _on_sigint)
            handler_installed = True
        except (ValueError, OSError):
            pass

    try:
        try:
            await sub_task
        except asyncio.CancelledError:
            if interrupted:
                renderer.abort_streaming()
                return None, None
            raise
        except ProviderError as e:
            renderer.abort_streaming()
            renderer.print_error(e.category, str(e))
            return None, None

        # 流结束：把残留的 text/thinking buf 封入 blocks
        if in_text and text_buf:
            blocks.append(TextBlock(text=text_buf))
            renderer.end_assistant()
        elif in_text:
            renderer.end_assistant()
        if in_thinking and thinking_buf:
            blocks.append(ThinkingBlock(text=thinking_buf))
            renderer.end_thinking()
        elif in_thinking:
            renderer.end_thinking()

        return blocks, pending_usage

    finally:
        try:
            await stream.aclose()
        except BaseException:
            pass
        if handler_installed:
            try:
                signal.signal(signal.SIGINT, old_handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass
