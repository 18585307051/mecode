"""对话引擎：Agent Loop 多轮 ReAct 循环。

第三阶段（spec F1-F12）：把第二阶段的"一次工具 + 一次答复"单轮闭环
升级为"多轮 ReAct 循环"——模型可以连续多轮调工具，直到自己判断完成。

Loop 结构：
    用户 prompt
        ↓
    ┌── for iteration in 1..50 ──────────────────┐
    │ emit IterationStart(N, 50)                  │
    │ _consume_round → blocks + usage             │
    │   (最后一轮 allow_tools=False，模型只能文本) │
    │ 累计 usage                                  │
    │ blocks is None → Stopped(user_cancel/error) │
    │ append_assistant(blocks)                    │
    │ emit IterationEnd(N)                        │
    │ tool_uses = [b for b in blocks if ToolUse]  │
    │ not tool_uses → Stopped(natural) + return   │
    │ 检查未知工具连续计数 → Stopped(unknown_tools)│
    │ _execute_tool_batch → (results, cancelled)  │
    │   (SAFE 并发 / DANGEROUS 串行)              │
    │ cancelled → Stopped(user_cancel) + return   │
    │ append_tool_results(results)                │
    └─────────────────────────────────────────────┘
    Stopped(max_iterations) + UsageTotal + return

事件流：通过 _emit(renderer, AgentEvent) 推给 Renderer.on_agent_event，
不直接调 Renderer 的语义方法（spec N1 模块边界）。
"""

import asyncio
import signal
import threading

from mewcode.chat.events import (
    AgentEvent,
    IterationEnd,
    IterationStart,
    Stopped,
    ToolBatchStart,
    ToolCall,
    ToolResultEvent,
    UsageTotal,
)
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

# spec N6 / D9：模块级常量，本阶段固定，后续可配置化
MAX_ITERATIONS = 50
MAX_CONCURRENT_SAFE_TOOLS = 8
UNKNOWN_TOOL_THRESHOLD = 2

# 软停止时注入的系统提示（spec N11：中文）
_SOFT_STOP_PROMPT = (
    "你已用完 {max} 轮迭代上限。请基于当前进展，用一段文字总结：\n"
    "1. 已完成的部分\n"
    "2. 未完成的部分\n"
    "3. 建议的后续步骤\n"
    "不要再调用工具。"
)


async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
    registry: ToolRegistry | None = None,
    confirmer: Confirmer | None = None,
    sandbox: Sandbox | None = None,
) -> bool:
    """跑一轮对话（Agent Loop 多轮 ReAct 循环）。

    签名与第二阶段完全一致（spec F9）——REPL 调用方零改动。
    无 tool_use 时退化为一轮直答（行为与第二阶段一致）。

    Returns:
        True  —— Loop 正常完成（含自然停止 / 软停止 / 未知工具停止）
        False —— 用户中断 / Provider 错误（已自处理）
    """
    session.append_user_text(user_input)
    return await _agent_loop(session, renderer, registry, confirmer, sandbox)


