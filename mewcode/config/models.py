"""配置层数据模型。

`AppConfig` 表示整份 mewcode.yaml 解析后的结果；`ProviderConfig` 表示
单个供应商条目。两者均为不可变数据类，由 loader 一次性构造、其他模块
只读使用。
"""

from dataclasses import dataclass
from typing import Literal

# 协议字面量类型。新增协议时只需在此处加一项，注册表（providers/registry.py）
# 中再加一行映射即可。
Protocol = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class ProviderConfig:
    """单个供应商的配置条目，对应 mewcode.yaml 中 providers 列表的一项。

    字段：
        name      —— 供应商名（YAML 中的 key），用于 /provider 切换、
                      /providers 列出、启动横幅展示。
        protocol  —— wire protocol，决定加载哪个 Provider 实现。
        model     —— 模型名，会原样传给后端。
        base_url  —— 请求基础 URL，不含路径部分（路径由 Provider 拼接）。
        api_key   —— 鉴权用的密钥，绝不在用户可见输出中外露（spec N9）。
    """

    name: str
    protocol: Protocol
    model: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class AppConfig:
    """整份 mewcode.yaml 解析后的结果。

    字段：
        providers —— 供应商名 → ProviderConfig 的映射。
        default   —— 启动时使用的供应商名，必须存在于 providers 中。
    """

    providers: dict[str, ProviderConfig]
    default: str
