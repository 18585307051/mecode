"""Provider 抽象基类与会话消息类型。

每种 wire protocol 一个具体 Provider 实现（按 spec 方案 1）。
配置中的 protocol 字段决定加载哪个 Provider。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from mewcode.config import Protocol, ProviderConfig
from mewcode.providers.events import StreamEvent

# 角色字面量。本阶段只支持 user / assistant；后续接入工具调用时再扩展。
Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    """会话历史中的一条消息。

    本阶段 content 只支持纯文本字符串。后续阶段加入 tool_use / tool_result
    等结构化内容时，会扩展为 list[ContentBlock] 形式。
    """

    role: Role
    content: str


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
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话。

        Args:
            messages: 完整的会话历史（包含本轮 user 消息），按时间顺序。
            thinking: 是否启用 extended thinking；openai 协议下应忽略。

        Returns:
            异步迭代器，按"事件流约定"产出 StreamEvent。

        Raises:
            ProviderError: 及其子类（NetworkError、HTTPStatusError、
                AuthError、StreamParseError 等）。
        """
        ...