async def _agent_loop(
    session: Session,
    renderer: Renderer,
    registry: ToolRegistry | None,
    confirmer: Confirmer | None,
    sandbox: Sandbox | None,
) -> bool:
    """Agent Loop 主循环。"""
    total_in = 0
    total_out = 0
    total_thinking: int | None = None
    unknown_streak = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        _emit(renderer, IterationStart(iteration, MAX_ITERATIONS))

        is_final = iteration == MAX_ITERATIONS
        if is_final:
            # spec F11：注入软停止提示，本轮不带 tools
            session.append_user_text(
                _SOFT_STOP_PROMPT.format(max=MAX_ITERATIONS)
            )

        blocks, usage = await _consume_round(
            session,
            renderer,
            registry,
            allow_tools=not is_final,
        )

        # 累计用量
        if usage:
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            if usage.thinking_tokens is not None:
                total_thinking = (total_thinking or 0) + usage.thinking_tokens

        # blocks is None → 用户取消或流出错
        if blocks is None:
            reason = "user_cancel"  # _consume_round 内部已处理 cancel/error
            _emit(renderer, Stopped(reason, iteration - 1))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration - 1)
            return False

        tool_uses = [b for b in blocks if isinstance(b, ToolUseBlock)]

        # 整条 assistant 消息入历史
        session.append_assistant(blocks)
        _emit(renderer, IterationEnd(iteration))

        # 软停止的最后一轮：无论模型返回什么（文本 or tool_use），都算
        # max_iterations——必须在"自然完成"判断之前，否则软停止轮返回
        # 纯文本会被误判为 natural
        if is_final:
            _emit(renderer, Stopped("max_iterations", iteration))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration)
            return True

        # 停止条件 1: 自然完成
        if not tool_uses:
            _emit(renderer, Stopped("natural", iteration))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration)
            return True

        # 停止条件 4: 连续未知工具
        if registry is not None:
            unknown_count = sum(
                1 for tu in tool_uses if registry.get(tu.name) is None
            )
            if unknown_count > 0:
                unknown_streak += 1
                if unknown_streak >= UNKNOWN_TOOL_THRESHOLD:
                    # 仍执行本轮工具（把未知工具错误反馈给模型），但不再继续
                    results, cancelled = await _execute_tool_batch(
                        session, renderer, registry, confirmer, sandbox, tool_uses
                    )
                    if not cancelled:
                        session.append_tool_results(results)
                    _emit(renderer, Stopped("unknown_tools", iteration))
                    _emit_usage(renderer, total_in, total_out, total_thinking, iteration)
                    return True
            else:
                unknown_streak = 0

        # 执行工具
        results, cancelled = await _execute_tool_batch(
            session, renderer, registry, confirmer, sandbox, tool_uses
        )

        # 停止条件 3: 用户取消
        if cancelled:
            # 回滚当前迭代的 assistant 入历史（避免孤儿 tool_use）
            if session.messages and session.messages[-1].role == "assistant":
                session.messages.pop()
            _emit(renderer, Stopped("user_cancel", iteration))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration - 1)
            return False

        session.append_tool_results(results)

    # 理论不可达：for 循环在第 50 轮 is_final 时已 return
    _emit(renderer, Stopped("max_iterations", MAX_ITERATIONS))
    _emit_usage(renderer, total_in, total_out, total_thinking, MAX_ITERATIONS)
    return True


