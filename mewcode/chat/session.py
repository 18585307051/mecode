"""单次进程内的会话状态。

Session 是整个 REPL 的"状态容器"——消息历史、当前 Provider、当前
供应商名、thinking 开关全部集中在此。命令层、REPL 层、对话引擎层
都通过 Session 对象读写状态，不绕过它直接访问内部字段。

注：spec 规定本阶段不持久化对话历史，所以 Session 只在单进程内活着，
进程退出后所有内容消失。
"""

from dataclasses import dataclass, field

from mewcode.providers import Message, Provider


@dataclass
class Session:
    """会话状态。可变。

    Attributes:
        provider:              当前生效的 Provider 实例。
        messages:              按时间顺序的消息历史。
        thinking_enabled:      extended thinking 开关，默认关闭（spec Q4）。
        current_provider_name: 当前供应商在 AppConfig.providers 中的 key，
            用于 /providers 命令标记当前生效项、/provider <name> 切换时
            同步记录。这是 task 阶段对 plan.md Session 定义的扩展，理由
            见 docs/02/task.md 的 T14。
    """

    provider: Provider
    messages: list[Message] = field(default_factory=list)
    thinking_enabled: bool = False
    current_provider_name: str = ""

    def append_user(self, text: str) -> None:
        """把一条用户消息追加到历史。"""
        self.messages.append(Message(role="user", content=text))

    def append_assistant(self, text: str) -> None:
        """把一条 AI 回复追加到历史。

        注意：被中断（Ctrl+C）的回复不应调用此方法，以满足 spec N5
        "中断不进历史"语义。调用方（chat.run_turn）负责正确判断时机。
        """
        self.messages.append(Message(role="assistant", content=text))

    def clear(self) -> None:
        """清空消息历史。供 /clear 命令与 switch_provider 调用。"""
        self.messages.clear()

    def switch_provider(self, provider: Provider, name: str = "") -> None:
        """切换 Provider 实例并清空消息历史。

        Args:
            provider: 新的 Provider 实例。
            name:     新供应商名（可选）。提供时同步更新
                current_provider_name；为空时保持原值不变。
        """
        self.provider = provider
        if name:
            self.current_provider_name = name
        self.messages.clear()
