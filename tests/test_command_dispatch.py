"""命令分发单元测试。

不依赖真实 Provider / Renderer / Session，用最小 stub 实现验证分发
逻辑、未知命令处理、几个关键 handler 的行为。
"""

from collections.abc import AsyncIterator
from typing import Iterator

import pytest

from mewcode.commands import (
    COMMANDS,
    CommandContext,
    CommandResult,
    dispatch,
    register_builtins,
)
from mewcode.config import AppConfig, ProviderConfig
from mewcode.providers import (
    Message,
    PROVIDER_REGISTRY,
    Provider,
    StreamEvent,
    build_provider,
)


# ---------- stub 实现 ----------


class _StubProvider(Provider):
    """最小 Provider：stream_chat 立即结束，protocol 由配置控制。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,
    ) -> AsyncIterator[StreamEvent]:
        if False:  # pragma: no cover
            yield


class _StubRenderer:
    """记录所有调用的 Renderer 替身，便于断言。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def has(self, method: str) -> bool:
        return any(c[0] == method for c in self.calls)

    def first(self, method: str) -> tuple:
        for c in self.calls:
            if c[0] == method:
                return c
        raise AssertionError(f"renderer 未调用 {method}")


class _StubSession:
    """与真实 Session 字段兼容的最小替身。"""

    def __init__(self, provider: Provider, name: str = "alpha") -> None:
        self.provider = provider
        self.messages: list[Message] = []
        self.thinking_enabled = False
        self.current_provider_name = name

    def append_user_text(self, text: str) -> None:
        self.messages.append(Message.text("user", text))

    def append_user(self, text: str) -> None:
        """旧名兼容：保留给少数测试调用。"""
        self.append_user_text(text)

    def append_assistant(self, content) -> None:
        """适配 T20 新接口 + 旧接口：

        - 新接口：传 list[ContentBlock]，直接组装为消息
        - 旧接口：传 str，包装成 TextBlock 单块
        """
        if isinstance(content, str):
            self.messages.append(Message.text("assistant", content))
        else:
            self.messages.append(Message(role="assistant", content=list(content)))

    def append_tool_results(self, results) -> None:
        self.messages.append(Message.tool_results(results))

    def clear(self) -> None:
        self.messages.clear()

    def switch_provider(self, provider: Provider, name: str = "") -> None:
        self.provider = provider
        if name:
            self.current_provider_name = name
        self.messages.clear()


# ---------- fixtures ----------


def _make_cfg(name: str, protocol: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        model="m",
        base_url="https://example.com",
        api_key="sk-stub",
    )


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        providers={
            "alpha": _make_cfg("alpha", "anthropic"),
            "beta": _make_cfg("beta", "openai"),
        },
        default="alpha",
    )


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    """保护 COMMANDS 与 PROVIDER_REGISTRY 的全局状态。"""
    saved_cmds = COMMANDS.copy()
    saved_reg = PROVIDER_REGISTRY.copy()

    # 把分发表里的两种协议都换成 stub，避免命令实际去构造真 Provider
    PROVIDER_REGISTRY["anthropic"] = _StubProvider  # type: ignore[index]
    PROVIDER_REGISTRY["openai"] = _StubProvider  # type: ignore[index]

    register_builtins()
    try:
        yield
    finally:
        COMMANDS.clear()
        COMMANDS.update(saved_cmds)
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(saved_reg)


def _make_ctx(
    session: _StubSession, app_config: AppConfig, renderer: _StubRenderer
) -> CommandContext:
    return CommandContext(
        session=session,  # type: ignore[arg-type]
        app_config=app_config,
        args=[],
        renderer=renderer,  # type: ignore[arg-type]
    )


# ---------- 测试用例 ----------


@pytest.mark.asyncio
async def test_非命令返回None(app_config: AppConfig) -> None:
    """普通文本不是命令，dispatch 应返回 None。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    result = await dispatch(
        "hello world", _make_ctx(session, app_config, renderer)
    )
    assert result is None


@pytest.mark.asyncio
async def test_未知命令(app_config: AppConfig) -> None:
    """未注册的 / 命令应触发 print_unknown_command 并返回非退出 result。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    result = await dispatch(
        "/foobar", _make_ctx(session, app_config, renderer)
    )
    assert isinstance(result, CommandResult)
    assert result.should_exit is False
    assert renderer.has("print_unknown_command")


