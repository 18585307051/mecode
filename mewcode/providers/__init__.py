"""Provider 层公共出口。

对外暴露：
- Provider 抽象基类、Message、Role
- 内容块体系（spec F16）：TextBlock / ThinkingBlock / ToolUseBlock /
  ToolResultBlock，以及 ContentBlock 联合类型
- 流式事件类型（含 ToolUse 三事件）+ StreamEvent 联合类型
- 五种 Provider 错误类型
- 协议分发表 PROVIDER_REGISTRY 与构造工厂 build_provider
"""

from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import Message, Provider, Role
from mewcode.providers.blocks import (
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.providers.errors import (
    AuthError,
    HTTPStatusError,
    NetworkError,
    ProviderError,
    StreamParseError,
)
from mewcode.providers.events import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
)
from mewcode.providers.openai import OpenAIProvider
from mewcode.providers.registry import PROVIDER_REGISTRY, build_provider

# 注册内置协议实现到分发表。新增协议时在此处加一行即可（spec F8）。
PROVIDER_REGISTRY["anthropic"] = AnthropicProvider
PROVIDER_REGISTRY["openai"] = OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "AuthError",
    "ContentBlock",
    "Done",
    "HTTPStatusError",
    "Message",
    "NetworkError",
    "OpenAIProvider",
    "PROVIDER_REGISTRY",
    "Provider",
    "ProviderError",
    "Role",
    "StreamEvent",
    "StreamParseError",
    "TextBlock",
    "TextDelta",
    "ThinkingBlock",
    "ThinkingDelta",
    "ToolResultBlock",
    "ToolUseBlock",
    "ToolUseEnd",
    "ToolUseInputDelta",
    "ToolUseStart",
    "Usage",
    "build_provider",
]
