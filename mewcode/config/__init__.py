"""配置层公共出口。

对外暴露：
- `load(path)`：读取并校验 mewcode.yaml，返回 AppConfig。
- `AppConfig`、`ProviderConfig`、`Protocol`：数据模型。
- 四个 `ConfigError` 子类：配置错误的分类异常。
"""

from mewcode.config.errors import (
    ConfigError,
    ConfigFieldError,
    ConfigFileNotFound,
    ConfigFormatError,
)
from mewcode.config.models import AppConfig, Protocol, ProviderConfig

# loader 在 T4 实现后启用：
from mewcode.config.loader import load  # noqa: E402

__all__ = [
    "AppConfig",
    "ConfigError",
    "ConfigFieldError",
    "ConfigFileNotFound",
    "ConfigFormatError",
    "Protocol",
    "ProviderConfig",
    "load",
]