async def _execute_tool_batch(
    session: Session,
    renderer: Renderer,
    registry: ToolRegistry | None,
    confirmer: Confirmer | None,
    sandbox: Sandbox | None,
    tool_uses: list[ToolUseBlock],
) -> tuple[list[ToolResultBlock], bool]:
    """分批执行 tool_use（spec F3）。

    - SAFE 只读工具：asyncio.gather 并发（上限 MAX_CONCURRENT_SAFE_TOOLS）
    - DANGEROUS 写类工具：串行执行，先 Confirmer.ask
    - 未知工具：生成错误 ToolResultBlock

    Returns:
        (results, cancelled) —— cancelled=True 表示用户中断
    """
    if registry is None or sandbox is None:
        # 兜底：不该发生（registry=None 时模型不应返回 tool_use）
        results = [
            ToolResultBlock(
                tool_use_id=tu.id,
                content="系统未注入 registry/sandbox，无法执行工具",
                is_error=True,
            )
            for tu in tool_uses
        ]
        return results, False

    # 按 danger_level 分批
    safe_tools: list[tuple[int, ToolUseBlock]] = []  # (index, tu)
    dangerous_tools: list[tuple[int, ToolUseBlock]] = []
    unknown_tools: list[tuple[int, ToolUseBlock]] = []
    plan_blocked: list[tuple[int, ToolUseBlock]] = []  # Plan Mode 拒绝的非只读工具

    for idx, tu in enumerate(tool_uses):
        tool = registry.get(tu.name)
        if tool is None:
            unknown_tools.append((idx, tu))
            continue
        # Plan Mode 物理隔离的兜底（spec D7）：执行前就拦截非只读工具
        if session.mode == "plan" and not tool.readonly:
            plan_blocked.append((idx, tu))
            continue
        if tool.danger_level == DangerLevel.SAFE:
            safe_tools.append((idx, tu))
        else:
            dangerous_tools.append((idx, tu))

    _emit(
        renderer,
        ToolBatchStart(
            count=len(tool_uses),
            safe_count=len(safe_tools),
            dangerous_count=len(dangerous_tools),
        ),
    )

    results_by_index: dict[int, ToolResultBlock] = {}

    # ---- 1) 并发执行 SAFE 工具 ----
    safe_to_run = safe_tools[:MAX_CONCURRENT_SAFE_TOOLS]
    safe_overflow = safe_tools[MAX_CONCURRENT_SAFE_TOOLS:]

    async def _run_safe(idx: int, tu: ToolUseBlock) -> tuple[int, ToolResultBlock]:
        tool = registry.get(tu.name)
        assert tool is not None
        _emit(renderer, ToolCall(tool.name, tool.render_call_summary(tu.input)))
        result = await tool.execute(tu.input, sandbox)
        _emit(
            renderer,
            ToolResultEvent(
                tool_use_id=tu.id,
                name=tool.name,
                summary=tool.render_result_summary(result),
                success=result.success,
            ),
        )
        return idx, ToolResultBlock(
            tool_use_id=tu.id,
            content=result.text,
            is_error=not result.success,
        )

    if safe_to_run:
        try:
            completed = await asyncio.gather(
                *[_run_safe(i, tu) for i, tu in safe_to_run]
            )
            for idx, tr in completed:
                results_by_index[idx] = tr
        except asyncio.CancelledError:
            return [], True

    # ---- 2) 串行执行 DANGEROUS + SAFE 溢出 ----
    serial_queue = dangerous_tools + safe_overflow
    for idx, tu in serial_queue:
        try:
            tool = registry.get(tu.name)
            assert tool is not None
            _emit(renderer, ToolCall(tool.name, tool.render_call_summary(tu.input)))

            # DANGEROUS 工具确认
            if tool.danger_level == DangerLevel.DANGEROUS:
                if confirmer is not None:
                    approved = await confirmer.ask(tool.name)
                    if not approved:
                        _emit(
                            renderer,
                            ToolResultEvent(
                                tool_use_id=tu.id,
                                name=tool.name,
                                summary="用户拒绝",
                                success=False,
                            ),
                        )
                        results_by_index[idx] = ToolResultBlock(
                            tool_use_id=tu.id,
                            content="用户拒绝执行此工具",
                            is_error=True,
                        )
                        continue

            result = await tool.execute(tu.input, sandbox)
            _emit(
                renderer,
                ToolResultEvent(
                    tool_use_id=tu.id,
                    name=tool.name,
                    summary=tool.render_result_summary(result),
                    success=result.success,
                ),
            )
            results_by_index[idx] = ToolResultBlock(
                tool_use_id=tu.id,
                content=result.text,
                is_error=not result.success,
            )
        except (KeyboardInterrupt, ConfirmCancelled, asyncio.CancelledError):
            return [], True

    # ---- 3) 未知工具 ----
    for idx, tu in unknown_tools:
        _emit(renderer, ToolCall(tu.name, "(未知工具)"))
        _emit(
            renderer,
            ToolResultEvent(
                tool_use_id=tu.id,
                name=tu.name,
                summary="失败：未知工具",
                success=False,
            ),
        )
        results_by_index[idx] = ToolResultBlock(
            tool_use_id=tu.id,
            content=f"未知工具：{tu.name}",
            is_error=True,
        )

    # ---- 4) Plan Mode 拦截：非只读工具不执行，返回错误 ----
    for idx, tu in plan_blocked:
        _emit(renderer, ToolCall(tu.name, "(Plan Mode 禁止)"))
        _emit(
            renderer,
            ToolResultEvent(
                tool_use_id=tu.id,
                name=tu.name,
                summary="Plan Mode 禁止",
                success=False,
            ),
        )
        results_by_index[idx] = ToolResultBlock(
            tool_use_id=tu.id,
            content=f"Plan Mode 禁止使用非只读工具：{tu.name}",
            is_error=True,
        )

    # 按原始顺序返回
    return [results_by_index[i] for i in range(len(tool_uses))], False


