"""协议分发表与 Provider 工厂。

新增协议（spec F8 扩展点）只需：
1. 在 mewcode/providers/ 下新建一个文件，实现 Provider 子类。
2. 在 mewcode/providers/__init__.py 中追加一行 import 与一行注册：
       PROVIDER_REGISTRY["new_protocol"] = NewProvider

REPL、配置加载、命令分发、对话状态等其他模块的代码无需修改。
"""

from mewcode.config import Protocol, ProviderConfig
from mewcode.providers.base import Provider

# 协议名 → Provider 实现类。本身是普通 dict，便于测试时临时替换。
PROVIDER_REGISTRY: dict[Protocol, type[Provider]] = {}


def build_provider(config: ProviderConfig) -> Provider:
    """根据 config.protocol 在注册表中查找并实例化对应的 Provider。

    Args:
        config: 单个供应商配置。

    Returns:
        Provider 实例。

    Raises:
        ValueError: 协议名不在注册表中。理论上 config 校验阶段已拦截
            未知协议，此处仅作最后一道防线。
    """
    cls = PROVIDER_REGISTRY.get(config.protocol)
    if cls is None:
        raise ValueError(
            f"未知协议: {config.protocol}（已注册: "
            f"{', '.join(sorted(PROVIDER_REGISTRY.keys())) or '无'}）"
        )
    return cls(config)
