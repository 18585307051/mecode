"""Compactor 综合单测（spec AC8 / AC9 / AC17 / AC18 / AC19 / AC20 / AC22）。"""

from pathlib import Path

import pytest

from mewcode.compaction.compactor import AUTO_COMPACT_THRESHOLD, Compactor
from mewcode.providers import (
    Done,
    Message,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------- stubs ----------


class _StubProvider:
    model = "deepseek-v4-pro"

    def __init__(self, output_text: str = "", raise_error: bool = False) -> None:
        self._output = output_text
        self._raise = raise_error

    async def stream_chat(self, messages, thinking, **kwargs):
        if self._raise:
            raise RuntimeError("boom")
        for ch in self._output:
            yield TextDelta(text=ch)
        yield Done()


class _StubSession:
    def __init__(self, provider, messages=None) -> None:
        self.provider = provider
        self.messages = list(messages or [])
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False
        self.session_id = "test_session"


def _user_text(t: str) -> Message:
    return Message(role="user", content=[TextBlock(text=t)])


def _assistant_text(t: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=t)])


_VALID_SUMMARY = (
    "<summary>"
    "## 会话目标\nGoal\n"
    "## 关键决策\nDec\n"
    "## 代码变更\nChange\n"
    "## 未完成事项\nTodo\n"
    "## 当前状态\nStatus\n"
    "</summary>"
)


# ---------- before_request ----------


@pytest.mark.asyncio
async def test_未达阈值不触发(tmp_path: Path) -> None:
    """spec AC8：估算未达 auto_threshold → 不触发摘要。"""
    provider = _StubProvider()
    session = _StubSession(provider, messages=[_user_text("hi")])
    compactor = Compactor(cwd=tmp_path)
    stats = await compactor.before_request(session)
    assert stats.summary_triggered is False


@pytest.mark.asyncio
async def test_达阈值触发并替换messages(tmp_path: Path) -> None:
    """spec AC9：达阈值 → 调摘要 → 替换 messages。"""
    provider = _StubProvider(output_text=_VALID_SUMMARY)
    # 构造大量历史让锚点估算超过 auto_threshold
    msgs = [_user_text("初始问题")]
    # 构造真实很大的历史：每条约 3KB，足以让 keep_boundary > 0
    for i in range(20):
        msgs.append(_assistant_text(f"答 {i} " + ("x" * 3000)))
        msgs.append(_user_text(f"问 {i} " + ("y" * 3000)))
    session = _StubSession(provider, messages=msgs)
    # 强制锚点估算到接近 window
    session.last_usage_input_tokens = AUTO_COMPACT_THRESHOLD + 100
    session.last_anchor_message_count = len(msgs)

    compactor = Compactor(cwd=tmp_path)
    stats = await compactor.before_request(session)

    assert stats.summary_triggered is True
    assert stats.summary_succeeded is True
    # 第 0 条是边界 reminder
    first_text = session.messages[0].content[0].text
    assert "Context Compacted" in first_text
    # 锚点已重置
    assert session.last_usage_input_tokens == 0


# ---------- 熔断 ----------


@pytest.mark.asyncio
async def test_3次失败_disabled(tmp_path: Path) -> None:
    """spec AC17：连续 3 次失败 → disabled=True。"""
    provider = _StubProvider(raise_error=True)
    msgs = [_user_text("初始")]
    for i in range(20):
        msgs.append(_assistant_text(f"a{i}"))
        msgs.append(_user_text(f"q{i}"))
    session = _StubSession(provider, messages=msgs)
    session.last_usage_input_tokens = AUTO_COMPACT_THRESHOLD + 100
    session.last_anchor_message_count = len(msgs)

    compactor = Compactor(cwd=tmp_path)
    for _ in range(3):
        await compactor.before_request(session)

    assert session.compaction_disabled is True
    assert session.compaction_failures == 3


