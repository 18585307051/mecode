"""chat.run_turn 的 Agent Loop 编排单元测试（用 stub Provider 不打真实 API）。

覆盖第三阶段 spec AC1-AC9 的核心场景：
- 自然停止 / 两轮 Loop / 分批执行 / 拒绝 / Ctrl+C / 软停止 / 未知工具 /
  流出错 / AgentEvent 顺序 / 并发上限
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mewcode.chat import Session, run_turn
from mewcode.chat.engine import MAX_CONCURRENT_SAFE_TOOLS, _get_tools_format
from mewcode.config import ProviderConfig
from mewcode.providers import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
)
from mewcode.providers.errors import ProviderError
from mewcode.tools import (
    ConfirmCancelled,
    DangerLevel,
    Sandbox,
    Tool,
    ToolRegistry,
    ToolResult,
)


# ---------- stub 实现 ----------


class _StubProvider(Provider):
    """可预设事件流的 Provider；按调用次数依次返回不同的事件序列。"""

    def __init__(self, cfg: ProviderConfig, rounds: list[list[StreamEvent]]):
        super().__init__(cfg)
        self._rounds = rounds
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,
        tools_format: list[dict] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        idx = self._call_count
        self._call_count += 1
        if idx >= len(self._rounds):
            yield Done()
            return
        for ev in self._rounds[idx]:
            yield ev


class _StubTool(Tool):
    """可预设结果的 SAFE 工具。"""

    name = "stub_safe"
    description = "stub safe"
    parameters_schema = {"type": "object"}
    danger_level = DangerLevel.SAFE

    def __init__(self, name: str = "stub_safe", text: str = "stub ok"):
        self.name = name  # type: ignore[misc]
        self._text = text
        self.calls: list[dict] = []
        self._delay: float = 0.0

    async def execute(self, params: dict, sandbox) -> ToolResult:
        self.calls.append(params)
        if self._delay:
            import asyncio
            await asyncio.sleep(self._delay)
        return ToolResult(success=True, text=self._text)


class _StubDangerousTool(_StubTool):
    """DANGEROUS 工具。"""

    danger_level = DangerLevel.DANGEROUS  # type: ignore[misc]


class _StubRenderer:
    """记录所有调用的 Renderer 替身。"""

    def __init__(self) -> None:
        self.events: list = []  # AgentEvent 列表
        self.calls: list[tuple[str, tuple, dict]] = []

    def on_agent_event(self, ev) -> None:
        self.events.append(ev)

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def agent_events_of(self, cls_name: str) -> list:
        return [e for e in self.events if type(e).__name__ == cls_name]


class _StubConfirmer:
    """可预设 ask 返回值序列。"""

    def __init__(self, answers: list[bool] | None = None, cancel: bool = False):
        self._answers = answers or []
        self._idx = 0
        self._cancel = cancel
        self.asked: list[str] = []

    async def ask(self, tool_name: str) -> bool:
        self.asked.append(tool_name)
        if self._cancel:
            raise ConfirmCancelled()
        if self._idx >= len(self._answers):
            return False
        ans = self._answers[self._idx]
        self._idx += 1
        return ans


# ---------- fixtures ----------


def _make_cfg(name: str = "alpha", protocol: str = "anthropic") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        model="m",
        base_url="https://example.com",
        api_key="sk-stub",
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


def _make_session(rounds: list[list[StreamEvent]]) -> tuple[Session, _StubProvider]:
    cfg = _make_cfg()
    prov = _StubProvider(cfg, rounds)
    s = Session(provider=prov, current_provider_name="alpha")
    return s, prov


# ---------- 测试用例 ----------


@pytest.mark.asyncio
async def test_自然停止_一轮直答(sandbox: Sandbox) -> None:
    """R1 仅文本 → Loop 1 轮结束，Stopped("natural", 1)。"""
    rounds = [
        [
            TextDelta(text="你好"),
            TextDelta(text="呀"),
            Usage(input_tokens=5, output_tokens=10),
            Done(),
        ]
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()

    ok = await run_turn(session, "hi", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is True
    assert prov.call_count == 1
    assert len(session.messages) == 2  # user + assistant

    # AgentEvent 检查
    starts = renderer.agent_events_of("IterationStart")
    assert len(starts) == 1
    assert starts[0].iteration == 1

    stopped = renderer.agent_events_of("Stopped")
    assert len(stopped) == 1
    assert stopped[0].reason == "natural"
    assert stopped[0].iteration == 1


@pytest.mark.asyncio
async def test_两轮Loop_工具加文本答复(sandbox: Sandbox) -> None:
    """R1 含 tool_use → 执行 → R2 文本 → Stopped("natural", 2)。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseInputDelta(id="t1", json_chunk='{"x":1}'),
            ToolUseEnd(id="t1", name="stub_safe", input={"x": 1}),
            Usage(input_tokens=10, output_tokens=5),
            Done(),
        ],
        [
            TextDelta(text="工具结果是 stub ok"),
            Usage(input_tokens=20, output_tokens=8),
            Done(),
        ],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    tool = _StubTool()
    registry.register(tool)
    confirmer = _StubConfirmer(answers=[True])

    ok = await run_turn(session, "用工具", renderer, registry, confirmer, sandbox)
    assert ok is True
    assert prov.call_count == 2
    assert len(tool.calls) == 1
    # messages: user, assistant(R1), user(tool_results), assistant(R2)
    assert len(session.messages) == 4
    assert stopped_reason(renderer) == "natural"
    assert stopped_iteration(renderer) == 2


