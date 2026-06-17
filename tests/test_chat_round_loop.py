"""chat.run_turn 的编排单元测试（用 stub Provider 不打真实 API）。

覆盖 task.md T21 的 6 个核心场景：
- R1 直答 → 退化为第一阶段
- R1 工具 + R2 文本 → 完整闭环
- 单 R1 多个 tool_use 串行执行
- 用户拒绝 → R1 入历史 + 拒绝 result 进 Round 2
- 中断回滚 → ConfirmCancelled → messages.pop
- R2 含 tool_use 硬停 → 剥离 + leftover 提示
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mewcode.chat import Session, run_turn
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
    ) -> AsyncIterator[StreamEvent]:
        idx = self._call_count
        self._call_count += 1
        if idx >= len(self._rounds):
            # 超出预设：返回空（仅 Done）
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
        type(self).name = name  # type: ignore[misc]
        # 用实例属性覆盖类属性以避免不同实例互相干扰
        self.name = name  # type: ignore[misc]
        self._text = text
        self.calls: list[dict] = []

    async def execute(self, params: dict, sandbox) -> ToolResult:
        self.calls.append(params)
        return ToolResult(success=True, text=self._text)


class _StubDangerousTool(_StubTool):
    """DANGEROUS 工具，记录 confirm_detail 调用。"""

    danger_level = DangerLevel.DANGEROUS


class _StubRenderer:
    """记录所有调用的 Renderer 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def has(self, method: str) -> bool:
        return any(c[0] == method for c in self.calls)

    def count(self, method: str) -> int:
        return sum(1 for c in self.calls if c[0] == method)


class _StubConfirmer:
    """可预设 ask 返回值序列；可设为 ConfirmCancelled。"""

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
async def test_R1直答_退化第一阶段(sandbox: Sandbox) -> None:
    """R1 仅含 TextDelta+Done → 不发起 R2，messages 末尾是 user/assistant。"""
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
    registry = ToolRegistry()  # 空 registry

    ok = await run_turn(session, "hi", renderer, registry, _StubConfirmer(), sandbox)
    assert ok is True
    assert prov.call_count == 1, "应只发起 1 次 LLM 请求"
    # messages = [user, assistant]
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"
    # assistant 内容是 TextBlock
    assert isinstance(session.messages[1].content[0], TextBlock)
    assert session.messages[1].content[0].text == "你好呀"
    # 应调用 print_usage 一次（R1 直答路径）
    assert renderer.has("print_usage")
    # 不应调用 print_usage_combined
    assert not renderer.has("print_usage_combined")


@pytest.mark.asyncio
async def test_R1工具_R2文本_完整闭环(sandbox: Sandbox) -> None:
    """完整闭环：R1 含 tool_use → 执行 → R2 文本答复。"""
    rounds = [
        # R1：模型决定调用 stub_safe 工具
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseInputDelta(id="t1", json_chunk='{"x":1}'),
            ToolUseEnd(id="t1", name="stub_safe", input={"x": 1}),
            Usage(input_tokens=10, output_tokens=5),
            Done(),
        ],
        # R2：基于工具结果给出最终答复
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
    assert prov.call_count == 2, "应发起 2 次 LLM 请求（R1 + R2）"
    # 工具被调用一次
    assert len(tool.calls) == 1
    assert tool.calls[0] == {"x": 1}
    # messages: user, assistant(R1 含 tool_use), user(tool_results), assistant(R2)
    assert len(session.messages) == 4
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"
    assert session.messages[2].role == "user"
    assert session.messages[3].role == "assistant"
    # R1 assistant 含 ToolUseBlock
    assert any(isinstance(b, ToolUseBlock) for b in session.messages[1].content)
    # tool_results 入历史
    assert any(
        isinstance(b, ToolResultBlock) for b in session.messages[2].content
    )
    # R2 含文本
    assert any(
        isinstance(b, TextBlock) and "stub ok" in b.text
        for b in session.messages[3].content
    )
    # 用量行：累计版（不是 print_usage 单次版）
    assert renderer.has("print_usage_combined")


