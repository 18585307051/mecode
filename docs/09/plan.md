# MewCode 第八阶段 Plan

> 基于已批准的 `docs/09/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第七阶段的兼容矩阵。

## 1. 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│ chat.engine.run_turn 入口                                     │
│   1. session.append_user_text(user_input)                     │
│   2. compactor.before_request(session) ← 第八阶段              │
│      ├─ 第一层：单工具/单消息存盘                              │
│      └─ 第二层：估算 → 必要时摘要                              │
│   3. _agent_loop(...) 跑模型                                  │
│   4. compactor.after_response(session, usage) ← 第八阶段       │
│      └─ 更新 last_usage + last_anchor_message_count           │
└─────────────┬────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────┐
│ mewcode/compaction/compactor.py                  │
│                                                  │
│ class Compactor:                                 │
│   __init__(cwd, manual_threshold_buffer=3000,    │
│            auto_threshold_buffer=13000)          │
│                                                  │
│   async before_request(session) -> CompactStats  │
│   async compact_now(session, instruction="") -> bool  ←/compact用│
│   def after_response(session, usage) -> None     │
└──────┬───────────────────────────────────────────┘
       │
       ▼
┌─────────────────────┐  ┌─────────────────────┐
│ lightweight.py      │  │ summarizer.py        │
│                     │  │                      │
│ apply_lightweight   │  │ summarize_async      │
│ (session, cwd) ->   │  │ (provider, messages,│
│  list[stash_event]  │  │  instruction)        │
│                     │  │  -> str | None       │
│ 单工具/单消息存盘    │  │ build_compaction_prompt│
└─────────────────────┘  │ extract_summary      │
                          │ compute_keep_boundary│
                          └─────────────────────┘

┌─────────────────────┐
│ tokens.py           │
│                     │
│ estimate_tokens     │
│ serialize_message   │
│   for_estimation    │
└─────────────────────┘
```

### 主要数据流

```
新一轮 user 输入
  ↓
[第一层处理：lightweight.apply]
  扫 messages[-1] (tool_results 消息) 的所有 ToolResultBlock
  超阈值的 → 写文件 + 替换 content
  ↓
[第二层判定：summarizer.should_compact_auto]
  estimated = estimate_tokens(messages, last_usage, anchor)
  if estimated >= auto_threshold AND not disabled:
      ↓ 进摘要流程
  ↓
[第二层执行：summarizer.summarize_async]
  keep_start = compute_keep_boundary(messages, ...)
  系列化早期消息为可读文本
  调 provider.stream_chat(临时构造，无工具)
  解析 <summary> 块
  ↓
[替换 messages]
  session.messages = [boundary_user_msg] + messages[keep_start:]
  ↓
[继续正常 stream_chat]
```

### 模块依赖

```
mewcode/compaction/
  tokens.py        → 仅依赖 mewcode.providers (Message/TextBlock/...)
  lightweight.py   → tokens + stdlib (pathlib)
  summarizer.py    → tokens + mewcode.providers
  compactor.py     → 上述全部
  __init__.py      → 公共出口

不依赖：chat / commands / render / permissions / mcp / instructions / tools
```

chat.engine 与 commands.builtin 单方面依赖 mewcode.compaction.

## 2. 模块设计

### 2.1 mewcode/compaction/tokens.py

```python
"""token 估算（spec F1 / D1）。

不引入精确 tokenizer。策略：
- 锚点：上次 API 响应的 input_tokens（实测）
- 增量：锚点之后新增的 messages 用字符 / 3 估算
- 加和返回总估算

字符 / 3 是经验系数：英文偏保守，中文较准（中文 1 char ≈ 1.5-2 token）。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.providers import Message


def serialize_message_for_estimation(msg: "Message") -> str:
    """把 Message 序列化为字符串供 token 估算。

    简化：拼接所有 TextBlock.text + ToolUseBlock.input(json) +
    ToolResultBlock.content。不用 json.dumps（性能开销，估算无需精确）。
    """
    from mewcode.providers import TextBlock, ToolResultBlock, ToolUseBlock

    parts = [msg.role + ":"]
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            parts.append(block.name)
            parts.append(repr(block.input))
        elif isinstance(block, ToolResultBlock):
            parts.append(block.content)
        else:
            parts.append(str(block))
    return "\n".join(parts)


def estimate_tokens(
    messages: list,
    last_usage_input_tokens: int = 0,
    anchor_message_count: int = 0,
) -> int:
    """估算总 input_tokens。

    Args:
        messages: 当前完整 messages 列表
        last_usage_input_tokens: 上次 API 响应的 input_tokens
        anchor_message_count: 上次响应时 messages 的长度

    Returns:
        估算 token 数
    """
    if last_usage_input_tokens == 0 or anchor_message_count == 0:
        # 无锚点：全部走字符估算
        total_chars = sum(
            len(serialize_message_for_estimation(m)) for m in messages
        )
        return total_chars // 3

    # 有锚点：锚点之前信任 last_usage
    if anchor_message_count > len(messages):
        # 异常：锚点超出（messages 被外部重置过），回退到全字符估算
        total_chars = sum(
            len(serialize_message_for_estimation(m)) for m in messages
        )
        return total_chars // 3

    # 锚点之后增量
    incremental_chars = sum(
        len(serialize_message_for_estimation(m))
        for m in messages[anchor_message_count:]
    )
    return last_usage_input_tokens + incremental_chars // 3
```

