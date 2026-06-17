"""mewcode.yaml 加载器。

负责把磁盘上的 YAML 文件读取、解析、字段校验后转换为 AppConfig 数据
对象。校验失败时抛出 ConfigError 子类，错误信息中明确指出问题字段。

设计原则（与 spec 一致）：
- 不读环境变量做 fallback。
- 不为缺失字段填充默认值（除非 spec 明确允许）。
- 校验逻辑全部手写，不引入 pydantic 等 schema 库。
"""

from pathlib import Path
from typing import Any

import yaml

from mewcode.config.errors import (
    ConfigFieldError,
    ConfigFileNotFound,
    ConfigFormatError,
)
from mewcode.config.models import AppConfig, ProviderConfig

# 合法的 protocol 取值集合
_VALID_PROTOCOLS = {"anthropic", "openai"}

# 单个供应商条目必须包含的字段名
_REQUIRED_PROVIDER_FIELDS = ("protocol", "model", "base_url", "api_key")


def load(path: str | Path) -> AppConfig:
    """读取并校验 mewcode.yaml，返回 AppConfig。

    Args:
        path: 配置文件路径，字符串或 Path 均可。

    Returns:
        AppConfig 数据对象。

    Raises:
        ConfigFileNotFound: 文件不存在。
        ConfigFormatError:  YAML 解析失败或顶层不是 dict。
        ConfigFieldError:   必需字段缺失、字段值非法、default 指向不存在的供应商等。
    """
    path = Path(path)

    # 步骤 a：路径存在性检查
    if not path.exists():
        raise ConfigFileNotFound(f"未找到配置文件: {path}")

    # 步骤 b：YAML 解析
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigFormatError(f"YAML 解析失败: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigFormatError(
            f"配置文件顶层必须是字典结构，当前类型: {type(raw).__name__}"
        )

    # 步骤 c：顶层字段检查
    if "default" not in raw:
        raise ConfigFieldError("配置文件缺少顶层字段: default")
    if "providers" not in raw:
        raise ConfigFieldError("配置文件缺少顶层字段: providers")

    default = raw["default"]
    providers_raw = raw["providers"]

    if not isinstance(default, str) or not default.strip():
        raise ConfigFieldError("顶层字段 default 必须是非空字符串")

    # 步骤 d：providers 结构检查
    if not isinstance(providers_raw, dict):
        raise ConfigFieldError(
            f"providers 必须是字典结构，当前类型: {type(providers_raw).__name__}"
        )
    if not providers_raw:
        raise ConfigFieldError("providers 不能为空，至少需要声明一个供应商")

    # 步骤 e：逐个校验 provider 条目
    providers: dict[str, ProviderConfig] = {}
    for name, entry in providers_raw.items():
        providers[name] = _build_provider_config(name, entry)

    # 步骤 f：default 必须存在
    if default not in providers:
        raise ConfigFieldError(
            f"default 指向不存在的供应商: {default}（已声明的供应商: "
            f"{', '.join(sorted(providers.keys()))}）"
        )

    # 步骤 g：构造并返回
    return AppConfig(providers=providers, default=default)


def _build_provider_config(name: str, entry: Any) -> ProviderConfig:
    """校验并构造单个 ProviderConfig。

    Args:
        name:  供应商名（YAML key）。
        entry: 供应商条目原始 dict。

    Raises:
        ConfigFieldError: 字段缺失或非法。
    """
    if not isinstance(entry, dict):
        raise ConfigFieldError(
            f"供应商 '{name}' 配置必须是字典结构，当前类型: {type(entry).__name__}"
        )

    # 必需字段非空字符串校验
    for field in _REQUIRED_PROVIDER_FIELDS:
        if field not in entry:
            raise ConfigFieldError(f"供应商 '{name}' 缺少字段: {field}")
        value = entry[field]
        if not isinstance(value, str) or not value.strip():
            raise ConfigFieldError(
                f"供应商 '{name}' 的字段 '{field}' 必须是非空字符串"
            )

    # protocol 取值校验
    protocol = entry["protocol"]
    if protocol not in _VALID_PROTOCOLS:
        raise ConfigFieldError(
            f"供应商 '{name}' 的 protocol 取值非法: {protocol}"
            f"（合法取值: {', '.join(sorted(_VALID_PROTOCOLS))}）"
        )

    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        model=entry["model"],
        base_url=entry["base_url"],
        api_key=entry["api_key"],
    )
