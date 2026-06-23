"""第九阶段 F13 / F16：MemoryNote frontmatter 与原子写入测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mewcode.memory.notes import (
    NOTES_DIRNAME,
    MemoryNote,
    delete_note_safe,
    list_notes,
    new_note_id,
    note_from_markdown,
    note_to_markdown,
    write_note_atomic,
)


def _make_note(category: str = "project_knowledge", scope: str = "project") -> MemoryNote:
    now = datetime.now().astimezone()
    return MemoryNote(
        id=new_note_id(now),
        scope=scope,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        source_session="20260101-000000-aaaa",
        tags=["unit", "test"],
        body="项目跑测试用 `pytest -q`。\n",
    )


def test_note_frontmatter_roundtrip(tmp_path: Path):
    note = _make_note()
    target = write_note_atomic(note, tmp_path)
    assert target.is_file()

    parsed = note_from_markdown(target)
    assert parsed.id == note.id
    assert parsed.scope == note.scope
    assert parsed.category == note.category
    assert parsed.tags == note.tags
    assert "pytest -q" in parsed.body


def test_note_to_markdown_contains_frontmatter_keys():
    note = _make_note()
    text = note_to_markdown(note)
    for key in [
        "id:",
        "scope:",
        "category:",
        "created_at:",
        "updated_at:",
        "source_session:",
        "tags:",
    ]:
        assert key in text


def test_list_notes_skips_broken(tmp_path: Path, capsys: pytest.CaptureFixture):
    note = _make_note()
    write_note_atomic(note, tmp_path)
    bad = tmp_path / NOTES_DIRNAME / "not-a-note.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")

    items = list_notes(tmp_path)
    assert len(items) == 1
    assert items[0].id == note.id
    captured = capsys.readouterr().out
    assert "跳过损坏的笔记" in captured


def test_delete_note_safe_blocks_traversal(tmp_path: Path):
    note = _make_note()
    write_note_atomic(note, tmp_path)
    # 非法 id：包含路径穿越
    assert delete_note_safe("../something", tmp_path) is False
    # 非法 id：不匹配 mem_xxx 正则
    assert delete_note_safe("evil$$", tmp_path) is False
    # 合法删除
    assert delete_note_safe(note.id, tmp_path) is True
    assert not (tmp_path / NOTES_DIRNAME / f"{note.id}.md").exists()


def test_note_from_markdown_invalid_scope(tmp_path: Path):
    target = tmp_path / "x.md"
    target.write_text(
        "---\n"
        "id: mem_20260101_000000_aaaa\n"
        "scope: nope\n"
        "category: preference\n"
        "created_at: 2026-01-01T00:00:00+08:00\n"
        "updated_at: 2026-01-01T00:00:00+08:00\n"
        "source_session: x\n"
        "tags: []\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        note_from_markdown(target)