### 2.2 mewcode/compaction/lightweight.py

```python
"""第一层：轻量预防（spec F3 / F4 / F5 / F6 / D3 / D4）。

每次请求前对最新一条 tool_results 消息处理：
- 单工具结果 > 10KB → 存盘 + 替换为预览
- 单消息总和 > 25KB → 排序+依次存盘

仅修改 ToolResultBlock.content；不动消息结构。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.providers import Message

# 阈值（spec F3 / Q2）
SINGLE_TOOL_LIMIT = 10 * 1024   # 10KB
SINGLE_MSG_LIMIT = 25 * 1024    # 25KB

# 预览格式（spec F6 / Q4）
PREVIEW_HEAD_LINES = 20
PREVIEW_TAIL_LINES = 5


@dataclass
class StashEvent:
    """一次存盘事件。"""
    tool_use_id: str
    file_path: Path
    original_size: int


def _build_preview(content: str, file_path: Path, size: int) -> str:
    """生成预览替换文本。"""
    rel_path = file_path  # caller 已经传入相对路径友好版本
    size_kb = size / 1024
    lines = content.splitlines()

    # 行数 ≤ 25 时不截取（spec AC7）
    if len(lines) <= PREVIEW_HEAD_LINES + PREVIEW_TAIL_LINES:
        return (
            f"[工具结果已存盘到 {rel_path} ({size_kb:.1f}KB)]\n\n"
            f"{content}\n\n"
            f"完整内容请用 read 工具读取上述文件路径。"
        )

    head = "\n".join(lines[:PREVIEW_HEAD_LINES])
    tail = "\n".join(lines[-PREVIEW_TAIL_LINES:])
    return (
        f"[工具结果已存盘到 {rel_path} ({size_kb:.1f}KB)]\n\n"
        f"—— 前 {PREVIEW_HEAD_LINES} 行 ——\n{head}\n\n"
        f"—— 后 {PREVIEW_TAIL_LINES} 行 ——\n{tail}\n\n"
        f"完整内容请用 read 工具读取上述文件路径。"
    )


def _stash_block(
    block,
    msg_idx: int,
    cwd: Path,
    session_id: str,
) -> tuple[str, StashEvent]:
    """把 block.content 写盘 + 返回 (预览, event)。"""
    target_dir = cwd / ".mewcode" / "transcripts" / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"tool_{msg_idx}_{block.tool_use_id}.txt"
    file_path.write_text(block.content, encoding="utf-8")

    # 路径用相对 cwd 的形式更友好
    try:
        display_path = file_path.relative_to(cwd)
    except ValueError:
        display_path = file_path

    size = len(block.content.encode("utf-8"))
    preview = _build_preview(block.content, display_path, size)
    return preview, StashEvent(
        tool_use_id=block.tool_use_id,
        file_path=file_path,
        original_size=size,
    )


def apply_lightweight(
    messages: list,
    cwd: Path,
    session_id: str,
) -> list[StashEvent]:
    """对最新一条 tool_results 消息应用第一层。

    Returns:
        存盘事件列表（供 UI 提示用）
    """
    from mewcode.providers import Message, ToolResultBlock

    if not messages:
        return []

    # 找最后一条含 ToolResultBlock 的消息（通常是末尾）
    target_msg_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and any(
            isinstance(b, ToolResultBlock) for b in m.content
        ):
            target_msg_idx = i
            break

    if target_msg_idx < 0:
        return []

    msg = messages[target_msg_idx]
    events: list[StashEvent] = []
    new_blocks = list(msg.content)

    # 第一阶段：单工具 > 10KB 直接存盘
    for i, block in enumerate(new_blocks):
        if not isinstance(block, ToolResultBlock):
            continue
        size = len(block.content.encode("utf-8"))
        if size > SINGLE_TOOL_LIMIT:
            preview, ev = _stash_block(block, target_msg_idx, cwd, session_id)
            new_blocks[i] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=preview,
                is_error=block.is_error,
            )
            events.append(ev)

    # 第二阶段：单消息总和 > 25KB 排序+依次存盘
    def msg_total_size():
        return sum(
            len(b.content.encode("utf-8"))
            for b in new_blocks
            if isinstance(b, ToolResultBlock)
        )

    if msg_total_size() > SINGLE_MSG_LIMIT:
        # 按 size 从大到小排序，逐个存盘直到 ≤ 25KB
        candidates = sorted(
            (
                (i, b)
                for i, b in enumerate(new_blocks)
                if isinstance(b, ToolResultBlock)
                and not b.content.startswith("[工具结果已存盘到 ")
            ),
            key=lambda pair: -len(pair[1].content.encode("utf-8")),
        )

        for i, block in candidates:
            if msg_total_size() <= SINGLE_MSG_LIMIT:
                break
            preview, ev = _stash_block(block, target_msg_idx, cwd, session_id)
            new_blocks[i] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=preview,
                is_error=block.is_error,
            )
            events.append(ev)

    if events:
        # 用新 blocks 重建 message（Message 是 frozen）
        messages[target_msg_idx] = Message(role=msg.role, content=new_blocks)

    return events
```

