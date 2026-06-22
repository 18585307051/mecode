"""第八阶段总入口：协调第一层 + 第二层 + 状态管理（spec F2 / F8 / F9 / F15 / D5 / D10 / D11）。

Compactor 是单实例（main.py 启动时构造一个），由 chat.engine 与
commands.builtin 共同持有引用。
"""

from dataclasses import dataclass, field
from pathlib import Path

from mewcode.compaction.lightweight import StashEvent, apply_lightweight
from mewcode.compaction.summarizer import (
    build_boundary_message,
    compute_keep_boundary,
    summarize_async,
)
from mewcode.compaction.tokens import estimate_tokens


# ---------- 阈值缓冲（spec F8 / Q5 / D5） ----------

# 自动触发固定阈值。
# 用户反馈：默认按 context_window - 13K 触发太晚，调成 5000 tokens，
# 便于长任务早压缩，也方便本地验证压缩链路。
AUTO_COMPACT_THRESHOLD = 5000

# 手动触发的安全余量（用户已下决定，保留作后续配置使用）
MANUAL_BUFFER = 3000

# 未知模型的保守默认 context window
DEFAULT_CONTEXT_WINDOW = 128000

# 已知模型 context window 映射（模糊匹配）
_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-3-5-sonnet": 200000,
    "claude-3-7-sonnet": 200000,
    "claude-sonnet-4": 200000,
    "claude-3-opus": 200000,
    "claude-3-haiku": 200000,
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "deepseek-v4": 128000,
    "deepseek-v3": 128000,
    "deepseek-chat": 128000,
}


def _detect_window(model: str) -> int:
    """根据模型名匹配 context window；未知模型给保守默认值。"""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    model_lc = model.lower()
    for key, window in _CONTEXT_WINDOWS.items():
        if key in model_lc:
            return window
    return DEFAULT_CONTEXT_WINDOW


# ---------- 数据结构 ----------


@dataclass
class CompactStats:
    """before_request / compact_now 返回的统计。"""

    stash_events: list[StashEvent] = field(default_factory=list)
    summary_triggered: bool = False
    summary_succeeded: bool = False
    summary_error: str | None = None
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    compacted_message_count: int = 0


# ---------- Compactor 类 ----------


class Compactor:
    """两层压缩协调器（spec F1 全图）。

    本类无状态——所有状态都存在 Session 上。每次调用都从 session 读
    last_usage / failures / disabled。这样测试容易（无需重置 Compactor）。
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    # ---- 状态查询 ----

    def get_window(self, model: str) -> int:
        """取模型 context window。"""
        return _detect_window(model)

    # ---- 主入口 ----

    async def before_request(
        self,
        session,
        manual: bool = False,
        instruction: str = "",
    ) -> CompactStats:
        """API 请求前的压缩检查链（spec F9）。

        Args:
            session: 当前 Session
            manual: True 表示来自 /compact 命令
            instruction: 仅 manual=True 时有效；用户附带的指示

        Returns:
            CompactStats，记录所有发生的事件
        """
        stats = CompactStats()

        # ---- 第一层：轻量预防（始终跑，不受熔断影响）----
        events = apply_lightweight(
            session.messages, self._cwd, session.session_id or "default"
        )
        stats.stash_events = events

        # ---- 估算总 token ----
        estimated = estimate_tokens(
            session.messages,
            session.last_usage_input_tokens,
            session.last_anchor_message_count,
        )
        stats.estimated_tokens_before = estimated

        # ---- 熔断判断（仅自动触发受影响）----
        if not manual and getattr(session, "compaction_disabled", False):
            stats.estimated_tokens_after = estimated
            return stats

        # ---- 第二层判定 ----
        if manual:
            should_compact = True  # /compact 必触发
        else:
            should_compact = estimated >= AUTO_COMPACT_THRESHOLD

        if not should_compact:
            stats.estimated_tokens_after = estimated
            return stats

        stats.summary_triggered = True

        # ---- 第二层执行 ----
        keep_start = compute_keep_boundary(session.messages)
        if keep_start <= 0:
            # 无可压缩前缀（历史太短 / 全是 user 输入找不到合适边界）
            stats.summary_error = "no_compactable_prefix"
            stats.estimated_tokens_after = estimated
            if not manual:
                self._tick_failure(session)
            return stats

        early = list(session.messages[:keep_start])
        recent = list(session.messages[keep_start:])

        try:
            summary_text = await summarize_async(
                session.provider, early, instruction
            )
        except Exception as e:
            stats.summary_error = f"exception: {type(e).__name__}: {e}"
            if not manual:
                self._tick_failure(session)
            stats.estimated_tokens_after = estimated
            return stats

        if summary_text is None:
            stats.summary_error = "llm_failed_or_unparsable"
            if not manual:
                self._tick_failure(session)
            stats.estimated_tokens_after = estimated
            return stats

        # ---- 替换 messages ----
        boundary_msg = build_boundary_message(summary_text, len(early))
        session.messages = [boundary_msg, *recent]

        # 重置锚点（下次响应重新锚定）
        session.last_usage_input_tokens = 0
        session.last_anchor_message_count = 0

        # 成功后重置 failures
        if not manual:
            session.compaction_failures = 0

        stats.summary_succeeded = True
        stats.compacted_message_count = len(early)
        stats.estimated_tokens_after = estimate_tokens(session.messages, 0, 0)
        return stats

    async def compact_now(
        self, session, instruction: str = ""
    ) -> CompactStats:
        """/compact 命令入口（spec F16）。"""
        return await self.before_request(
            session, manual=True, instruction=instruction
        )

    def after_response(self, session, usage) -> None:
        """API 响应完成后更新锚点（spec F2）。

        Args:
            session: 当前 Session
            usage: Provider Usage 事件，需含 input_tokens
        """
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        if input_tokens > 0:
            session.last_usage_input_tokens = input_tokens
            session.last_anchor_message_count = len(session.messages)

    def reset_state(self, session) -> None:
        """清空压缩状态（spec F15 / 由 /clear / switch_provider 调用）。"""
        session.last_usage_input_tokens = 0
        session.last_anchor_message_count = 0
        session.compaction_failures = 0
        session.compaction_disabled = False

    # ---- 内部 ----

    @staticmethod
    def _tick_failure(session) -> None:
        """自动触发失败时累加计数 + 达 3 次熔断。"""
        session.compaction_failures = getattr(
            session, "compaction_failures", 0
        ) + 1
        if session.compaction_failures >= 3:
            session.compaction_disabled = True
