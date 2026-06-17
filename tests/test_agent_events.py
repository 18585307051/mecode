"""AgentEvent 单元测试。

覆盖 spec F4：
- 7 种 AgentEvent 可构造且 frozen
- Stopped reason 取值覆盖
- AgentEvent 联合 isinstance
- Renderer.on_agent_event 不抛异常
"""

import pytest
from dataclasses import FrozenInstanceError

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
from mewcode.render import Renderer
from rich.console import Console


def test_所有AgentEvent可构造() -> None:
    """7 种 AgentEvent 都能正常构造。"""
    assert IterationStart(1, 50).iteration == 1
    assert IterationEnd(3).iteration == 3
    assert ToolBatchStart(5, 3, 2).count == 5
    assert ToolCall("read", "path=a").name == "read"
    assert ToolResultEvent("t1", "read", "5 行", True).success is True
    assert Stopped("natural", 2).reason == "natural"
    assert UsageTotal(100, 50, None, 2).iterations == 2


def test_AgentEvent_frozen() -> None:
    """所有 AgentEvent 都是 frozen dataclass。"""
    ev = IterationStart(1, 50)
    with pytest.raises(FrozenInstanceError):
        ev.iteration = 2  # type: ignore[misc]

    stopped = Stopped("natural", 1)
    with pytest.raises(FrozenInstanceError):
        stopped.reason = "error"  # type: ignore[misc]


def test_Stopped_reason取值覆盖() -> None:
    """5 种 reason 都能构造。"""
    for reason in ("natural", "max_iterations", "user_cancel", "unknown_tools", "error"):
        s = Stopped(reason, 5)
        assert s.reason == reason


def test_AgentEvent_联合isinstance() -> None:
    """每个子类型实例 isinstance(ev, AgentEvent) 为 True。"""
    events: list[AgentEvent] = [
        IterationStart(1, 50),
        IterationEnd(1),
        ToolBatchStart(1, 1, 0),
        ToolCall("read", "x"),
        ToolResultEvent("t1", "read", "ok", True),
        Stopped("natural", 1),
        UsageTotal(0, 0, None, 1),
    ]
    for ev in events:
        assert isinstance(ev, AgentEvent)


def test_Renderer_on_agent_event不抛异常() -> None:
    """Renderer 对每种事件类型调 on_agent_event 都不抛异常。"""
    r = Renderer(Console())
    events: list[AgentEvent] = [
        IterationStart(1, 50),
        IterationEnd(1),
        ToolBatchStart(1, 1, 0),
        ToolCall("read", "path=very_long_summary" * 10),  # 测截断
        ToolResultEvent("t1", "read", "读取 5 行", True),
        ToolResultEvent("t2", "edit", "未找到匹配", False),
        Stopped("natural", 2),
        Stopped("max_iterations", 50),
        Stopped("user_cancel", 3),
        Stopped("unknown_tools", 2),
        Stopped("error", 1),
        UsageTotal(100, 50, None, 2),
        UsageTotal(100, 50, 30, 3),
    ]
    for ev in events:
        r.on_agent_event(ev)  # 不抛异常即可