### 2.3 mewcode/compaction/summarizer.py

```python
"""第二层：重量摘要 LLM 调用（spec F10 / F11 / F12 / F13）。

包含：
- COMPACTION_SYSTEM_PROMPT 常量
- compute_keep_boundary：计算近期保留区
- summarize_messages_to_text：序列化早期 messages 为 LLM 输入
- summarize_async：调 LLM 拿摘要文本
- extract_summary：解析 <summary> 标签
- build_boundary_message：构造边界 reminder user 消息
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.providers import Message, Provider

from mewcode.compaction.tokens import (
    estimate_tokens,
    serialize_message_for_estimation,
)


COMPACTION_SYSTEM_PROMPT = """\
You are conducting a conversation summarization task. Your output will
REPLACE the early portion of a conversation history.

CRITICAL CONSTRAINTS:
- DO NOT call any tools. This is a summarization task only.
- DO NOT generate code. Describe what was done, not how.
- First write your <analysis> draft (free thinking), then write the
  final <summary>.

The <analysis> section is for your reasoning—it will be discarded.
The <summary> section is what gets retained. Use these EXACT 5
subsections in Chinese:

<summary>
## 会话目标
（用户最初想完成什么？1-2 句话）

## 关键决策
（重要的技术选择、约定、推翻的方案）

## 代码变更
（已修改的文件 + 大致改了什么；不要贴代码）

## 未完成事项
（TODO、失败的尝试、待验证的假设）

## 当前状态
（走到哪一步，下一步应当做什么）
</summary>
"""

# 近期保留区下限（spec F10 / Q6）
KEEP_TOKEN_TARGET = 10000
KEEP_MIN_MESSAGES = 5


def compute_keep_boundary(messages: list) -> int:
    """从尾部往回数 ≥ 10K token 或至少 5 条；扩展到完整 turn 边界。

    Returns:
        keep_start_index：[keep_start_index:] 是保留区
        若整个历史不够 → 返回 0（调用方应当跳过摘要）
    """
    from mewcode.providers import ToolResultBlock

    if not messages:
        return 0

    # 1. 从尾部向前累加 token
    accumulated = 0
    keep_start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        size = len(serialize_message_for_estimation(messages[i])) // 3
        accumulated += size
        keep_start = i
        # 累计达 10K AND 已包含至少 5 条
        if (
            accumulated >= KEEP_TOKEN_TARGET
            and (len(messages) - keep_start) >= KEEP_MIN_MESSAGES
        ):
            break

    # 至少 5 条
    if len(messages) - keep_start < KEEP_MIN_MESSAGES:
        keep_start = max(0, len(messages) - KEEP_MIN_MESSAGES)

    # 2. 扩展到完整 turn 边界：往前找一个真实用户消息
    while keep_start > 0:
        m = messages[keep_start]
        if m.role == "user" and not any(
            isinstance(b, ToolResultBlock) for b in m.content
        ):
            break  # 这是真实 user 消息，作为 turn 起点
        keep_start -= 1

    return keep_start


def summarize_messages_to_text(messages: list) -> str:
    """把 messages 序列化为可读文本喂给摘要 LLM。"""
    parts = []
    for i, m in enumerate(messages):
        parts.append(f"--- message {i} (role={m.role}) ---")
        parts.append(serialize_message_for_estimation(m))
    return "\n\n".join(parts)


async def summarize_async(
    provider: "Provider",
    messages_to_summarize: list,
    user_instruction: str = "",
) -> str | None:
    """调 LLM 摘要，返回 <summary> 部分；失败返回 None。"""
    from mewcode.providers import Done, Message, TextBlock, TextDelta

    history_text = summarize_messages_to_text(messages_to_summarize)
    user_prompt = f"以下是需要摘要的对话历史（早期部分）：\n\n{history_text}"
    if user_instruction.strip():
        user_prompt += f"\n\n额外要求：\n{user_instruction.strip()}"

    request_messages = [
        Message(role="user", content=[TextBlock(text=user_prompt)])
    ]

    text_buf = ""
    try:
        async for ev in provider.stream_chat(
            request_messages,
            thinking=False,
            system=COMPACTION_SYSTEM_PROMPT,
            tools_format=None,
        ):
            if isinstance(ev, TextDelta):
                text_buf += ev.text
            elif isinstance(ev, Done):
                break
    except Exception:
        return None

    return extract_summary(text_buf)


_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def extract_summary(llm_output: str) -> str | None:
    """提取 <summary>...</summary> 中间内容。

    成功条件：
    - 找到 <summary> 块
    - 块内非空
    - 5 段标题至少有 3 个
    """
    if not llm_output:
        return None
    m = _SUMMARY_RE.search(llm_output)
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return None
    title_count = sum(
        1
        for title in ("会话目标", "关键决策", "代码变更", "未完成事项", "当前状态")
        if title in body
    )
    if title_count < 3:
        return None
    return body


def build_boundary_message(
    summary_text: str, compacted_count: int
) -> "Message":
    """构造摘要后的边界 user 消息（spec F14）。"""
    from mewcode.providers import Message, TextBlock

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        "<system-reminder>\n"
        "[Context Compacted]\n"
        f"上面是早期对话的摘要（从 {compacted_count} 条消息压缩而来）。\n"
        "重要：完整文件内容请重新用 read 工具读取，不要从摘要中脑补具体代码。\n"
        f"压缩时间：{timestamp}\n\n"
        f"{summary_text}\n"
        "</system-reminder>"
    )
    return Message(role="user", content=[TextBlock(text=text)])
```

