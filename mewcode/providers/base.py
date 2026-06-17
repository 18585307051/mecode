"""Provider 抽象基类与会话消息类型。

每种 wire protocol 一个具体 Provider 实现（按 spec 方案 1）。
配置中的 protocol 字段决定加载哪个 Provider。

第二阶段升级（破坏性）：
- Message.content 由 `str` 升级为 `list[ContentBlock]`，统一承载文本、
  思考、工具调用、工具结果四种块。
- 提供 `Message.text(role, content)` 与 `Message.tool_results(results)`
  两个工厂方法，简化纯文本与工具回填的常见构造场景。
- stream_chat 签名增加 `tools_format` 参数，由 chat 层在每次请求前从
  ToolRegistry 取出已格式化的工具元信息列表注入；Provider 自身不持有
  ToolRegistry（D6 决策）。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from mewcode.config import Protocol, ProviderConfig
from mewcode.providers.blocks import (
    ContentBlock,
    TextBlock,
    ToolResultBlock,
)
from mewcode.providers.events import StreamEvent

# 角色字面量。本阶段只支持 user / assistant；后续接入工具调用结果时
# OpenAI 协议下会出现 role="tool" 的消息——但这是 Provider 在序列化
# 历史时按各家协议格式临时扩展的，不影响内部 Message.role 类型。
Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    """会话历史中的一条消息。

    第二阶段破坏性升级：content 从 `str` 改为 `list[ContentBlock]`，
    每条消息可以是文本、思考、工具调用、工具结果块的任意混合。

    旧场景的纯文本消息可通过 `Message.text(role, content)` 工厂方法
    构造，避免手写 `[TextBlock(text=...)]`。
    """

    role: Role
    content: list[ContentBlock]

    @classmethod
    def text(cls, role: Role, content: str) -> "Message":
        """便捷构造：纯文本消息，content 包装成单个 TextBlock。"""
        return cls(role=role, content=[TextBlock(text=content)])

    @classmethod
    def tool_results(cls, results: list[ToolResultBlock]) -> "Message":
        """便捷构造：含一组 tool_result 的 user 消息。

        spec F14：工具执行完成后，所有 ToolResultBlock 按对应 tool_use_id
        关联到上一条 assistant 消息的 ToolUseBlock，作为一条 user 消息
        追加到历史，随后发起 Round 2 LLM 请求。
        """
        return cls(role="user", content=list(results))


class Provider(ABC):
    """Provider 抽象基类。

    每种 wire protocol 一个具体子类。子类只需实现 `stream_chat`，
    并把后端返回的原始 SSE 流映射为统一的 StreamEvent 序列。
    """

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    @property
    def protocol(self) -> Protocol:
        """当前 Provider 走的 wire protocol。"""
        return self._config.protocol

    @property
    def model(self) -> str:
        """当前使用的模型名。"""
        return self._config.model

    @abstractmethod
    def stream_chat(
        self,
        messages: list[Message],
        thinking: bool,
        tools_format: list[dict] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话。

        Args:
            messages:     完整的会话历史（包含本轮 user 消息），按时间顺序。
            thinking:     是否启用 extended thinking；openai 协议下应忽略。
            tools_format: 已按当前协议格式序列化的工具元信息列表；
                None 或空列表表示本次请求不携带 tools 字段。
                由 chat 层从 ToolRegistry.to_xxx_format() 取出后传入。
            system:       系统提示（spec 第二阶段引入）。Anthropic 协议
                走请求体顶层 `system` 字段；OpenAI 协议在 messages
                头部插入 role=system 消息。None 时不携带。

        Returns:
            异步迭代器，按"事件流约定"产出 StreamEvent。

        Raises:
            ProviderError: 及其子类（NetworkError、HTTPStatusError、
                AuthError、StreamParseError 等）。
        """
        ...
