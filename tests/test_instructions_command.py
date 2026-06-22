"""/instructions 命令族单测（spec AC9-AC12）。"""

from pathlib import Path
from typing import Iterator

import pytest

from mewcode.commands import (
    COMMANDS,
    CommandContext,
    dispatch,
    register_builtins,
)
from mewcode.config import AppConfig, ProviderConfig
from mewcode.instructions import InstructionsLoader
from mewcode.providers import Provider, StreamEvent
from collections.abc import AsyncIterator


# ---------- stubs（与 test_permissions_command 同套路） ----------


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
        def _noop(*a, **kw):
            pass
        return _noop


class _StubSession:
    def __init__(self) -> None:
        self.provider = _StubProvider(_make_cfg())
        self.messages = []
        self.thinking_enabled = False
        self.current_provider_name = "alpha"
        self.system_prompt = "## old"
        self.mode = "do"
        self.plan_turn_count = 0


def _make_cfg() -> ProviderConfig:
    return ProviderConfig(
        name="alpha", protocol="anthropic", model="m",
        base_url="https://x", api_key="sk",
    )


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(providers={"alpha": _make_cfg()}, default="alpha")


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch) -> Iterator[None]:
    saved = COMMANDS.copy()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".mewcode").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    register_builtins()
    try:
        yield
    finally:
        COMMANDS.clear()
        COMMANDS.update(saved)


@pytest.fixture
def renderer() -> _StubRenderer:
    return _StubRenderer()


def _make_ctx(loader, renderer, app_config, rebuild=None) -> CommandContext:
    return CommandContext(
        session=_StubSession(),  # type: ignore[arg-type]
        app_config=app_config,
        args=[],
        renderer=renderer,  # type: ignore[arg-type]
        instructions=loader,
        rebuild_system_prompt=rebuild,
    )


# ---------- /instructions show ----------


@pytest.mark.asyncio
async def test_show_无内容(tmp_path: Path, renderer, app_config) -> None:
    """spec AC10：三层全空时 show 提示未加载。"""
    loader = InstructionsLoader(tmp_path)
    loader.load_all()  # None

    await dispatch(
        "/instructions show",
        _make_ctx(loader, renderer, app_config),
    )
    joined = "\n".join(renderer.infos)
    assert "未加载任何项目指令" in joined
    assert "AGENTS.md" in joined


@pytest.mark.asyncio
async def test_show_有内容(tmp_path: Path, renderer, app_config) -> None:
    """spec AC9：show 打印当前指令文本。"""
    (tmp_path / "AGENTS.md").write_text("MY RULES", encoding="utf-8")
    loader = InstructionsLoader(tmp_path)
    loader.load_all()

    await dispatch(
        "/instructions show",
        _make_ctx(loader, renderer, app_config),
    )
    joined = "\n".join(renderer.infos)
    assert "MY RULES" in joined
    assert "### 项目规则" in joined


@pytest.mark.asyncio
async def test_show_缺省(tmp_path: Path, renderer, app_config) -> None:
    """/instructions 不带子命令 → 默认 show。"""
    (tmp_path / "AGENTS.md").write_text("DEFAULT SHOW", encoding="utf-8")
    loader = InstructionsLoader(tmp_path)
    loader.load_all()

    await dispatch(
        "/instructions",
        _make_ctx(loader, renderer, app_config),
    )
    assert any("DEFAULT SHOW" in info for info in renderer.infos)


# ---------- /instructions reload ----------


@pytest.mark.asyncio
async def test_reload_未变化(tmp_path: Path, renderer, app_config) -> None:
    """spec AC11：内容未变 → 不调 rebuild 且打印未变化。"""
    (tmp_path / "AGENTS.md").write_text("v1", encoding="utf-8")
    loader = InstructionsLoader(tmp_path)
    loader.load_all()

    rebuild_calls: list = []

    def rebuild(text):
        rebuild_calls.append(text)

    await dispatch(
        "/instructions reload",
        _make_ctx(loader, renderer, app_config, rebuild=rebuild),
    )

    joined = "\n".join(renderer.infos)
    assert "未变化" in joined
    assert rebuild_calls == []  # rebuild 未被调用


@pytest.mark.asyncio
async def test_reload_内容变化_触发rebuild(
    tmp_path: Path, renderer, app_config
) -> None:
    """spec AC12：内容变了 → 调 rebuild + 打印已重新加载。"""
    target = tmp_path / "AGENTS.md"
    target.write_text("v1", encoding="utf-8")
    loader = InstructionsLoader(tmp_path)
    loader.load_all()

    target.write_text("v2 NEW", encoding="utf-8")

    rebuild_calls: list = []

    def rebuild(text):
        rebuild_calls.append(text)

    await dispatch(
        "/instructions reload",
        _make_ctx(loader, renderer, app_config, rebuild=rebuild),
    )

    joined = "\n".join(renderer.infos)
    assert "已重新加载" in joined
    # rebuild 被调用一次，新文本含 v2 NEW
    assert len(rebuild_calls) == 1
    assert "v2 NEW" in rebuild_calls[0]


@pytest.mark.asyncio
async def test_reload_新增文件_触发rebuild(
    tmp_path: Path, renderer, app_config
) -> None:
    """初始无文件 → 加文件 → reload 触发 rebuild。"""
    loader = InstructionsLoader(tmp_path)
    loader.load_all()  # None

    (tmp_path / "AGENTS.md").write_text("FRESH", encoding="utf-8")

    rebuild_calls: list = []
    await dispatch(
        "/instructions reload",
        _make_ctx(loader, renderer, app_config, rebuild=rebuild_calls.append),
    )

    joined = "\n".join(renderer.infos)
    assert "已重新加载" in joined
    assert len(rebuild_calls) == 1
    assert "FRESH" in rebuild_calls[0]


# ---------- 边界 ----------


@pytest.mark.asyncio
async def test_loader_为None_提示未启用(renderer, app_config) -> None:
    """instructions=None 时友好提示。"""
    ctx = CommandContext(
        session=_StubSession(),  # type: ignore[arg-type]
        app_config=app_config,
        args=[],
        renderer=renderer,  # type: ignore[arg-type]
        instructions=None,
    )
    await dispatch("/instructions show", ctx)
    assert any("未启用" in info for info in renderer.infos)


@pytest.mark.asyncio
async def test_未知子命令(tmp_path: Path, renderer, app_config) -> None:
    loader = InstructionsLoader(tmp_path)
    loader.load_all()
    await dispatch(
        "/instructions wtf",
        _make_ctx(loader, renderer, app_config),
    )
    joined = "\n".join(renderer.infos)
    assert "未知子命令" in joined or "用法" in joined