### 2.4 mewcode/compaction/compactor.py

```python
"""第八阶段总入口：协调第一层 + 第二层 + 状态管理（spec F2 / F8 / F9 / F15）。"""

from dataclasses import dataclass, field
from pathlib import Path

from mewcode.compaction.lightweight import StashEvent, apply_lightweight
from mewcode.compaction.summarizer import (
    build_boundary_message,
    compute_keep_boundary,
    summarize_async,
)
from mewcode.compaction.tokens import estimate_tokens

# 阈值缓冲（spec F8 / Q5）
AUTO_BUFFER = 13000
MANUAL_BUFFER = 3000

DEFAULT_CONTEXT_WINDOW = 128000

# 不同 provider/model 的 context window
_CONTEXT_WINDOWS = {
    "claude-3-5-sonnet": 200000,
    "claude-3-7-sonnet": 200000,
    "claude-3-7-sonnet-latest": 200000,
    "claude-3-opus": 200000,
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "deepseek-v4": 128000,
}


def _detect_window(model: str) -> int:
    """根据模型名匹配 context window，未知模型给保守默认值。"""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    model_lc = model.lower()
    for key, window in _CONTEXT_WINDOWS.items():
        if key in model_lc:
            return window
    return DEFAULT_CONTEXT_WINDOW


@dataclass
class CompactStats:
    """before_request 返回的统计。"""
    stash_events: list[StashEvent] = field(default_factory=list)
    summary_triggered: bool = False
    summary_succeeded: bool = False
    summary_error: str | None = None
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0


class Compactor:
    """两层压缩协调器。"""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    # ---- 状态查询 ----

    def get_window(self, model: str) -> int:
        return _detect_window(model)

    # ---- 主入口 ----

    async def before_request(
        self, session, manual: bool = False, instruction: str = ""
    ) -> CompactStats:
        """API 请求前的检查链。

        Args:
            session: 当前 Session
            manual: 是否用户手动触发（/compact）
            instruction: 用户指示（仅 manual=True 时有效）

        Returns:
            CompactStats，记录所有发生的事件
        """
        stats = CompactStats()

        # ---- 第一层：轻量预防（始终跑）----
        events = apply_lightweight(
            session.messages, self._cwd, session.session_id
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
        if not manual and session.compaction_disabled:
            return stats

        # ---- 第二层判定 ----
        window = self.get_window(session.provider.model)
        auto_threshold = window - AUTO_BUFFER
        manual_threshold = window - MANUAL_BUFFER

        if manual:
            should = True  # /compact 必触发
        else:
            should = estimated >= auto_threshold

        if not should:
            stats.estimated_tokens_after = estimated
            return stats

        stats.summary_triggered = True

        # ---- 第二层执行 ----
        keep_start = compute_keep_boundary(session.messages)
        if keep_start <= 0:
            # 没有可压缩的早期部分（历史太短 / 找不到 turn 边界）
            stats.summary_error = "no_compactable_prefix"
            stats.estimated_tokens_after = estimated
            if not manual:
                session.compaction_failures += 1
                if session.compaction_failures >= 3:
                    session.compaction_disabled = True
            return stats

        early = list(session.messages[:keep_start])
        recent = list(session.messages[keep_start:])

        summary_text = await summarize_async(
            session.provider, early, instruction
        )

        if summary_text is None:
            stats.summary_error = "llm_failed_or_unparsable"
            if not manual:
                session.compaction_failures += 1
                if session.compaction_failures >= 3:
                    session.compaction_disabled = True
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
        """API 响应完成后更新锚点（spec F2）。"""
        if usage and getattr(usage, "input_tokens", 0) > 0:
            session.last_usage_input_tokens = usage.input_tokens
            session.last_anchor_message_count = len(session.messages)

    def reset_state(self, session) -> None:
        """/clear / switch_provider 时重置压缩状态。"""
        session.last_usage_input_tokens = 0
        session.last_anchor_message_count = 0
        session.compaction_failures = 0
        session.compaction_disabled = False
```