@pytest.mark.asyncio
async def test_单R1多tool_use_串行(sandbox: Sandbox) -> None:
    """R1 含 2 个 tool_use → 顺序执行 2 次，R2 答复正常。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseEnd(id="t1", name="stub_safe", input={"i": 1}),
            ToolUseStart(id="t2", name="stub_safe"),
            ToolUseEnd(id="t2", name="stub_safe", input={"i": 2}),
            Done(),
        ],
        [TextDelta(text="完成"), Done()],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    tool = _StubTool()
    registry.register(tool)
    confirmer = _StubConfirmer()

    ok = await run_turn(session, "go", renderer, registry, confirmer, sandbox)
    assert ok is True
    assert len(tool.calls) == 2
    # 顺序：先 i=1 后 i=2
    assert tool.calls[0] == {"i": 1}
    assert tool.calls[1] == {"i": 2}
    # tool_results 应有两条
    tr_msg = session.messages[2]
    assert len(tr_msg.content) == 2


@pytest.mark.asyncio
async def test_用户拒绝_R1入历史_拒绝result进R2(sandbox: Sandbox) -> None:
    """DANGEROUS 工具被拒绝时，R1 仍入历史，'用户拒绝执行' 作为 tool_result。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_dangerous"),
            ToolUseEnd(id="t1", name="stub_dangerous", input={"x": 1}),
            Done(),
        ],
        [TextDelta(text="好的，已放弃"), Done()],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    dtool = _StubDangerousTool(name="stub_dangerous")
    registry.register(dtool)
    confirmer = _StubConfirmer(answers=[False])  # 拒绝

    ok = await run_turn(session, "做点危险事", renderer, registry, confirmer, sandbox)
    assert ok is True  # turn 整体成功完成（虽然工具被拒绝）
    # 工具未被实际执行
    assert len(dtool.calls) == 0
    # R1 入历史
    assert len(session.messages) == 4  # user, R1 assistant, tool_results, R2 assistant
    # tool_result 内容含 "用户拒绝"
    tr_msg = session.messages[2]
    assert any(
        isinstance(b, ToolResultBlock) and "用户拒绝" in b.content
        for b in tr_msg.content
    )
    # Confirmer 被问过一次
    assert confirmer.asked == ["stub_dangerous"]
    # Renderer 收到 print_tool_rejected
    assert renderer.has("print_tool_rejected")
    # R2 仍发起
    assert prov.call_count == 2


@pytest.mark.asyncio
async def test_中断回滚_ConfirmCancelled(sandbox: Sandbox) -> None:
    """确认提示中按 Ctrl+C → 回滚 R1 assistant，return False。"""
    rounds = [
        [
            ToolUseStart(id="t1", name="stub_dangerous"),
            ToolUseEnd(id="t1", name="stub_dangerous", input={}),
            Done(),
        ],
        # 不应被调用
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    dtool = _StubDangerousTool(name="stub_dangerous")
    registry.register(dtool)
    confirmer = _StubConfirmer(cancel=True)  # ConfirmCancelled

    ok = await run_turn(session, "x", renderer, registry, confirmer, sandbox)
    assert ok is False
    assert prov.call_count == 1  # 只跑了 R1，没进 R2
    # messages 末尾不是 R1 assistant（已 pop），应只剩 user
    assert len(session.messages) == 1
    assert session.messages[0].role == "user"


@pytest.mark.asyncio
async def test_R2含tool_use硬停(sandbox: Sandbox) -> None:
    """R2 流再含 tool_use 时硬停：剥离 + 灰字提示。"""
    rounds = [
        # R1：调一次工具
        [
            ToolUseStart(id="t1", name="stub_safe"),
            ToolUseEnd(id="t1", name="stub_safe", input={}),
            Done(),
        ],
        # R2：又想调工具（硬停场景）
        [
            TextDelta(text="先回答一下，然后..."),
            ToolUseStart(id="t2", name="stub_safe"),
            ToolUseEnd(id="t2", name="stub_safe", input={"y": 9}),
            Done(),
        ],
    ]
    session, prov = _make_session(rounds)
    renderer = _StubRenderer()
    registry = ToolRegistry()
    tool = _StubTool()
    registry.register(tool)
    confirmer = _StubConfirmer()

    ok = await run_turn(session, "测试", renderer, registry, confirmer, sandbox)
    assert ok is True
    # R1 工具应执行 1 次；R2 的 tool_use 不执行
    assert len(tool.calls) == 1
    # messages: user, R1, tool_results, R2(已剥离 tool_use)
    assert len(session.messages) == 4
    r2_msg = session.messages[3]
    # R2 中应不含 ToolUseBlock
    assert not any(isinstance(b, ToolUseBlock) for b in r2_msg.content)
    # 但 TextBlock 仍在
    assert any(isinstance(b, TextBlock) for b in r2_msg.content)
    # Renderer 应收到含"还想调用工具"的 print_info
    info_calls = [c for c in renderer.calls if c[0] == "print_info"]
    assert any("还想调用工具" in c[1][0] for c in info_calls)
