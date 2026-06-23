"""第九阶段 F3-F11：SessionArchive JSONL 存档与恢复测试。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from mewcode.providers import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.sessions.archive import (
    GAP_REMINDER_TAG,
    SessionArchive,
)
from mewcode.sessions.codec import message_to_jsonl


def _user_text(text: str) -> Message:
    return Message.text("user", text)


def _assistant_tool_use(uid: str, name: str = "read") -> Message:
    return Message(
        role="assistant",
        content=[ToolUseBlock(id=uid, name=name, input={"path": "x"})],
    )


def _tool_result(uid: str, content: str = "ok") -> Message:
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=uid, content=content)],
    )


# --- AC6 / AC9 ----------------------------------------------------------------


def test_append_messages_creates_jsonl(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    sid = archive.new_session_id()

    archive.append_message(sid, _user_text("hi"))
    archive.append_message(sid, _assistant_tool_use("tu_1"))
    archive.append_message(sid, _tool_result("tu_1"))

    path = archive.session_path(sid)
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    # 不生成 meta / index 文件（spec AC9）
    other_files = sorted(p.name for p in archive.directory.iterdir())
    assert other_files == [path.name]


# --- AC7 坏行跳过 -------------------------------------------------------------


def test_restore_skips_bad_lines(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    sid = archive.new_session_id()
    path = archive.session_path(sid)
    archive.directory.mkdir(parents=True, exist_ok=True)

    good = message_to_jsonl(_user_text("first"))
    bad = "{not valid json}\n"
    good2 = message_to_jsonl(_user_text("second"))
    path.write_text(good + bad + good2, encoding="utf-8")

    result = archive.restore(sid)
    assert result.bad_lines == 1
    assert len(result.messages) == 2
    assert any(
        isinstance(b, TextBlock) and b.text == "first"
        for b in result.messages[0].content
    )


# --- AC8 截断 -----------------------------------------------------------------


def test_restore_truncates_orphan_tool_use(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    sid = archive.new_session_id()

    archive.append_message(sid, _user_text("hi"))
    archive.append_message(sid, _assistant_tool_use("tu_1"))
    # 没有匹配 tool_result，最后一条 assistant tool_use 是孤儿

    result = archive.restore(sid)
    assert result.truncated is True
    # 截断后只剩 user 'hi'
    assert len(result.messages) == 1
    assert any(
        isinstance(b, TextBlock) and b.text == "hi"
        for b in result.messages[0].content
    )


def test_restore_truncates_orphan_tool_result(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    sid = archive.new_session_id()

    archive.append_message(sid, _user_text("hi"))
    archive.append_message(sid, _tool_result("tu_orphan"))

    result = archive.restore(sid)
    assert result.truncated is True
    assert len(result.messages) == 1


# --- AC10 选择最新 ------------------------------------------------------------


def test_restore_latest_picks_most_recent(tmp_path: Path):
    archive = SessionArchive(tmp_path)

    sid_old = "20200101-000000-aaaa"
    sid_new = "20300101-000000-bbbb"
    archive.directory.mkdir(parents=True, exist_ok=True)
    (archive.directory / f"{sid_old}.jsonl").write_text(
        message_to_jsonl(_user_text("old")), encoding="utf-8"
    )
    new_line = message_to_jsonl(_user_text("new"))
    (archive.directory / f"{sid_new}.jsonl").write_text(new_line, encoding="utf-8")

    summaries = archive.scan_summaries()
    assert summaries[0].session_id == sid_new

    result = archive.restore_latest()
    assert result.session_id == sid_new
    assert result.restored is True


# --- AC11 过期清理 ------------------------------------------------------------


def test_cleanup_expired(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    archive.directory.mkdir(parents=True, exist_ok=True)

    # 写一条带很旧 ts 的会话
    old_msg = {
        "type": "message",
        "ts": (datetime.now().astimezone() - timedelta(days=60)).isoformat(),
        "role": "user",
        "content": [{"type": "text", "text": "old"}],
    }
    old_path = archive.directory / "20200101-000000-old1.jsonl"
    old_path.write_text(json.dumps(old_msg) + "\n", encoding="utf-8")

    # 写一条新会话
    new_sid = archive.new_session_id()
    archive.append_message(new_sid, _user_text("new"))

    removed = archive.cleanup_expired(days=30)
    assert removed == 1
    assert not old_path.exists()
    assert archive.session_path(new_sid).exists()


# --- AC12 长间隔提醒 ----------------------------------------------------------


def test_gap_reminder_inserted_once(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    archive.directory.mkdir(parents=True, exist_ok=True)
    sid = archive.new_session_id()
    path = archive.session_path(sid)

    # 写一条 ts=2 天前的消息
    old_ts = (datetime.now().astimezone() - timedelta(days=2)).isoformat()
    record = {
        "type": "message",
        "ts": old_ts,
        "role": "user",
        "content": [{"type": "text", "text": "hi"}],
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = archive.restore(sid)
    assert result.inserted_gap_reminder is True
    assert len(result.messages) == 2
    last = result.messages[-1]
    assert last.role == "user"
    assert any(
        isinstance(b, TextBlock) and b.text.startswith(GAP_REMINDER_TAG)
        for b in last.content
    )

    # 再次恢复：应该不会再次插入
    result2 = archive.restore(sid)
    assert result2.inserted_gap_reminder is False


# --- ID / 路径 ----------------------------------------------------------------


def test_new_session_id_format(tmp_path: Path):
    archive = SessionArchive(tmp_path)
    sid = archive.new_session_id()
    parts = sid.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 6 and parts[1].isdigit()
    assert len(parts[2]) == 4
    # 同时间多次生成不冲突
    seen = {archive.new_session_id() for _ in range(10)}
    assert len(seen) == 10
