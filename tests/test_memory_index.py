"""第九阶段 F16：memory index 限制与裁剪测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from mewcode.memory.index import (
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    build_index,
    rebuild_index,
)
from mewcode.memory.notes import (
    MemoryNote,
    new_note_id,
    write_note_atomic,
)


def _note(category: str, body: str, days_ago: int = 0) -> MemoryNote:
    ts = datetime.now().astimezone() - timedelta(days=days_ago)
    return MemoryNote(
        id=new_note_id(ts),
        scope="project",
        category=category,  # type: ignore[arg-type]
        created_at=ts,
        updated_at=ts,
        source_session="s",
        tags=[],
        body=body,
    )


def test_build_index_groups_by_category():
    notes = [
        _note("preference", "p1"),
        _note("project_knowledge", "k1"),
        _note("correction", "c1"),
        _note("reference", "r1"),
    ]
    text = build_index(notes, scope="project")
    # 顺序：correction > preference > project_knowledge > reference
    assert text.index("纠正反馈") < text.index("用户偏好")
    assert text.index("用户偏好") < text.index("项目知识")
    assert text.index("项目知识") < text.index("参考资料")


def test_build_index_sorts_by_updated_desc():
    new_note = _note("preference", "newer", days_ago=0)
    old_note = _note("preference", "older", days_ago=10)
    text = build_index([old_note, new_note], scope="user")
    # 新条目应排在前
    assert text.index("newer") < text.index("older")


def test_index_line_limit():
    # 200 行 = 远多于实际可放下的笔记数；构造 600 条，触发裁剪
    notes = [_note("reference", f"line{i}", days_ago=i) for i in range(600)]
    text = build_index(notes, scope="project")
    line_count = text.count("\n")
    assert line_count <= MAX_INDEX_LINES
    assert "省略" in text


def test_index_byte_limit_under_25k():
    # 让单条 body 偏长，配合大量条目，触发字节限制
    big_body = "x" * 200
    notes = [
        _note("reference", big_body, days_ago=i) for i in range(2000)
    ]
    text = build_index(notes, scope="project")
    assert len(text.encode("utf-8")) <= MAX_INDEX_BYTES


def test_index_priority_keeps_correction_first():
    correction = _note("correction", "C must keep")
    refs = [_note("reference", f"R-{i}", days_ago=i) for i in range(500)]
    text = build_index([*refs, correction], scope="project")
    # correction 必须保留
    assert "C must keep" in text


def test_rebuild_index_writes_file(tmp_path: Path):
    # 先写一个 note，然后 rebuild
    note = _note("preference", "be brief")
    write_note_atomic(note, tmp_path)
    text = rebuild_index(tmp_path, "project")
    index_path = tmp_path / "index.md"
    assert index_path.is_file()
    assert "be brief" in text
    assert index_path.read_text(encoding="utf-8") == text