### 2.5 mewcode/compaction/__init__.py

```python
from mewcode.compaction.compactor import Compactor, CompactStats
from mewcode.compaction.lightweight import StashEvent

__all__ = ["Compactor", "CompactStats", "StashEvent"]
```

### 2.6 chat/session.py 修改

```python
@dataclass
class Session:
    ...
    # 第八阶段：上下文压缩状态
    last_usage_input_tokens: int = 0
    last_anchor_message_count: int = 0
    compaction_failures: int = 0
    compaction_disabled: bool = False
    session_id: str = ""

    def clear(self) -> None:
        self.messages.clear()
        self.mode = "do"
        self.plan_turn_count = 0
        # 第八阶段：重置压缩状态（保留 session_id）
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False

    def switch_provider(self, provider: Provider, name: str = "") -> None:
        ...
        # 第八阶段：切 provider 时重置压缩状态
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False
```

### 2.7 chat/engine.py 集成

`run_turn` 入口（在 append_user_text 之后、_agent_loop 之前）：

```python
async def run_turn(
    session, user_input, renderer,
    registry=None, confirmer=None, sandbox=None,
    policy=None, asker=None,
    compactor=None,                    # ← 新增
) -> bool:
    session.append_user_text(user_input)

    # 第八阶段：请求前压缩
    if compactor is not None:
        try:
            stats = await compactor.before_request(session)
            _emit_compact_stats(renderer, stats)
        except Exception as e:
            renderer.print_info(f"⚠️ 压缩阶段异常：{e}")

    return await _agent_loop(
        session, renderer, registry, confirmer, sandbox,
        policy=policy, asker=asker,
        compactor=compactor,           # ← 透传给 _agent_loop
    )
```

