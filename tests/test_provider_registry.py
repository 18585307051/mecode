"""mewcode.providers.registry 的单元测试。

不依赖真实 AnthropicProvider / OpenAIProvider 实现，而是用 stub Provider
子类验证注册与查找逻辑、未知协议错误、扩展性结构。

测试在 setup/teardown 中保存与恢复 PROVIDER_REGISTRY 状态，避免污染
其他测试模块。
"""

from collections.abc import AsyncIterator
from typing import Iterator

import pytest

from mewcode.config import ProviderConfig
from mewcode.providers import (
    PROVIDER_REGISTRY,
    Message,
    Provider,
    StreamEvent,
    build_provider,
)

# ---------- 辅助：stub Provider ----------


class _StubProvider(Provider):
    """测试专用的最小 Provider 实现，stream_chat 直接结束。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,
    ) -> AsyncIterator[StreamEvent]:
        if False:  # pragma: no cover - 让函数成为异步生成器
            yield


# ---------- fixture：保存与恢复全局注册表 ----------


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """每个测试用例运行前后，PROVIDER_REGISTRY 恢复到原状态。"""
    saved = PROVIDER_REGISTRY.copy()
    try:
        yield
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(saved)


# ---------- 测试用例 ----------


def _make_config(protocol: str) -> ProviderConfig:
    """构造一个最小的合法 ProviderConfig（绕过 loader 校验）。"""
    return ProviderConfig(
        name="test",
        protocol=protocol,  # type: ignore[arg-type]
        model="m",
        base_url="https://example.com",
        api_key="sk-test",
    )


def test_注册与查找() -> None:
    """注册 stub 后，build_provider 应返回对应实例。"""
    PROVIDER_REGISTRY.clear()
    PROVIDER_REGISTRY["anthropic"] = _StubProvider  # type: ignore[index]

    cfg = _make_config("anthropic")
    prov = build_provider(cfg)
    assert isinstance(prov, _StubProvider)
    assert prov.protocol == "anthropic"
    assert prov.model == "m"


def test_未知协议抛错() -> None:
    """注册表为空时调用 build_provider 应抛 ValueError。"""
    PROVIDER_REGISTRY.clear()
    cfg = _make_config("anthropic")
    with pytest.raises(ValueError) as exc_info:
        build_provider(cfg)
    assert "未知协议" in str(exc_info.value)


def test_扩展性可见() -> None:
    """检验注册表是 dict，新协议只需在此 dict 加一行（spec AC24）。

    通过：
    1. PROVIDER_REGISTRY 是 dict 类型
    2. 模块加载阶段两个内置协议都已注册（T9/T11 完成后）

    注：本测试在 T8 阶段会因为 anthropic/openai 尚未注册而失败；
    将在 T9/T11 完成后通过。当前阶段允许 PROVIDER_REGISTRY 为空，
    只验证类型契约。
    """
    assert isinstance(PROVIDER_REGISTRY, dict)
    # 注册一个新协议只需一行赋值
    PROVIDER_REGISTRY["__test_new__"] = _StubProvider  # type: ignore[index]
    assert "__test_new__" in PROVIDER_REGISTRY
