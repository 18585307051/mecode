"""第九阶段 F4：sessions codec roundtrip 测试。"""

from __future__ import annotations

import json

import pytest

from mewcode.providers import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.sessions.codec import (
    block_from_dict,
    block_to_dict,
    message_from_jsonl,
    message_to_jsonl,
)


def test_text_block_roundtrip():
    block = TextBlock(text="hello 你好")
    data = block_to_dict(block)
    assert data == {"type": "text", "text": "hello 你好"}
    assert block_from_dict(data) == block


def test_thinking_block_roundtrip():
    block = ThinkingBlock(text="why?", signature="sig-123")
    data = block_to_dict(block)
    assert data["type"] == "thinking"
    restored = block_from_dict(data)
    assert restored == block


def test_tool_use_block_roundtrip():
    block = ToolUseBlock(id="tu_1", name="read", input={"path": "a.py"})
    data = block_to_dict(block)
    assert data == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "read",
        "input": {"path": "a.py"},
    }
    assert block_from_dict(data) == block


def test_tool_result_block_roundtrip():
    block = ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)
    data = block_to_dict(block)
    assert data["type"] == "tool_result"
    assert block_from_dict(data) == block


def test_message_jsonl_roundtrip():
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="计划如下"),
            ToolUseBlock(id="tu_1", name="read", input={"path": "a"}),
        ],
    )
    line = message_to_jsonl(msg)
    assert line.endswith("\n")
    # ensure_ascii=False：中文不转 \uXXXX
    assert "计划如下" in line
    parsed_msg, _ts = message_from_jsonl(line)
    assert parsed_msg == msg


def test_invalid_block_type_raises():
    with pytest.raises(ValueError):
        block_from_dict({"type": "unknown", "x": 1})


def test_message_from_jsonl_bad_json():
    with pytest.raises(ValueError):
        message_from_jsonl("{not valid json")


def test_message_record_role_validation():
    # role 必须是 user / assistant
    payload = json.dumps(
        {"type": "message", "role": "system", "content": [], "ts": ""}
    )
    with pytest.raises(ValueError):
        message_from_jsonl(payload)