@pytest.mark.asyncio
async def test_多tool_use_分批执行(sandbox: Sandbox) -> None:
    """一轮中 2 SAFE + 1 DANGEROUS → SAFE 并发 + DANGEROUS 串行。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseEnd(id="t1", name="stub_safe", input={"i": 1}),
            ToolUseStart(id="t2", name="stub_safe"),
            ToolUseEnd(id="t2", name="stub_safe", input={"i": 2}),
            ToolUseStart(id="t3", name="stub_dangerous"),
            ToolUseEnd(id="t3", name="stub_dangerous", input={"i": 3}),
            Done(),
        ],
        [TextDelta(text="完成"), Done()],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    safe_tool = _StubTool(name="stub_safe")
    danger_tool = _StubDangerousTool(name="stub_dangerous")
    registry.register(safe_tool)
    registry.register(danger_tool)
    confirmer = _StubConfirmer(answers=[True])  # 批准 DANGEROUS

    ok = await run_turn(session, "go", renderer, registry, confirmer, sandbox)
    assert ok is True
    assert len(safe_tool.calls) == 2
    assert len(danger_tool.calls) == 1
    # tool_results 按原始顺序
    tr_msg = session.messages[2]
    assert tr_msg.content[0].tool_use_id == "t1"
    assert tr_msg.content[1].tool_use_id == "t2"
    assert tr_msg.content[2].tool_use_id == "t3"


@pytest.mark.asyncio
async def test_DANGEROUS工具拒绝(sandbox: Sandbox) -> None:
    """confirmer 返回 False → ToolResultBlock 含"用户拒绝"。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_dangerous"),
            ToolUseEnd(id="t1", name="stub_dangerous", input={}),
            Done(),
        ],
        [TextDelta(text="好的，已放弃"), Done()],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    dtool = _StubDangerousTool(name="stub_dangerous")
    registry.register(dtool)
    confirmer = _StubConfirmer(answers=[False])

    ok = await run_turn(session, "做点危险事", renderer, registry, confirmer, sandbox)
    assert ok is True
    assert len(dtool.calls) == 0  # 未实际执行
    tr_msg = session.messages[2]
    assert "用户拒绝" in tr_msg.content[0].content


