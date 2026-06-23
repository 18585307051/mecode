"""第九阶段 F14 / F15：natural stop 后调度记忆更新的集成测试。

目标：验证 chat.engine.run_turn 在 natural stop 后会调用
memory_manager.schedule_update，而在 user_cancel / max_iterations /
仍有 tool_use 的轮次不会调用。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mewcode.chat import Session, run_turn
from mewcode.providers import (
    Done,
    Message,
    StreamEvent,
    TextDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)
from mewcode.providers.base import Provider
from mewcode.providers.events import ToolUseInputDelta


class _FakeProvider(Provider):
    """可控制每轮返回事件的最小 Provider 实现。"""

    protocol = "openai"  # type: ignore[assignment]
    model = "test"

    def __init__(self, rounds: list[list[StreamEvent]]):
        self._rounds = rounds
        self._idx = 0

    def stream_chat(self, *args, **kwargs):  # type: ignore[override]
        events = self._rounds[self._idx] if self._idx < len(self._rounds) else []
        self._idx += 1

        async def _gen() -> AsyncIterator[StreamEvent]:
            for e in events:
                yield e

        return _gen()


class _FakeRenderer:
    def __init__(self):
        self.events: list = []
        self.infos: list[str] = []

    # AgentEvent 入口
    def on_agent_event(self, ev):
        self.events.append(ev)

    # streaming hooks
    def begin_assistant(self):
        pass

    def end_assistant(self):
        pass

    def push_text(self, t: str):
        pass

    def begin_thinking(self):
        pass

    def end_thinking(self):
        pass

    def push_thinking(self, t: str):
        pass

    def abort_streaming(self):
        pass

    def print_info(self, msg: str):
        self.infos.append(msg)

    def print_error(self, *a, **k):
        pass


class _FakeMemoryManager:
    def __init__(self):
        self.calls = 0
        self.refresh_calls = 0

    def refresh_system_prompt_if_changed(self, rebuild):
        self.refresh_calls += 1
        return False

    def schedule_update(
        self, session, recent_messages, renderer=None, rebuild_system_prompt=None
    ):
        self.calls += 1
        return None


def _natural_stop_events() -> list[StreamEvent]:
    return [
        TextDelta(text="done"),
        Usage(input_tokens=10, output_tokens=2, thinking_tokens=None),
        Done(),
    ]


def _tool_use_then_text_events() -> tuple[list[StreamEvent], list[StreamEvent]]:
    """两轮：第一轮 tool_use，第二轮纯文本（natural stop）。"""
    round1 = [
        ToolUseStart(id="tu_1", name="read"),
        ToolUseInputDelta(id="tu_1", json_chunk='{"path":"a"}'),
        ToolUseEnd(id="tu_1", name="read", input={"path": "a"}),
        Usage(input_tokens=8, output_tokens=4, thinking_tokens=None),
        Done(),
    ]
    round2 = _natural_stop_events()
    return round1, round2


def _make_session(provider: Provider) -> Session:
    return Session(provider=provider, current_provider_name="fake")


# --- AC14 natural stop 触发 ----------------------------------------------------


def test_natural_stop_schedules_memory_update():
    provider = _FakeProvider([_natural_stop_events()])
    session = _make_session(provider)
    renderer = _FakeRenderer()
    mm = _FakeMemoryManager()

    asyncio.run(
        run_turn(
            session,
            "hello",
            renderer,
            registry=None,
            confirmer=None,
            sandbox=None,
            memory_manager=mm,
        )
    )

    assert mm.calls == 1


# --- AC15 工具调用中间不触发 / 仅最后一轮触发 ---------------------------------


def test_tool_use_round_does_not_schedule_until_natural_stop():
    """两轮：第一轮 tool_use，第二轮 natural stop——只有第二轮调度一次。"""
    from mewcode.tools import ToolRegistry, Sandbox

    # 简单注册一个 echo 工具用于跑通 _execute_tool_batch
    from mewcode.tools.base import (
        DangerLevel,
        Tool,
        ToolResult,
    )

    class _EchoTool(Tool):
        name = "read"
        description = "echo"
        parameters_schema = {"type": "object"}
        readonly = True
        danger_level = DangerLevel.SAFE

        def render_call_summary(self, params):
            return "echo"

        def render_result_summary(self, result):
            return "ok"

        async def execute(self, params, sandbox):
            return ToolResult(success=True, text="ok")

    registry = ToolRegistry()
    registry.register(_EchoTool())
    sandbox = Sandbox(cwd=Path("."))

    r1, r2 = _tool_use_then_text_events()
    provider = _FakeProvider([r1, r2])
    session = _make_session(provider)
    renderer = _FakeRenderer()
    mm = _FakeMemoryManager()

    asyncio.run(
        run_turn(
            session,
            "do read",
            renderer,
            registry=registry,
            confirmer=None,
            sandbox=sandbox,
            memory_manager=mm,
        )
    )

    # 仅 natural stop 那一轮调度过一次
    assert mm.calls == 1


# --- 用户取消不触发 ----------------------------------------------------------


def test_provider_error_does_not_schedule():
    """Provider 异常：不调度记忆更新。"""

    class _ErrProvider(Provider):
        protocol = "openai"  # type: ignore[assignment]
        model = "test"

        def __init__(self):
            pass

        def stream_chat(self, *args, **kwargs):
            from mewcode.providers.errors import NetworkError

            async def _gen():
                raise NetworkError("boom")
                yield  # pragma: no cover

            return _gen()

    session = _make_session(_ErrProvider())
    renderer = _FakeRenderer()
    mm = _FakeMemoryManager()

    asyncio.run(
        run_turn(
            session,
            "ping",
            renderer,
            registry=None,
            confirmer=None,
            sandbox=None,
            memory_manager=mm,
        )
    )

    assert mm.calls == 0
