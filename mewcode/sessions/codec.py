"""Session JSONL 编解码（spec 第九阶段 F4）。

把 chat.Message / providers.ContentBlock 与 JSON dict 互转：
- 序列化：写入 `<cwd>/.mewcode/sessions/<session_id>.jsonl`，每行一条
  message 记录，便于追加写与坏行跳过。
- 反序列化：恢复历史时按行 json.loads，再调本模块还原 Message。

设计要点：
- 仅记录"内部已知"的四种 ContentBlock 类型，未知类型抛 ValueError，
  由 archive 层按"坏行跳过"处理。
- record 顶层固定字段：type / ts / role / content；不写入 provider、
  system_prompt、usage 等运行时元数据（spec D2 不维护 meta 文件）。
- ensure_ascii=False 写入，便于用户直接打开查看中文。
"""

import json
from datetime import datetime
from typing import Any

from mewcode.providers import (
    ContentBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def block_to_dict(block: ContentBlock) -> dict[str, Any]:
    """把单个 ContentBlock 序列化为可 json.dumps 的 dict。"""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "text": block.text,
            "signature": block.signature,
        }
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise ValueError(f"未知 ContentBlock 类型：{type(block).__name__}")


def block_from_dict(data: dict[str, Any]) -> ContentBlock:
    """把单个 block dict 还原为 ContentBlock。

    非法 type 或缺字段抛 ValueError，调用方按"坏行跳过"处理。
    """
    if not isinstance(data, dict):
        raise ValueError("block 必须是 dict")
    btype = data.get("type")
    if btype == "text":
        text = data.get("text")
        if not isinstance(text, str):
            raise ValueError("text block 缺少 text 字段")
        return TextBlock(text=text)
    if btype == "thinking":
        text = data.get("text")
        signature = data.get("signature", "")
        if not isinstance(text, str):
            raise ValueError("thinking block 缺少 text 字段")
        if not isinstance(signature, str):
            signature = ""
        return ThinkingBlock(text=text, signature=signature)
    if btype == "tool_use":
        tid = data.get("id")
        name = data.get("name")
        tinput = data.get("input", {})
        if not isinstance(tid, str) or not isinstance(name, str):
            raise ValueError("tool_use block 缺少 id/name 字段")
        if not isinstance(tinput, dict):
            raise ValueError("tool_use.input 必须是 dict")
        return ToolUseBlock(id=tid, name=name, input=tinput)
    if btype == "tool_result":
        tuid = data.get("tool_use_id")
        content = data.get("content")
        is_error = bool(data.get("is_error", False))
        if not isinstance(tuid, str) or not isinstance(content, str):
            raise ValueError("tool_result block 缺少 tool_use_id/content 字段")
        return ToolResultBlock(
            tool_use_id=tuid, content=content, is_error=is_error
        )
    raise ValueError(f"未知 block.type：{btype!r}")


def message_to_record(
    message: Message, *, ts: datetime | None = None
) -> dict[str, Any]:
    """把 Message 序列化为 JSONL 单行 record dict。"""
    if ts is None:
        ts = datetime.now().astimezone()
    return {
        "type": "message",
        "ts": ts.isoformat(),
        "role": message.role,
        "content": [block_to_dict(b) for b in message.content],
    }


def message_from_record(record: dict[str, Any]) -> tuple[Message, datetime]:
    """把 JSONL record dict 还原为 (Message, ts)。

    校验失败抛 ValueError；调用方按坏行跳过即可。
    """
    if not isinstance(record, dict):
        raise ValueError("record 必须是 dict")
    if record.get("type") != "message":
        raise ValueError(f"非 message 类型：{record.get('type')!r}")

    role = record.get("role")
    if role not in ("user", "assistant"):
        raise ValueError(f"非法 role：{role!r}")

    raw_content = record.get("content")
    if not isinstance(raw_content, list):
        raise ValueError("content 必须是 list")
    blocks: list[ContentBlock] = [block_from_dict(b) for b in raw_content]

    ts_raw = record.get("ts", "")
    ts: datetime
    if isinstance(ts_raw, str) and ts_raw:
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            # ts 格式异常不致命，置为 epoch 让 archive 层兜底
            ts = datetime.fromtimestamp(0).astimezone()
    else:
        ts = datetime.fromtimestamp(0).astimezone()

    return Message(role=role, content=blocks), ts


def message_to_jsonl(message: Message, *, ts: datetime | None = None) -> str:
    """便捷函数：把 Message 序列化为单行 JSONL 字符串（含末尾换行）。"""
    record = message_to_record(message, ts=ts)
    return json.dumps(record, ensure_ascii=False) + "\n"


def message_from_jsonl(line: str) -> tuple[Message, datetime]:
    """便捷函数：把单行 JSONL 字符串解析为 (Message, ts)。

    JSON 解析失败或字段非法都抛 ValueError。
    """
    line = line.strip()
    if not line:
        raise ValueError("空行")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败：{e}") from e
    return message_from_record(data)