@pytest.mark.asyncio
async def test_disabled跳过自动(tmp_path: Path) -> None:
    """spec AC18：disabled 时 before_request 跳过摘要。"""
    provider = _StubProvider(output_text=_VALID_SUMMARY)
    msgs = [_user_text("hi")]
    session = _StubSession(provider, messages=msgs)
    session.compaction_disabled = True
    session.last_usage_input_tokens = AUTO_COMPACT_THRESHOLD + 100
    session.last_anchor_message_count = len(msgs)

    compactor = Compactor(cwd=tmp_path)
    stats = await compactor.before_request(session)
    assert stats.summary_triggered is False


@pytest.mark.asyncio
async def test_compact_now_必触发(tmp_path: Path) -> None:
    """spec AC20：/compact 命令必触发（即便未达阈值）。"""
    provider = _StubProvider(output_text=_VALID_SUMMARY)
    msgs = [_user_text("初始")]
    for i in range(10):
        msgs.append(_assistant_text(f"a{i}"))
        msgs.append(_user_text(f"q{i}"))
    session = _StubSession(provider, messages=msgs)
    # token 远未达阈值
    compactor = Compactor(cwd=tmp_path)
    stats = await compactor.compact_now(session)
    assert stats.summary_triggered is True


@pytest.mark.asyncio
async def test_手动失败不熔断(tmp_path: Path) -> None:
    """spec AC22：/compact 失败 → failures 不增加。"""
    provider = _StubProvider(raise_error=True)
    msgs = [_user_text("初始")]
    for i in range(20):
        msgs.append(_assistant_text(f"a{i}"))
        msgs.append(_user_text(f"q{i}"))
    session = _StubSession(provider, messages=msgs)
    compactor = Compactor(cwd=tmp_path)
    for _ in range(3):
        await compactor.compact_now(session)
    # 手动失败不计 failures
    assert session.compaction_failures == 0
    assert session.compaction_disabled is False


# ---------- after_response ----------


def test_after_response_更新锚点(tmp_path: Path) -> None:
    """API 响应后更新 last_usage / anchor_count。"""
    from mewcode.providers import Usage

    provider = _StubProvider()
    session = _StubSession(provider, messages=[_user_text("a"), _assistant_text("b")])
    compactor = Compactor(cwd=tmp_path)

    usage = Usage(input_tokens=1234, output_tokens=10)
    compactor.after_response(session, usage)
    assert session.last_usage_input_tokens == 1234
    assert session.last_anchor_message_count == 2


def test_after_response_零值不更新(tmp_path: Path) -> None:
    from mewcode.providers import Usage

    provider = _StubProvider()
    session = _StubSession(provider, messages=[_user_text("a")])
    session.last_usage_input_tokens = 999
    session.last_anchor_message_count = 5

    compactor = Compactor(cwd=tmp_path)
    compactor.after_response(session, Usage(input_tokens=0, output_tokens=10))
    # 未更新
    assert session.last_usage_input_tokens == 999


# ---------- reset_state ----------


def test_reset_state(tmp_path: Path) -> None:
    """/clear / switch_provider 时调 reset_state 重置压缩状态。"""
    provider = _StubProvider()
    session = _StubSession(provider, messages=[])
    session.last_usage_input_tokens = 1000
    session.last_anchor_message_count = 5
    session.compaction_failures = 2
    session.compaction_disabled = True

    compactor = Compactor(cwd=tmp_path)
    compactor.reset_state(session)

    assert session.last_usage_input_tokens == 0
    assert session.last_anchor_message_count == 0
    assert session.compaction_failures == 0
    assert session.compaction_disabled is False


# ---------- 第一层始终跑 ----------


@pytest.mark.asyncio
async def test_第一层始终跑_即便disabled(tmp_path: Path) -> None:
    """disabled 时仍跑第一层，只跳过第二层。"""
    provider = _StubProvider()
    big_block = ToolResultBlock(
        tool_use_id="t1", content="x" * 12000, is_error=False
    )
    msgs = [
        _user_text("hi"),
        Message(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="read", input={})],
        ),
        Message(role="user", content=[big_block]),
    ]
    session = _StubSession(provider, messages=msgs)
    session.compaction_disabled = True

    compactor = Compactor(cwd=tmp_path)
    stats = await compactor.before_request(session)

    # 第一层存盘了
    assert len(stats.stash_events) == 1
    # 第二层未触发
    assert stats.summary_triggered is False
