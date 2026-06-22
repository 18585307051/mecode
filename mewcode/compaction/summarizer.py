"""第二层：重量摘要 LLM 调用（spec F10 / F11 / F12 / F13 / F14 / D6 / D7 / D8 / D9）。

包含：
- COMPACTION_SYSTEM_PROMPT：摘要任务的 system 提示
- compute_keep_boundary：计算近期保留区起点
- summarize_messages_to_text：序列化早期 messages 为 LLM 输入文本
- summarize_async：调当前 provider 拿摘要
- extract_summary：解析 <summary>...</summary>
- build_boundary_message：构造摘要后的边界 user 消息
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.providers import Message, Provider

from mewcode.compaction.tokens import serialize_message_for_estimation


# ---------- 常量 ----------

# 近期保留区目标（spec F10 / Q6）
KEEP_TOKEN_TARGET = 10000
KEEP_MIN_MESSAGES = 5

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

_REQUIRED_SUBHEADINGS = (
    "会话目标",
    "关键决策",
    "代码变更",
    "未完成事项",
    "当前状态",
)


# ---------- 近期保留区计算 ----------


def compute_keep_boundary(messages: list) -> int:
    """从尾部往回数 ≥ 10K token 或至少 5 条；扩展到完整 turn 边界。

    spec F10 / Q6 / D6：
    1. 从尾部向前累加字符 / 3 估算
    2. 累计达 10K 且条数 ≥ 5 → 候选 keep_start
    3. 至少保留 5 条
    4. 扩展边界：往前移到一个真实 user 消息（role=user 且不含
       ToolResultBlock）

    Returns:
        keep_start_index：messages[keep_start:] 是保留区
        若整个历史都不够压缩 → 返回 0（调用方应当跳过摘要）
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
        if (
            accumulated >= KEEP_TOKEN_TARGET
            and (len(messages) - keep_start) >= KEEP_MIN_MESSAGES
        ):
            break

    # 2. 至少 5 条
    if len(messages) - keep_start < KEEP_MIN_MESSAGES:
        keep_start = max(0, len(messages) - KEEP_MIN_MESSAGES)

    # 3. 扩展到完整 turn 边界：往前找一个真实用户消息
    while keep_start > 0:
        m = messages[keep_start]
        is_real_user = (
            m.role == "user"
            and not any(isinstance(b, ToolResultBlock) for b in m.content)
        )
        if is_real_user:
            break
        keep_start -= 1

    return keep_start


# ---------- 序列化 ----------


def summarize_messages_to_text(messages: list) -> str:
    """把 messages 序列化为可读文本，供摘要 LLM 输入。

    格式：
        --- message N (role=user|assistant) ---
        <内容>
    """
    parts = []
    for i, m in enumerate(messages):
        parts.append(f"--- message {i} (role={m.role}) ---")
        parts.append(serialize_message_for_estimation(m))
    return "\n\n".join(parts)


# ---------- 摘要解析 ----------


_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL | re.IGNORECASE)


def extract_summary(llm_output: str) -> str | None:
    """提取 <summary>...</summary> 中间内容（spec F13 / D8）。

    成功条件：
    - 找到 <summary> 块
    - 块内非空
    - 5 段中文标题至少含 3 个（容忍模型偶尔少写 1-2 段）

    Returns:
        摘要正文（不含 <summary> 标签）或 None（失败）
    """
    if not llm_output:
        return None
    m = _SUMMARY_RE.search(llm_output)
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return None
    title_count = sum(1 for title in _REQUIRED_SUBHEADINGS if title in body)
    if title_count < 3:
        return None
    return body


# ---------- 调 LLM 摘要 ----------


async def summarize_async(
    provider: "Provider",
    messages_to_summarize: list,
    user_instruction: str = "",
) -> str | None:
    """调当前 provider 摘要早期消息（spec F11 / F12）。

    特点：
    - 不传 tools_format → 强制模型不调工具（spec D7）
    - 单独的摘要 system_prompt（不复用 session.system_prompt）
    - 失败（任何异常 / 解析失败）返回 None

    Returns:
        <summary> 部分或 None
    """
    from mewcode.providers import Done, Message, TextBlock, TextDelta

    history_text = summarize_messages_to_text(messages_to_summarize)
    user_prompt = (
        f"以下是需要摘要的对话历史（早期部分）：\n\n{history_text}"
    )
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


# ---------- 边界消息构造 ----------


def build_boundary_message(
    summary_text: str, compacted_count: int
) -> "Message":
    """构造摘要后的边界 user 消息（spec F14 / D9）。

    含 <system-reminder> 标签 + 摘要内容 + 时间戳。
    """
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