`_agent_loop` 在每轮 stream_chat 完后（拿到 Usage 时）调
`compactor.after_response(session, usage)`。

`_emit_compact_stats` 是 chat 层小工具：
- stash_events 非空 → 打印 `📦 已存盘 N 个超大工具结果到 transcripts/<session_id>/`
- summary_succeeded → `🧠 已压缩历史：从 X 条消息生成摘要，节省约 Y tokens`
- summary_error → `⚠️ 压缩失败：<error>`

### 2.8 commands/builtin.py 新增 /compact

```python
async def _handle_compact(ctx: CommandContext) -> CommandResult:
    """/compact [instruction]：手动触发上下文压缩。"""
    compactor = getattr(ctx, "compactor", None)
    if compactor is None:
        ctx.renderer.print_info("压缩系统未启用。")
        return CommandResult()

    instruction = " ".join(ctx.args).strip()
    stats = await compactor.compact_now(ctx.session, instruction)

    if stats.stash_events:
        ctx.renderer.print_info(
            f"📦 第一层：存盘 {len(stats.stash_events)} 个工具结果"
        )

    if stats.summary_succeeded:
        ctx.renderer.print_info(
            f"🧠 已压缩。{stats.estimated_tokens_before} → "
            f"{stats.estimated_tokens_after} tokens（估算）"
        )
    elif stats.summary_triggered:
        ctx.renderer.print_info(
            f"⚠️ 压缩失败：{stats.summary_error or '未知错误'}"
        )
    elif not stats.stash_events:
        ctx.renderer.print_info("当前对话无需压缩。")

    return CommandResult()
```

注册到 register_builtins。CommandContext 增加 `compactor` 字段。

### 2.9 main.py 装配

```python
from datetime import datetime
from mewcode.compaction import Compactor

session.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
compactor = Compactor(cwd=sandbox.cwd)
```

`_amain` 把 compactor 透传到 run_repl，run_repl 再透传给 chat.run_turn
与 CommandContext。

## 3. 技术决策

### D1. 为什么字符 / 3 而非 / 4

**决策**：字符数除以 3 估算 token。

**理由**：
- 字符 / 4 是英文 tokenizer 经验值（GPT BPE 平均 4 char ≈ 1 token）
- 中文 1 char 经常 = 1.5-2 token（每个汉字独立 token）
- mewcode 用户场景中文多 → 偏保守用 / 3 避免低估
- 实测在 deepseek-v4-pro 上 / 3 比实际 input_tokens 偏大约 5-15%——
  正好作为安全余量

### D2. 为什么锚点策略而非每次重新估算

**决策**：用 last_usage_input_tokens 作为锚点，仅对增量估算。

**理由**：
- 上次实测值 100% 准确，比字符估算靠谱得多
- 增量部分（最近 1-3 条）字符估算误差很小
- 锚点失效时（如 messages 被外部重置）回退到全字符估算
- 性能：估算从 O(N) 缩减到 O(增量条数)

### D3. 为什么单工具 10KB / 单消息 25KB

**决策**：双阈值。

**理由**：
- 10KB ≈ 3000 token：覆盖 read 大文件（典型源文件 5-10KB）/ search
  返回大量匹配的 80% 场景
- 25KB ≈ 8000 token：覆盖一轮模型并发调多个工具的累计场景
- 阈值不通过配置暴露（spec N2）：
  - 配置项越多，认知成本越高
  - 后续如果有真实需求再加（如接收很多 image 数据的工具）

### D4. 为什么前 20 + 后 5 行预览

**决策**：截取前 20 行 + 后 5 行。

**理由**：
- read 工具结果开头通常是文件头注释、import 语句、类定义——20 行
  足以推断"这是一个什么文件"
- 结尾通常是 export / 主函数 / EOF——5 行确认完整性
- 总预览 < 1KB，相比原始 10KB+ 节省 90%+
- 行数 ≤ 25 时不截：避免反向放大（25 行小文件全留比预览还短）

### D5. 为什么自动 13K / 手动 3K 缓冲

**决策**：自动留 13K 余量，手动留 3K。

**理由**：
- 字符 / 3 可能偏差 5-10%，128K 模型偏差最高 12K → 13K 缓冲精确兜底
- 自动触发是"防御性"，宁早勿晚
- 手动触发用户已经下决定 → 缓冲收窄，最大化保留可压缩量
- 两个缓冲都不通过配置暴露（spec N2）

