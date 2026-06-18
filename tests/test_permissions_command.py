"""/permissions 命令族单测（spec AC12 / AC13 / AC14 / AC15 / AC16 / AC20）。"""

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
from mewcode.permissions import PermissionPolicy
from mewcode.providers import Provider, Message, StreamEvent
from collections.abc import AsyncIterator


# ---------- stubs ----------


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
        self.system_prompt = ""
        self.mode = "do"
        self.plan_turn_count = 0
        self.permission_session_allow = []
        self.permission_session_deny = []
        self.permission_mode_override = None

    def append_user_text(self, text: str) -> None:
        pass

    def append_assistant(self, blocks) -> None:
        pass

    def append_tool_results(self, results) -> None:
        pass

    def clear(self) -> None:
        pass

    def switch_provider(self, p, name="") -> None:
        self.provider = p


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
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    register_builtins()
    try:
        yield
    finally:
        COMMANDS.clear()
        COMMANDS.update(saved)


@pytest.fixture
def policy(tmp_path: Path) -> PermissionPolicy:
    return PermissionPolicy(tmp_path)


@pytest.fixture
def renderer() -> _StubRenderer:
    return _StubRenderer()


def _make_ctx(policy, renderer, app_config) -> CommandContext:
    return CommandContext(
        session=_StubSession(),  # type: ignore[arg-type]
        app_config=app_config,
        args=[],
        renderer=renderer,  # type: ignore[arg-type]
        policy=policy,
    )


# ---------- /permissions show（AC12）----------


@pytest.mark.asyncio
async def test_show_无规则(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions show", _make_ctx(policy, renderer, app_config)
    )
    # 应有 mode 显示
    joined = "\n".join(renderer.infos)
    assert "权限模式" in joined
    # 无规则时应有提示
    assert "无任何规则" in joined or "permissions init" in joined


@pytest.mark.asyncio
async def test_show_有session规则(policy, renderer, app_config) -> None:
    from mewcode.permissions.rules import parse_rule
    policy.add_session_allow(parse_rule("Bash(git *)"))
    await dispatch(
        "/permissions show", _make_ctx(policy, renderer, app_config)
    )
    joined = "\n".join(renderer.infos)
    assert "Bash(git *)" in joined
    assert "会话级" in joined


# ---------- /permissions allow（AC13）----------


@pytest.mark.asyncio
async def test_allow_添加规则(policy, renderer, app_config) -> None:
    await dispatch(
        '/permissions allow Bash(test *)',
        _make_ctx(policy, renderer, app_config),
    )
    assert len(policy.session_allow) == 1
    assert policy.session_allow[0].pattern == "test *"


@pytest.mark.asyncio
async def test_allow_带引号(policy, renderer, app_config) -> None:
    """用户加引号的规则也能识别。"""
    await dispatch(
        '/permissions allow "Bash(test *)"',
        _make_ctx(policy, renderer, app_config),
    )
    assert len(policy.session_allow) == 1


@pytest.mark.asyncio
async def test_allow_非法格式(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions allow invalid",
        _make_ctx(policy, renderer, app_config),
    )
    assert len(policy.session_allow) == 0
    joined = "\n".join(renderer.infos)
    assert "非法规则" in joined


# ---------- /permissions deny（AC13）----------


@pytest.mark.asyncio
async def test_deny_添加规则(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions deny Bash(rm *)",
        _make_ctx(policy, renderer, app_config),
    )
    assert len(policy.session_deny) == 1


# ---------- /permissions mode（AC14）----------


@pytest.mark.asyncio
async def test_mode_切换yolo(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions mode yolo", _make_ctx(policy, renderer, app_config)
    )
    assert policy.mode == "yolo"


@pytest.mark.asyncio
async def test_mode_切换strict(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions mode strict", _make_ctx(policy, renderer, app_config)
    )
    assert policy.mode == "strict"


@pytest.mark.asyncio
async def test_mode_非法档位(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions mode wild", _make_ctx(policy, renderer, app_config)
    )
    # 模式不变（仍 default）
    assert policy.mode == "default"


# ---------- /permissions reload（AC15）----------


@pytest.mark.asyncio
async def test_reload_清空session(policy, renderer, app_config) -> None:
    from mewcode.permissions.rules import parse_rule
    policy.add_session_allow(parse_rule("Bash(*)"))
    policy.set_mode_override("yolo")

    await dispatch(
        "/permissions reload", _make_ctx(policy, renderer, app_config)
    )

    assert len(policy.session_allow) == 0
    assert policy.mode == "default"  # override 被清


# ---------- /permissions init（AC16 / AC20）----------


@pytest.mark.asyncio
async def test_init_生成模板(policy, renderer, app_config, tmp_path) -> None:
    """init 应生成 .mewcode/permissions.yaml 模板。"""
    await dispatch(
        "/permissions init", _make_ctx(policy, renderer, app_config)
    )
    template = tmp_path / ".mewcode" / "permissions.yaml"
    assert template.exists()
    content = template.read_text(encoding="utf-8")
    assert "mode:" in content
    assert "allow:" in content


@pytest.mark.asyncio
async def test_init_文件已存在不覆盖(
    policy, renderer, app_config, tmp_path
) -> None:
    template = tmp_path / ".mewcode" / "permissions.yaml"
    template.parent.mkdir()
    template.write_text("# my custom\n", encoding="utf-8")

    await dispatch(
        "/permissions init", _make_ctx(policy, renderer, app_config)
    )
    # 内容未变
    assert template.read_text(encoding="utf-8") == "# my custom\n"


@pytest.mark.asyncio
async def test_init_加gitignore(
    policy, renderer, app_config, tmp_path
) -> None:
    """spec AC20：init 后 .gitignore 含本地规则文件路径。"""
    await dispatch(
        "/permissions init", _make_ctx(policy, renderer, app_config)
    )
    gi = tmp_path / ".gitignore"
    assert gi.exists()
    assert ".mewcode/permissions.local.yaml" in gi.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_init_不重复加gitignore(
    policy, renderer, app_config, tmp_path
) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text(".mewcode/permissions.local.yaml\n", encoding="utf-8")

    await dispatch(
        "/permissions init", _make_ctx(policy, renderer, app_config)
    )
    content = gi.read_text(encoding="utf-8")
    # 只出现一次
    assert content.count(".mewcode/permissions.local.yaml") == 1


# ---------- 未知子命令 ----------


@pytest.mark.asyncio
async def test_未知子命令(policy, renderer, app_config) -> None:
    await dispatch(
        "/permissions wtf", _make_ctx(policy, renderer, app_config)
    )
    joined = "\n".join(renderer.infos)
    assert "未知子命令" in joined or "用法" in joined


# ---------- policy 为 None 时不崩 ----------


@pytest.mark.asyncio
async def test_policy_None_提示未启用(renderer, app_config) -> None:
    ctx = CommandContext(
        session=_StubSession(),  # type: ignore[arg-type]
        app_config=app_config,
        args=[],
        renderer=renderer,  # type: ignore[arg-type]
        policy=None,
    )
    await dispatch("/permissions show", ctx)
    joined = "\n".join(renderer.infos)
    assert "权限系统未启用" in joined or "未注入" in joined