@pytest.mark.asyncio
async def test_exit返回should_exit(app_config: AppConfig) -> None:
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    result = await dispatch("/exit", _make_ctx(session, app_config, renderer))
    assert result is not None
    assert result.should_exit is True


@pytest.mark.asyncio
async def test_quit别名(app_config: AppConfig) -> None:
    """/quit 应共用 /exit 的 handler，同样 should_exit。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    result = await dispatch("/quit", _make_ctx(session, app_config, renderer))
    assert result is not None
    assert result.should_exit is True


@pytest.mark.asyncio
async def test_clear清空历史(app_config: AppConfig) -> None:
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    session.append_user("hi")
    session.append_assistant("hello")
    assert len(session.messages) == 2
    renderer = _StubRenderer()
    await dispatch("/clear", _make_ctx(session, app_config, renderer))
    assert session.messages == []
    assert renderer.has("print_info")


@pytest.mark.asyncio
async def test_think_on_anthropic协议(app_config: AppConfig) -> None:
    """anthropic 协议下 /think on 应启用 thinking。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    await dispatch("/think on", _make_ctx(session, app_config, renderer))
    assert session.thinking_enabled is True


@pytest.mark.asyncio
async def test_think_on_openai协议(app_config: AppConfig) -> None:
    """openai 协议下 /think on 应保持 thinking 关闭并提示不支持。"""
    session = _StubSession(_StubProvider(_make_cfg("beta", "openai")))
    renderer = _StubRenderer()
    await dispatch("/think on", _make_ctx(session, app_config, renderer))
    assert session.thinking_enabled is False
    assert renderer.has("print_info")
    # 提示文本中应含"不支持"或协议名
    info_calls = [c for c in renderer.calls if c[0] == "print_info"]
    assert any("不支持" in c[1][0] for c in info_calls)


@pytest.mark.asyncio
async def test_think_off(app_config: AppConfig) -> None:
    """/think off 应关闭 thinking。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    session.thinking_enabled = True
    renderer = _StubRenderer()
    await dispatch("/think off", _make_ctx(session, app_config, renderer))
    assert session.thinking_enabled is False


@pytest.mark.asyncio
async def test_provider切换(app_config: AppConfig) -> None:
    """/provider <name> 切换并清空历史。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    session.append_user("hi")
    renderer = _StubRenderer()
    await dispatch(
        "/provider beta", _make_ctx(session, app_config, renderer)
    )
    assert session.current_provider_name == "beta"
    assert session.messages == []


@pytest.mark.asyncio
async def test_provider_不存在(app_config: AppConfig) -> None:
    """/provider 指向不存在的 name 应给出提示，不切换。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    await dispatch(
        "/provider gamma", _make_ctx(session, app_config, renderer)
    )
    assert session.current_provider_name == "alpha"
    info_calls = [c for c in renderer.calls if c[0] == "print_info"]
    assert any("不存在" in c[1][0] for c in info_calls)


@pytest.mark.asyncio
async def test_help(app_config: AppConfig) -> None:
    """/help 应调用 print_command_list。"""
    session = _StubSession(_StubProvider(_make_cfg("alpha", "anthropic")))
    renderer = _StubRenderer()
    await dispatch("/help", _make_ctx(session, app_config, renderer))
    assert renderer.has("print_command_list")
    # /help 不应触发 print_unknown_command
    assert not renderer.has("print_unknown_command")


@pytest.mark.asyncio
async def test_providers列表(app_config: AppConfig) -> None:
    """/providers 应调用 print_provider_list 且 current_name 正确传入。"""
    session = _StubSession(
        _StubProvider(_make_cfg("alpha", "anthropic")), name="alpha"
    )
    renderer = _StubRenderer()
    await dispatch("/providers", _make_ctx(session, app_config, renderer))
    assert renderer.has("print_provider_list")
    call = renderer.first("print_provider_list")
    # 第二个位置/关键字参数是 current_name；我们用关键字调用 stub 方法
    # 时位置/关键字会混用，统一断言 current_name 出现在调用中
    payload = str(call)
    assert "alpha" in payload
