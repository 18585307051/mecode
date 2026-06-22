"""token 估算（spec F1 / D1 / D2）。

不引入精确 tokenizer。策略：
- 锚点：上次 API 响应的 input_tokens（实测）
- 增量：锚点之后新增的 messages 用字符 / 3 估算
- 加和返回总估算

字符 / 3 是经验系数：
- 英文 BPE 平均 4 char ≈ 1 token，/ 3 偏保守
- 中文 1 char ≈ 1.5-2 token，/ 3 较准
- 中文优先场景下用 / 3 避免低估上下文压力
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.providers import Message


# 字符 / token 比例（spec D1）。3 是中文优先的保守系数。
_CHARS_PER_TOKEN = 3


def serialize_message_for_estimation(msg: "Message") -> str:
    """把 Message 序列化为字符串供 token 估算（spec F1）。

    简化：拼接所有 block 的可读文本。不用 json.dumps 序列化（性能开销
    + 估算无需精确）。
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
    """估算当前 messages 的 input_tokens。

    Args:
        messages: 当前完整 messages 列表
        last_usage_input_tokens: 上次 API 响应的 input_tokens（锚点值）
        anchor_message_count: 上次响应时 messages 的长度（锚点位置）

    Returns:
        估算 token 数。仅估算 messages 部分，不包括 system_prompt
        （system_prompt 由 chat 层加上 context 整体判断时另算或忽略，
        因为它对压缩判定影响较小且固定）。

    边界处理：
    - 空 messages → 0
    - 无锚点（last_usage=0 或 anchor=0）→ 全字符估算
    - 锚点超出（anchor > len(messages)，常见于 messages 被 reset）→
      回退到全字符估算
    """
    if not messages:
        return 0

    if last_usage_input_tokens <= 0 or anchor_message_count <= 0:
        # 无锚点：全部走字符估算
        total_chars = sum(
            len(serialize_message_for_estimation(m)) for m in messages
        )
        return total_chars // _CHARS_PER_TOKEN

    if anchor_message_count > len(messages):
        # 锚点失效（messages 被外部重置过短）→ 回退全字符估算
        total_chars = sum(
            len(serialize_message_for_estimation(m)) for m in messages
        )
        return total_chars // _CHARS_PER_TOKEN

    # 有锚点：锚点之前信任 last_usage；之后按字符估算增量
    incremental_chars = sum(
        len(serialize_message_for_estimation(m))
        for m in messages[anchor_message_count:]
    )
    return last_usage_input_tokens + incremental_chars // _CHARS_PER_TOKEN