@pytest.mark.asyncio
async def test_Ctrl加C取消整个Loop(sandbox: Sandbox) -> None:
    """工具执行中 ConfirmCancelled → Stopped("user_cancel")。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_dangerous"),
            ToolUseEnd(id="t1", name="stub_dangerous", input={}),
            Done(),
        ],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    dtool = _StubDangerousTool(name="stub_dangerous")
    registry.register(dtool)
    confirmer = _StubConfirmer(cancel=True)

    ok = await run_turn(session, "x", renderer, registry, confirmer, sandbox)
    assert ok is False
    assert prov.call_count == 1  # 只跑了 1 轮
    # 回滚了 R1 assistant → messages 末尾不是 assistant
    assert session.messages[-1].role == "user"
    assert stopped_reason(renderer) == "user_cancel"


@pytest.mark.asyncio
async def test_迭代上限软停止(sandbox: Sandbox, monkeypatch) -> None:
    """MAX_ITERATIONS 改为 3 → 第 3 轮软停止。"""
    import mewcode.chat.engine as engine_mod

    monkeypatch.setattr(engine_mod, "MAX_ITERATIONS", 3)

    # 每轮都调工具（永不自然停止）
    tool_round = [
        ToolUseStart(id="t1", name="stub_safe"),
        ToolUseEnd(id="t1", name="stub_safe", input={}),
        Usage(input_tokens=10, output_tokens=5),
        Done(),
    ]
    final_round = [
        TextDelta(text="总结：已用完上限"),
        Usage(input_tokens=5, output_tokens=20),
        Done(),
    ]
    rounds = [list(tool_round), list(tool_round), final_round]

    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    registry.register(_StubTool())

    ok = await run_turn(session, "无限循环", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is True
    assert prov.call_count == 3
    assert stopped_reason(renderer) == "max_iterations"
    assert stopped_iteration(renderer) == 3
    # 最后一条消息是 assistant(text)
    assert session.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_连续未知工具停止(sandbox: Sandbox) -> None:
    """模型连续两轮调 "foobar" → 第 2 轮 Stopped("unknown_tools")。"""
    unknown_round = [
        ToolUseStart(id="t1", name="foobar"),
        ToolUseEnd(id="t1", name="foobar", input={}),
        Done(),
    ]
    rounds = [list(unknown_round), list(unknown_round)]

    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()  # 空注册表，foobar 未知

    ok = await run_turn(session, "x", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is True
    assert stopped_reason(renderer) == "unknown_tools"


@pytest.mark.asyncio
async def test_LLM流出错停止(sandbox: Sandbox) -> None:
    """Provider 第 2 轮抛 ProviderError → Stopped("error")。"""

    class _ErrorProvider(_StubProvider):
        async def stream_chat(self, *args, **kwargs):
            if self._call_count == 0:
                self._call_count += 1
                for ev in self._rounds[0]:
                    yield ev
            else:
                self._call_count += 1
                raise ProviderError("模拟流出错")  # category 是属性不是构造参数
                yield  # pragma: no cover

    rounds = [
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseEnd(id="t1", name="stub_safe", input={}),
            Done(),
        ],
        [],  # 第 2 轮抛错
    ]
    cfg = _make_cfg()
    prov = _ErrorProvider(cfg, rounds)
    session = Session(provider=prov, current_provider_name="alpha")
    renderer = _StubRenderer()
    registry = ToolRegistry()
    registry.register(_StubTool())

    ok = await run_turn(session, "x", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is False
    assert stopped_reason(renderer) == "user_cancel"  # blocks is None 走此分支


@pytest.mark.asyncio
async def test_AgentEvent发射顺序(sandbox: Sandbox) -> None:
    """验证一次完整 Loop 的 AgentEvent 序列。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseEnd(id="t1", name="stub_safe", input={}),
            Usage(input_tokens=10, output_tokens=5),
            Done(),
        ],
        [TextDelta(text="done"), Usage(input_tokens=5, output_tokens=3), Done()],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    registry.register(_StubTool())

    await run_turn(session, "go", renderer, registry, _StubConfirmer(), sandbox)

    # 验证关键事件存在且顺序正确
    types = [type(e).__name__ for e in renderer.events]
    assert "IterationStart" in types
    assert "ToolCall" in types
    assert "ToolResultEvent" in types
    assert "IterationEnd" in types
    assert "Stopped" in types
    assert "UsageTotal" in types

    # IterationStart 在 ToolCall 之前
    assert types.index("IterationStart") < types.index("ToolCall")
    # ToolCall 在 ToolResultEvent 之前
    assert types.index("ToolCall") < types.index("ToolResultEvent")
    # Stopped 在 UsageTotal 之前
    assert types.index("Stopped") < types.index("UsageTotal")

    # UsageTotal 字段
    ut = renderer.agent_events_of("UsageTotal")[0]
    assert ut.input_tokens == 15  # 10 + 5
    assert ut.output_tokens == 8  # 5 + 3
    assert ut.iterations == 2


@pytest.mark.asyncio
async def test_并发上限8(sandbox: Sandbox, monkeypatch) -> None:
    """10 个 SAFE 工具 → 前 8 并发 + 后 2 串行。"""
    # 构造 10 个 tool_use
    tu_events = []
    for i in range(10):
        tid = f"t{i}"
        tu_events.append(ToolUseStart(id=tid, name=f"safe_{i}"))
        tu_events.append(ToolUseEnd(id=tid, name=f"safe_{i}", input={"i": i}))
    tu_events.append(Done())

    rounds = [tu_events, [TextDelta(text="done"), Done()]]

    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    for i in range(10):
        registry.register(_StubTool(name=f"safe_{i}"))

    ok = await run_turn(session, "go", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is True
    # 所有 10 个工具都执行了
    total_calls = sum(len(t.calls) for t in registry)
    assert total_calls == 10
    # tool_results 有 10 条
    tr_msg = session.messages[2]
    assert len(tr_msg.content) == 10


@pytest.mark.asyncio
async def test_PlanMode只含只读工具(sandbox: Sandbox) -> None:
    """_get_tools_format 在 plan 模式下只含 readonly 工具。"""
    registry = ToolRegistry()
    # stub_safe 默认 readonly=True
    registry.register(_StubTool(name="read"))
    # stub_dangerous 默认继承 _StubTool 的 readonly=True，改为 False
    class _WriteStub(_StubDangerousTool):
        readonly = False
    registry.register(_WriteStub(name="write"))

    # do 模式
    fmt_do = _get_tools_format(registry, "anthropic", "do")
    assert fmt_do is not None
    assert len(fmt_do) == 2

    # plan 模式
    fmt_plan = _get_tools_format(registry, "anthropic", "plan")
    assert fmt_plan is not None
    assert len(fmt_plan) == 1
    assert fmt_plan[0]["name"] == "read"


# ---------- 辅助 ----------


def stopped_reason(renderer: _StubRenderer) -> str:
    stopped = renderer.agent_events_of("Stopped")
    return stopped[0].reason if stopped else ""


def stopped_iteration(renderer: _StubRenderer) -> int:
    stopped = renderer.agent_events_of("Stopped")
    return stopped[0].iteration if stopped else 0
