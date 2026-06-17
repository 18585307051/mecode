"""Plan Mode 单元测试。

覆盖 spec AC10 / AC11：
- /plan /do 命令切换
- _get_tools_format 物理隔离
- /clear /provider 重置 mode
"""

from pathlib import Path

import pytest

from mewcode.chat.engine import _get_tools_format
from mewcode.chat.session import Session
from mewcode.commands import CommandContext, dispatch, register_builtins
from mewcode.config import AppConfig, ProviderConfig
from mewcode.providers import Provider, Message, StreamEvent
from collections.abc import AsyncIterator
from mewcode.tools import DangerLevel, Sandbox, Tool, ToolRegistry, ToolResult


# ---------- stub ----------


class _StubProvider(Provider):
    async def stream_chat(self, messages, thinking, **kwargs) -> AsyncIterator[StreamEvent]:
        if False:
            yield


class _StubRenderer:
    def __init__(self):
        self.infos: list[str] = []

    def print_info(self, text: str) -> None:
        self.infos.append(text)

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            pass
        return _noop


class _StubSafe(Tool):
    name = "read"
    description = "read"
    parameters_schema = {"type": "object"}
    danger_level = DangerLevel.SAFE

    async def execute(self, params, sandbox) -> ToolResult:
        return ToolResult(True, "ok")


class _StubDangerous(Tool):
    name = "write"
    description = "write"
    parameters_schema = {"type": "object"}
    danger_level = DangerLevel.DANGEROUS
    readonly = False  # 有副作用

    async def execute(self, params, sandbox) -> ToolResult:
        return ToolResult(True, "ok")


# ---------- fixtures ----------


def _make_cfg() -> ProviderConfig:
    return ProviderConfig(
        name="alpha", protocol="anthropic", model="m",
        base_url="https://x", api_key="sk",
    )


@pytest.fixture
def session() -> Session:
    return Session(provider=_StubProvider(_make_cfg()), current_provider_name="alpha")


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(providers={"alpha": _make_cfg()}, default="alpha")


@pytest.fixture
def renderer() -> _StubRenderer:
    return _StubRenderer()


@pytest.fixture(autouse=True)
def _register():
    register_builtins()


def _make_ctx(session, app_config, renderer) -> CommandContext:
    return CommandContext(
        session=session, app_config=app_config, args=[], renderer=renderer,
    )


# ---------- 测试 ----------


@pytest.mark.asyncio
async def test_plan命令切换(session: Session, app_config, renderer) -> None:
    await dispatch("/plan", _make_ctx(session, app_config, renderer))
    assert session.mode == "plan"
    assert any("Plan Mode" in s for s in renderer.infos)


@pytest.mark.asyncio
async def test_do命令切回(session: Session, app_config, renderer) -> None:
    session.mode = "plan"
    await dispatch("/do", _make_ctx(session, app_config, renderer))
    assert session.mode == "do"
    assert any("执行模式" in s for s in renderer.infos)


def test_clear重置mode(session: Session) -> None:
    session.mode = "plan"
    session.clear()
    assert session.mode == "do"


def test_switch_provider重置mode(session: Session) -> None:
    session.mode = "plan"
    new_prov = _StubProvider(_make_cfg())
    session.switch_provider(new_prov, name="beta")
    assert session.mode == "do"


def test_get_tools_format_plan只含SAFE() -> None:
    """Plan Mode 下 tools_format 只含 SAFE 工具。"""
    registry = ToolRegistry()
    registry.register(_StubSafe())
    registry.register(_StubDangerous())

    # do 模式：2 个工具
    fmt_do = _get_tools_format(registry, "anthropic", "do")
    assert fmt_do is not None
    assert len(fmt_do) == 2
    names_do = {t["name"] for t in fmt_do}
    assert names_do == {"read", "write"}

    # plan 模式：只 1 个 SAFE 工具
    fmt_plan = _get_tools_format(registry, "anthropic", "plan")
    assert fmt_plan is not None
    assert len(fmt_plan) == 1
    assert fmt_plan[0]["name"] == "read"


def test_get_tools_format_openai协议() -> None:
    """OpenAI 协议下 plan 模式也只含 SAFE。"""
    registry = ToolRegistry()
    registry.register(_StubSafe())
    registry.register(_StubDangerous())

    fmt = _get_tools_format(registry, "openai", "plan")
    assert fmt is not None
    assert len(fmt) == 1
    assert fmt[0]["type"] == "function"
    assert fmt[0]["function"]["name"] == "read"
