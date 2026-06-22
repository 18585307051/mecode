"""token 估算单测（spec AC1 / AC2）。"""

from mewcode.compaction.tokens import (
    estimate_tokens,
    serialize_message_for_estimation,
)
from mewcode.providers import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _user_text(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_text(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


# ---------- serialize_message_for_estimation ----------


def test_serialize_text_block() -> None:
    msg = _user_text("hello world")
    s = serialize_message_for_estimation(msg)
    assert "user:" in s
    assert "hello world" in s


def test_serialize_tool_use_block() -> None:
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="ok"),
            ToolUseBlock(id="t1", name="read", input={"path": "a.py"}),
        ],
    )
    s = serialize_message_for_estimation(msg)
    assert "read" in s
    assert "a.py" in s


def test_serialize_tool_result_block() -> None:
    msg = Message(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id="t1", content="file body", is_error=False
            )
        ],
    )
    s = serialize_message_for_estimation(msg)
    assert "file body" in s


# ---------- estimate_tokens ----------


def test_estimate_empty_messages() -> None:
    assert estimate_tokens([], 0, 0) == 0


def test_estimate_no_anchor_全字符() -> None:
    """spec AC2：last_usage=0 → 全部 messages 走字符 / 3。"""
    msgs = [_user_text("a" * 30), _assistant_text("b" * 30)]
    n = estimate_tokens(msgs, 0, 0)
    # 每条至少 30+ 字符（含 role 标签），总和 / 3
    assert n > 0
    assert n < 60  # 字符 / 3 上限


def test_estimate_with_anchor() -> None:
    """spec AC1：锚点之后用字符增量估算。"""
    msgs = [
        _user_text("first"),     # 锚点之前
        _assistant_text("ans"),  # 锚点之前
        _user_text("c" * 90),    # 锚点之后（增量 ≈ 90 chars / 3 ≈ 30 token）
    ]
    n = estimate_tokens(msgs, last_usage_input_tokens=1000, anchor_message_count=2)
    # 锚定 1000 + 第三条字符 / 3
    assert n >= 1000
    assert n < 1100  # 增量不会很大


def test_estimate_anchor_失效回退() -> None:
    """anchor 超出 → 回退全字符估算。"""
    msgs = [_user_text("a" * 30)]
    # 锚点 anchor=5 > len=1 → 视为锚点失效
    n = estimate_tokens(msgs, last_usage_input_tokens=1000, anchor_message_count=5)
    # 不会用 1000 这个值
    assert n < 100