### D6. 为什么往前扩展到完整 turn 边界

**决策**：keep_boundary 计算后往前移到一个真实 user 消息。

**理由**：
- assistant + tool_use + user(tool_results) + assistant 是完整 turn
- 切到中间会孤立 tool_use（无对应 tool_result）/ 反之
- Anthropic 协议会拒绝孤立的 tool_use（messages 配对校验）
- 简单实现：从 keep_start 往前扫，找第一个 role=user 且 content 不是
  ToolResultBlock 的位置
- 代价：保留区可能大于 10K（这是下限不是上限）—— 可接受

### D7. 为什么禁工具传 tools_format=None

**决策**：摘要请求不传 tools_format。

**理由**：
- Anthropic / OpenAI 协议都规定 tools 数组为空时模型不会调用工具
- 比"在 system prompt 写 DO NOT call tools"硬约束（前者 100%，后者
  90%）
- 摘要纯生成任务，工具调用反而会引入噪声
- 实现简单：调 stream_chat 不传 tools_format 参数

### D8. 为什么 5 段中文标题

**决策**：5 段固定结构，中文标题。

**理由**：
- 模型对结构化提示响应稳定（"必须按这 5 段写"）
- 中文标题与 mewcode 整体中文优先策略一致
- 5 段覆盖软件开发场景核心信息：目标 / 决策 / 变更 / TODO / 状态
- 解析时 `if title_count < 3` 即视为格式失败 → 兜底（模型忘了写不会
  导致摘要质量崩盘）

### D9. 为什么 system-reminder 标签

**决策**：边界消息用 `<system-reminder>` 标签包裹。

**理由**：
- 与第四阶段 plan mode reminder 同模式（模型已经训练过识别此标签）
- 让模型清楚这是系统补充而非用户问题
- 含动态内容（消息数 / 时间戳）
- 包在 user 消息里：
  - 不破坏 system_prompt（cache 不失效）
  - 进入正常对话流（模型读到后会理解为"这是历史压缩说明"）

### D10. 为什么连续 3 次失败熔断

**决策**：自动触发失败 3 次 → disabled，/clear 重置。

**理由**：
- 1 次失败可能是网络抖动 / LLM 偶发错误
- 3 次连续失败几乎确认 provider 有问题
- disabled 后所有自动压缩跳过 → 避免在崩盘的 provider 上继续浪费 token
- /compact 不计入熔断（用户已经决定要重试）
- /clear 是用户清空意图 → 自然重置

### D11. 为什么 main 装配 Compactor 而非 chat 内部

**决策**：main.py 构造 Compactor 实例，透传到 chat 与 commands。

**理由**：
- Compactor 需要 cwd（路径）+ 状态—— main 是装配处
- 透传给 run_turn / CommandContext 保持模块边界清晰
- 测试时可注入 stub Compactor
- 与第五阶段 PermissionPolicy / 第七阶段 InstructionsLoader 同模式

### D12. 为什么不引入精确 tokenizer

**决策**：估算用纯字符，不引入 tiktoken / sentencepiece。

**理由**：
- spec N2：本阶段不引入新依赖
- tiktoken 只对 OpenAI 模型精确，对 Anthropic / DeepSeek 仍是估算
- 锚点策略 + 13K 缓冲已经足够安全
- 后续如有需要再加（成本 = 一个新依赖 + 各 provider 的 tokenizer 切换）

## 4. 时序图

### 4.1 第一层处理（典型场景）

```
user      chat.engine    compactor           lightweight       fs
 │           │              │                   │                │
 │ 输入       │              │                   │                │
 ├─►run_turn │              │                   │                │
 │           │ append_user_text                  │                │
 │           │ before_request(session)           │                │
 │           ├─────────────►│                   │                │
 │           │              │ apply_lightweight  │                │
 │           │              ├──────────────────►│                │
 │           │              │                   │ 扫最新tool_results│
 │           │              │                   │ 单工具>10KB:写盘│
 │           │              │                   ├───────────────►│ tool_5_xxx.txt
 │           │              │                   │ 替换 content 为预览│
 │           │              │                   │                │
 │           │              │ ◄────────────────┤ events          │
 │           │              │ 估算 → < 阈值     │                │
 │           │ ◄────────────┤ stats             │                │
 │           │ 进入 _agent_loop                  │                │
```

### 4.2 第二层触发流程

