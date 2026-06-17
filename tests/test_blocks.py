"""ContentBlock 与 Message 工厂方法的单元测试。

覆盖 task.md T2 的 4 个验证场景：
- Message.text 工厂构造
- Message.tool_results 工厂构造
- Message 含混合块
- 块的不可变性
"""

import pytest
from dataclasses import FrozenInstanceError

from mewcode.providers import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def test_message_text_工厂() -> None:
    """Message.text 应返回 content 含单个 TextBlock 的消息。"""
    m = Message.text("user", "你好")
    assert m.role == "user"
    assert isinstance(m.content, list)
    assert len(m.content) == 1
    assert isinstance(m.content[0], TextBlock)
    assert m.content[0].text == "你好"


def test_message_tool_results_工厂() -> None:
    """Message.tool_results 应返回 role=user 的消息，含给定的 ToolResultBlock。"""
    r1 = ToolResultBlock(tool_use_id="t1", content="ok")
    r2 = ToolResultBlock(tool_use_id="t2", content="err", is_error=True)
    m = Message.tool_results([r1, r2])
    assert m.role == "user"
    assert len(m.content) == 2
    assert m.content[0] is r1
    assert m.content[1] is r2
    assert m.content[1].is_error is True


def test_message_含混合块() -> None:
    """assistant 消息可包含 text + thinking + tool_use 任意混合块。"""
    blocks = [
        ThinkingBlock(text="思考中...", signature="sig-1"),
        TextBlock(text="我来帮你看一下。"),
        ToolUseBlock(id="toolu_01", name="read", input={"path": "a.py"}),
    ]
    m = Message(role="assistant", content=blocks)
    assert m.role == "assistant"
    assert len(m.content) == 3
    assert isinstance(m.content[0], ThinkingBlock)
    assert isinstance(m.content[1], TextBlock)
    assert isinstance(m.content[2], ToolUseBlock)
    # 字段访问正确
    assert m.content[0].signature == "sig-1"
    assert m.content[2].input["path"] == "a.py"


def test_blocks_frozen() -> None:
    """所有 ContentBlock 都是 frozen dataclass，禁止修改字段。"""
    text = TextBlock(text="hi")
    with pytest.raises(FrozenInstanceError):
        text.text = "hello"  # type: ignore[misc]

    tu = ToolUseBlock(id="x", name="read", input={"path": "a"})
    with pytest.raises(FrozenInstanceError):
        tu.id = "y"  # type: ignore[misc]

    tr = ToolResultBlock(tool_use_id="x", content="ok")
    with pytest.raises(FrozenInstanceError):
        tr.is_error = True  # type: ignore[misc]


def test_thinking_block_默认signature() -> None:
    """ThinkingBlock.signature 默认为空字符串。"""
    t = ThinkingBlock(text="嗯")
    assert t.signature == ""