async def _consume_round(
    session: Session,
    renderer: Renderer,
    registry: ToolRegistry | None,
    allow_tools: bool = True,
) -> tuple[list[ContentBlock] | None, Usage | None]:
    """跑一次流式 LLM 请求，边渲染边累积块。

    Args:
        allow_tools: True 时按 session.mode 携带 tools_format；
                     False 时不携带（软停止最后一轮）。

    Returns:
        (blocks, usage) —— 正常结束
        (None, None)    —— 用户中断 / ProviderError（已渲染过）
    """
    # 准备 tools_format
    tools_format: list[dict] | None = None
    if allow_tools and registry is not None:
        tools_format = _get_tools_format(
            registry, session.provider.protocol, session.mode
        )

    stream = session.provider.stream_chat(
        session.messages,
        session.thinking_enabled,
        tools_format=tools_format,
        system=session.system_prompt or None,
    )

    # 累积状态
    blocks: list[ContentBlock] = []
    text_buf = ""
    thinking_buf = ""
    in_text = False
    in_thinking = False
    pending_usage: Usage | None = None
    finished = False

    async def _consume() -> None:
        nonlocal text_buf, thinking_buf, in_text, in_thinking
        nonlocal pending_usage, finished

        async for event in stream:
            if finished:
                continue

            if isinstance(event, ThinkingDelta):
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

            elif isinstance(event, ToolUseInputDelta):
                pass  # UI 不消费

            elif isinstance(event, ToolUseEnd):
                blocks.append(
                    ToolUseBlock(id=event.id, name=event.name, input=event.input)
                )

            elif isinstance(event, Usage):
                pending_usage = event

            elif isinstance(event, Done):
                finished = True

    sub_task = asyncio.create_task(_consume())
    interrupted = False

    def _on_sigint(sig: int, frame) -> None:
        nonlocal interrupted
        interrupted = True
        sub_task.cancel()

    can_install = threading.current_thread() is threading.main_thread()
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

        # 流结束：封入残留 buf
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


def _get_tools_format(
    registry: ToolRegistry,
    protocol: str,
    mode: str,
) -> list[dict] | None:
    """按 session.mode 过滤工具并按协议格式序列化。

    spec F6 / D7：Plan Mode 物理隔离——tools_format 只含 SAFE 工具。
    """
    if mode == "plan":
        tools = [t for t in registry if t.readonly]
    else:
        tools = list(registry)

    if not tools:
        return None

    if protocol == "anthropic":
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters_schema,
            }
            for t in tools
        ]
    else:  # openai
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in tools
        ]


def _emit(renderer: Renderer, ev: AgentEvent) -> None:
    """安全地给 renderer 推 AgentEvent（spec D10）。"""
    try:
        renderer.on_agent_event(ev)
    except Exception:
        pass  # UI 渲染失败不影响 Agent 逻辑


def _emit_usage(
    renderer: Renderer,
    total_in: int,
    total_out: int,
    total_thinking: int | None,
    iterations: int,
) -> None:
    """构造并 emit UsageTotal。"""
    _emit(
        renderer,
        UsageTotal(
            input_tokens=total_in,
            output_tokens=total_out,
            thinking_tokens=total_thinking,
            iterations=iterations,
        ),
    )