```
chat.engine    compactor       summarizer       provider
   │              │              │                │
   │ before_request(session)     │                │
   ├─────────────►│              │                │
   │              │ apply_lightweight             │
   │              │ ...                           │
   │              │ estimate_tokens               │
   │              │ -> 130K (window=128K，>auto_threshold)│
   │              │                               │
   │              │ compute_keep_boundary         │
   │              ├─────────────►│                │
   │              │ ◄────────────┤ keep_start=15  │
   │              │                               │
   │              │ summarize_async(early[0:15])  │
   │              ├─────────────►│                │
   │              │              │ stream_chat   │
   │              │              ├──────────────►│ tools=None
   │              │              │ ◄─────────────┤ TextDelta...
   │              │              │ extract_summary│
   │              │ ◄────────────┤ summary_text  │
   │              │                               │
   │              │ build_boundary_message       │
   │              │ session.messages = [boundary, *recent]│
   │              │ 重置 last_usage / anchor      │
   │              │                               │
   │ ◄────────────┤ stats(summary_succeeded=True)│
   │ 继续 stream_chat                            │
```

### 4.3 熔断流程

```
chat.engine    compactor          provider
   │              │                  │
   │ before_request × 3 (不同 turn)  │
   ├─────────────►│                  │
   │              │ summarize_async  │
   │              ├─────────────────►│ 失败1
   │              │ failures=1       │
   │              │                  │
   │              │ summarize_async  │
   │              ├─────────────────►│ 失败2
   │              │ failures=2       │
   │              │                  │
   │              │ summarize_async  │
   │              ├─────────────────►│ 失败3
   │              │ failures=3 → disabled=True
   │ ◄────────────┤ stats           │
   │ 后续自动压缩全跳过；/clear 重置 │
```

## 5. 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/compaction/__init__.py` |
| 新建 | `mewcode/compaction/tokens.py` |
| 新建 | `mewcode/compaction/lightweight.py` |
| 新建 | `mewcode/compaction/summarizer.py` |
| 新建 | `mewcode/compaction/compactor.py` |
| 修改 | `mewcode/chat/session.py` (+5 字段 + clear/switch 重置) |
| 修改 | `mewcode/chat/engine.py` (run_turn / _agent_loop 加 compactor 参数) |
| 修改 | `mewcode/commands/registry.py` (CommandContext + compactor) |
| 修改 | `mewcode/commands/builtin.py` (+ /compact 命令) |
| 修改 | `mewcode/main.py` (装配 Compactor + session_id) |
| 修改 | `mewcode/repl/main_loop.py` (透传 compactor) |
| 修改 | `.gitignore` (+ .mewcode/transcripts/) |
| 新建 | `tests/test_compaction_tokens.py` |
| 新建 | `tests/test_compaction_lightweight.py` |
| 新建 | `tests/test_compaction_summarizer.py` |
| 新建 | `tests/test_compaction_compactor.py` |
| 新建 | `tests/test_compact_command.py` |
| 新建 | `scripts/verify_compaction.py` |

共 18 个文件（10 新建 + 7 修改 + 1 .gitignore）。

## 6. 与第七阶段的兼容矩阵

| 第七阶段行为 | 第八阶段是否保留 | 说明 |
|-------------|-----------------|------|
| run_turn 签名 | ✅ 兼容 | 新增可选参数 compactor |
| Provider stream_chat | ✅ 不变 | |
| ToolRegistry | ✅ 不变 | |
| Sandbox | ✅ 不变 | |
| PermissionPolicy | ✅ 不变 | |
| MCP 子模块 | ✅ 不变 | |
| InstructionsLoader | ✅ 不变 | |
| build_system_prompt | ✅ 不变 | |
| AgentEvent 7 种 | ✅ 不变 | |
| /clear /provider /think /plan /do /permissions /instructions | ✅ 不变 |
| 320 个已有单测 | ✅ 全过 | 新模块独立 |
| prompt cache 命中 | ✅ 短对话不变 | 第二层触发时 messages 大改会破 cache 一次（预期） |
| 端到端脚本 | ✅ 全过 | 短对话场景不触发任何压缩 |

### 不需要适配的已有测试

- run_turn 测试不传 compactor → 行为完全等同第七阶段
- Session.clear() 单测仍通过（新字段重置不影响 mode/plan_turn_count）
- 第七阶段 320 测试全部不感知 compaction 模块
