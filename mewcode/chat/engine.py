"""对话引擎：跑一轮对话，把 Provider 的事件流转给 Renderer。

run_turn 是 chat 层的核心入口，负责：
1. 把用户消息追加到历史。
2. 调用当前 Provider 的 stream_chat。
3. 按事件类型分派给 Renderer 的对应方法。
4. 正常结束：累积的 assistant 文本进历史 + 打印用量行。
5. KeyboardInterrupt（Ctrl+C）：abort 渲染，不进历史（spec N5）。
6. ProviderError：abort 渲染，红字打印错误（spec F14）。

Ctrl+C 处理：
    Python 3.11+ 的 asyncio.Runner 默认会把 SIGINT 转为对主任务的
    cancel。本函数在执行期间临时安装自己的 SIGINT handler，把流式
    消费包成 sub-task，Ctrl+C 只 cancel 这个 sub-task；退出时恢复原
    handler，prompt_toolkit 的 Ctrl+C 双击退出语义不受影响。

异步生成器清理：
    退出时（无论正常/中断/出错）显式 await stream.aclose()，避免 GC
    时清理路径上的 noise 通过 stderr 渗漏到终端。
"""

import asyncio
import signal
import threading

from mewcode.chat.session import Session
from mewcode.providers import (
    Done,
    ProviderError,
    TextDelta,
    ThinkingDelta,
    Usage,
)
from mewcode.render import Renderer


async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
) -> bool:
    """跑一轮对话。

    Returns:
        True  —— 正常完成。
        False —— 被中断或出错（已自处理）。
    """
    # 用户消息无条件入历史
    session.append_user(user_input)

    # 累积状态——闭包变量，供 sub-task 读写
    state: dict = {
        "assistant_buf": "",
        "pending_usage": None,
        "in_thinking": False,
        "in_assistant": False,
        "finished": False,
    }

    # 提前持有 stream 引用，便于 finally 显式 aclose
    stream = session.provider.stream_chat(
        session.messages,
        session.thinking_enabled,
    )

    async def _consume() -> None:
        async for event in stream:
            if state["finished"]:
                continue

            if isinstance(event, ThinkingDelta):
                if not state["in_thinking"]:
                    renderer.begin_thinking()
                    state["in_thinking"] = True
                renderer.push_thinking(event.text)

            elif isinstance(event, TextDelta):
                if state["in_thinking"]:
                    renderer.end_thinking()
                    state["in_thinking"] = False
                if not state["in_assistant"]:
                    renderer.begin_assistant()
                    state["in_assistant"] = True
                state["assistant_buf"] += event.text
                renderer.push_text(event.text)

            elif isinstance(event, Usage):
                state["pending_usage"] = event

            elif isinstance(event, Done):
                state["finished"] = True
                # 不 break：让底层 SSE 字节流自然走到 EOF

    sub_task = asyncio.create_task(_consume())
    interrupted = False

    def _on_sigint(sig: int, frame) -> None:
        nonlocal interrupted
        interrupted = True
        sub_task.cancel()

    can_install_handler = (
        threading.current_thread() is threading.main_thread()
    )
    old_handler: signal.Handlers | object = signal.SIG_DFL
    handler_installed = False

    if can_install_handler:
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
                return False
            raise
        except ProviderError as e:
            renderer.abort_streaming()
            renderer.print_error(e.category, str(e))
            return False

        # 正常结束
        if state["in_thinking"]:
            renderer.end_thinking()
        if state["in_assistant"]:
            renderer.end_assistant()
        if state["assistant_buf"]:
            session.append_assistant(state["assistant_buf"])
        if state["pending_usage"] is not None:
            renderer.print_usage(state["pending_usage"])
        return True

    finally:
        # 显式关闭异步生成器，吞掉清理路径上的所有异常（这些 cleanup
        # noise 都是良性的：httpx 在 cancellation 路径中会从底层 socket
        # 抛 ReadError、CancelledError 等，对功能无影响）
        try:
            await stream.aclose()
        except BaseException:
            pass

        # 恢复原 SIGINT handler
        if handler_installed:
            try:
                signal.signal(signal.SIGINT, old_handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass
