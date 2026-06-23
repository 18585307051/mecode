"""单次进程内的会话状态。

Session 是整个 REPL 的"状态容器"——消息历史、当前 Provider、当前
供应商名、thinking 开关全部集中在此。命令层、REPL 层、对话引擎层
都通过 Session 对象读写状态，不绕过它直接访问内部字段。

第二阶段（T20）升级：
- append_user_text(text)：用户输入的纯文本消息
- append_assistant(blocks)：assistant 消息含混合块（text/thinking/tool_use）
- append_tool_results(results)：以 user 角色追加 tool_results 消息
- 删除旧的 append_user / append_assistant(text) 方法

第九阶段升级（spec F4 / F6）：
- 增加 archive 字段：每次 append_xxx 后自动追加写 JSONL。
- 增加 restored_needs_compaction_check 标记：恢复会话后第一次请求
  应额外触发一次压缩估算。
- clear() / switch_provider() 调用 archive.rotate() 换发新 session_id，
  避免清空历史后仍写入旧 JSONL。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

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
        mode:                  工作模式（spec 第三阶段引入）：
            - "do"   执行模式，全部 6 个工具可用
            - "plan" 计划模式，只读工具（read/glob/search）可用
            /clear 与 /provider 切换时重置为 "do"。
        plan_turn_count:       自切换到 plan 模式后已经发起的轮数（1-based）。
            spec F7 / D7 第四阶段引入：用于决定 reminder 注入完整版还是
            精简版。Do Mode 下保持 0；切到 Plan 后每次 _consume_round 递
            增；切回 Do 或 /clear / /provider 时重置为 0。
        archive:               第九阶段 SessionArchive 实例。None 时不
            持久化；非 None 时每次 append_xxx 自动追加写 JSONL。
        restored_needs_compaction_check: 第九阶段 F9：标记本会话是
            从历史 JSONL 恢复而来，且本次启动后还没做过压缩估算。
            run_turn 在第一次请求前消费此标记触发额外压缩检查。
    """

    provider: Provider
    messages: list[Message] = field(default_factory=list)
    thinking_enabled: bool = False
    current_provider_name: str = ""
    system_prompt: str = ""
    mode: Literal["do", "plan"] = "do"
    plan_turn_count: int = 0
    # 第八阶段：上下文压缩状态（spec F2）
    last_usage_input_tokens: int = 0
    last_anchor_message_count: int = 0
    compaction_failures: int = 0
    compaction_disabled: bool = False
    session_id: str = ""
    # 第九阶段：会话存档与恢复后压缩
    archive: Any = None
    restored_needs_compaction_check: bool = False

    # ---- 内部：持久化 hook ----

    def _persist_last(self) -> None:
        """把 messages[-1] 追加写到 archive（第九阶段 F4）。

        archive 为 None 或 session_id 为空时直接跳过。写入异常由
        archive.append_message 内部 warning，不抛出，避免影响主对话。
        """
        if self.archive is None or not self.session_id or not self.messages:
            return
        try:
            self.archive.append_message(self.session_id, self.messages[-1])
        except Exception as e:
            # 双重兜底：archive 内部已 try/except；此处再防御一次
            print(f"⚠️ 会话存档异常（已忽略）：{e}")

    def append_user_text(self, text: str) -> None:
        """把一条用户文本消息追加到历史。"""
        self.messages.append(Message.text("user", text))
        self._persist_last()

    def append_assistant(self, blocks: list[ContentBlock]) -> None:
        """把一条 assistant 消息追加到历史。

        blocks 可包含 TextBlock / ThinkingBlock / ToolUseBlock 任意混合。
        被中断（Ctrl+C）的回复不应调用此方法（spec N5：中断不进历史）；
        chat.run_turn 负责正确判断时机。
        """
        self.messages.append(Message(role="assistant", content=list(blocks)))
        self._persist_last()

    def append_tool_results(self, results: list[ToolResultBlock]) -> None:
        """把一组工具结果以 user 角色追加到历史。

        spec F14：所有 tool_use 执行完成后，工具结果按对应 tool_use_id
        包成 ToolResultBlock 列表，作为一条 user 消息追加；随后发起
        Round 2 LLM 请求。
        """
        self.messages.append(Message.tool_results(results))
        self._persist_last()

    def _rotate_session_id(self) -> None:
        """换发新 session_id（archive 存在时）。"""
        if self.archive is not None and hasattr(self.archive, "rotate"):
            try:
                self.archive.rotate(self)
            except Exception:
                pass

    def clear(self) -> None:
        """清空消息历史。供 /clear 命令与 switch_provider 调用。

        同时重置 mode 为 "do"（spec F6）、plan_turn_count 为 0
        （spec F7）以及第八阶段的压缩状态（spec F15）。
        第九阶段：换发新的 session_id，避免后续消息继续写入旧 JSONL。
        """
        self.messages.clear()
        self.mode = "do"
        self.plan_turn_count = 0
        # 第八阶段：重置压缩状态
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False
        # 第九阶段：清空后认为不再有"恢复后首次压缩"待办
        self.restored_needs_compaction_check = False
        self._rotate_session_id()

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
        self.mode = "do"
        self.plan_turn_count = 0
        # 第八阶段：切 provider 时重置压缩状态
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False
        self.restored_needs_compaction_check = False
        self._rotate_session_id()
