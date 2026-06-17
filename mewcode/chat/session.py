"""单次进程内的会话状态。

Session 是整个 REPL 的"状态容器"——消息历史、当前 Provider、当前
供应商名、thinking 开关全部集中在此。命令层、REPL 层、对话引擎层
都通过 Session 对象读写状态，不绕过它直接访问内部字段。

第二阶段（T20）升级：
- append_user_text(text)：用户输入的纯文本消息
- append_assistant(blocks)：assistant 消息含混合块（text/thinking/tool_use）
- append_tool_results(results)：以 user 角色追加 tool_results 消息
- 删除旧的 append_user / append_assistant(text) 方法

注：spec 规定本阶段不持久化对话历史，所以 Session 只在单进程内活着，
进程退出后所有内容消失。
"""

from dataclasses import dataclass, field

from mewcode.providers import (
    ContentBlock,
    Message,
    Provider,
    ToolResultBlock,
)


@dataclass
class Session:
    """会话状态。可变。

    Attributes:
        provider:              当前生效的 Provider 实例。
        messages:              按时间顺序的消息历史。
        thinking_enabled:      extended thinking 开关，默认关闭（spec Q4）。
        current_provider_name: 当前供应商在 AppConfig.providers 中的 key，
            用于 /providers 命令标记当前生效项、/provider <name> 切换时
            同步记录。
        system_prompt:         系统提示（spec 第二阶段引入）。启动时一次
            构造，每次 LLM 请求都带上。空字符串表示不发送 system 字段。
    """

    provider: Provider
    messages: list[Message] = field(default_factory=list)
    thinking_enabled: bool = False
    current_provider_name: str = ""
    system_prompt: str = ""

    def append_user_text(self, text: str) -> None:
        """把一条用户文本消息追加到历史。"""
        self.messages.append(Message.text("user", text))

    def append_assistant(self, blocks: list[ContentBlock]) -> None:
        """把一条 assistant 消息追加到历史。

        blocks 可包含 TextBlock / ThinkingBlock / ToolUseBlock 任意混合。
        被中断（Ctrl+C）的回复不应调用此方法（spec N5：中断不进历史）；
        chat.run_turn 负责正确判断时机。
        """
        self.messages.append(Message(role="assistant", content=list(blocks)))

    def append_tool_results(self, results: list[ToolResultBlock]) -> None:
        """把一组工具结果以 user 角色追加到历史。

        spec F14：所有 tool_use 执行完成后，工具结果按对应 tool_use_id
        包成 ToolResultBlock 列表，作为一条 user 消息追加；随后发起
        Round 2 LLM 请求。
        """
        self.messages.append(Message.tool_results(results))

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
