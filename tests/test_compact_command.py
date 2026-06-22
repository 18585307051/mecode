"""/compact 命令单测（spec AC20 / AC21 / AC22）。"""

from collections.abc import AsyncIterator
from typing import Iterator

import pytest

from mewcode.commands import COMMANDS, CommandContext, dispatch, register_builtins
from mewcode.config import AppConfig, ProviderConfig
from mewcode.providers import Provider, StreamEvent


class _StubProvider(Provider):
    async def stream_chat(self, messages, thinking, **kwargs) -> AsyncIterator[StreamEvent]:
        if False:
            yield


def _make_cfg() -> ProviderConfig:
    return ProviderConfig(
        name="alpha", protocol="anthropic", model="m",
        base_url="https://x", api_key="sk",
    )


class _StubSession:
    def __init__(self) -> None:
        self.provider = _StubProvider(_make_cfg())
        self.messages = []
        self.compaction_failures = 0
        self.compaction_disabled = False


class _StubRenderer:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def print_info(self, text: str) -> None:
        self.infos.append(text)

    def __getattr__(self, name):
        def _noop(*a, **kw):
            pass
        return _noop


class _Stats:
    def __init__(self, *, ok=True, error=None):
        self.stash_events = []
        self.summary_triggered = True
        self.summary_succeeded = ok
        self.summary_error = error
        self.estimated_tokens_before = 1000
        self.estimated_tokens_after = 100
        self.compacted_message_count = 3


class _StubCompactor:
    def __init__(self, *, ok=True, error=None) -> None:
        self.calls: list[tuple[object, str]] = []
        self.ok = ok
        self.error = error

    async def compact_now(self, session, instruction: str = ""):
        self.calls.append((session, instruction))
        return _Stats(ok=self.ok, error=self.error)


@pytest.fixture(autouse=True)
def _isolate_commands() -> Iterator[None]:
    saved = COMMANDS.copy()
    register_builtins()
    try:
        yield
    finally:
        COMMANDS.clear()
        COMMANDS.update(saved)


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(providers={"alpha": _make_cfg()}, default="alpha")


def _ctx(compactor, renderer, app_config, session=None) -> CommandContext:
    return CommandContext(
        session=session or _StubSession(),  # type: ignore[arg-type]
        app_config=app_config,
        renderer=renderer,  # type: ignore[arg-type]
        compactor=compactor,
    )


@pytest.mark.asyncio
async def test_compact_默认(app_config) -> None:
    """spec AC20：/compact 调 compactor.compact_now。"""
    renderer = _StubRenderer()
    compactor = _StubCompactor()
    session = _StubSession()
    await dispatch("/compact", _ctx(compactor, renderer, app_config, session))
    assert compactor.calls == [(session, "")]
    joined = "\n".join(renderer.infos)
    assert "已压缩" in joined


@pytest.mark.asyncio
async def test_compact_带自定义指示(app_config) -> None:
    """spec AC21：/compact 后面的文本作为 instruction。"""
    renderer = _StubRenderer()
    compactor = _StubCompactor()
    await dispatch(
        "/compact 重点保留架构决策",
        _ctx(compactor, renderer, app_config),
    )
    assert compactor.calls[0][1] == "重点保留架构决策"


@pytest.mark.asyncio
async def test_compact_失败不熔断(app_config) -> None:
    """spec AC22：/compact 失败不修改 failures。"""
    renderer = _StubRenderer()
    compactor = _StubCompactor(ok=False, error="llm_failed")
    session = _StubSession()
    session.compaction_failures = 2
    await dispatch("/compact", _ctx(compactor, renderer, app_config, session))
    assert session.compaction_failures == 2
    joined = "\n".join(renderer.infos)
    assert "压缩失败" in joined


@pytest.mark.asyncio
async def test_compact_未启用(app_config) -> None:
    renderer = _StubRenderer()
    await dispatch("/compact", _ctx(None, renderer, app_config))
    assert any("未启用" in x for x in renderer.infos)
