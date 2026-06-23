"""第九阶段 F17 / F18：MemoryManager 注入与 operation 应用测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from mewcode.memory.manager import MemoryManager
from mewcode.memory.notes import list_notes, scope_root
from mewcode.memory.updater import MemoryOperation


class _FakeProvider:
    protocol = "openai"
    model = "test"

    def stream_chat(self, *args, **kwargs):
        async def _gen():
            if False:
                yield None  # pragma: no cover
        return _gen()


class _FakeSession:
    def __init__(self, cwd: Path):
        self.provider = _FakeProvider()
        self.session_id = "20260101-000000-aaaa"
        self.system_prompt = ""


def _patch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


# --- AC17 user / project 分开存 -----------------------------------------------


def test_create_writes_to_correct_scope_project(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    op = MemoryOperation(
        op="create",
        scope="user",  # 故意写错
        category="project_knowledge",
        body="测试命令是 pytest -q",
    )
    changes, ok = mgr._apply_operation(op, sess.session_id)
    assert ok is True
    # category=project_knowledge → scope 应被纠正成 project
    assert "project" in changes
    assert "user" not in changes
    notes = list_notes(scope_root(project, "project"))
    assert len(notes) == 1
    assert notes[0].body.startswith("测试命令")


def test_create_writes_to_correct_scope_user(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    fake_home = _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    op = MemoryOperation(
        op="create",
        scope=None,
        category="preference",
        body="用中文回答",
        tags=["lang"],
    )
    changes, ok = mgr._apply_operation(op, sess.session_id)
    assert ok is True
    assert "user" in changes
    user_notes = list_notes(scope_root(project, "user"))
    assert len(user_notes) == 1
    assert "中文" in user_notes[0].body


# --- update / delete ----------------------------------------------------------


def test_update_existing_note(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    # 先 create
    create_op = MemoryOperation(
        op="create",
        category="project_knowledge",
        body="第一版内容",
    )
    mgr._apply_operation(create_op, sess.session_id)
    notes = list_notes(scope_root(project, "project"))
    assert len(notes) == 1
    note_id = notes[0].id

    # update
    update_op = MemoryOperation(
        op="update",
        id=note_id,
        category="project_knowledge",
        body="第二版内容",
    )
    changes, ok = mgr._apply_operation(update_op, sess.session_id)
    assert ok is True
    notes2 = list_notes(scope_root(project, "project"))
    assert len(notes2) == 1
    assert "第二版" in notes2[0].body


def test_delete_existing_note(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    create_op = MemoryOperation(
        op="create",
        category="reference",
        body="link: https://example.com",
    )
    mgr._apply_operation(create_op, sess.session_id)
    notes = list_notes(scope_root(project, "project"))
    note_id = notes[0].id

    delete_op = MemoryOperation(op="delete", id=note_id)
    changes, ok = mgr._apply_operation(delete_op, sess.session_id)
    assert ok is True
    assert "project" in changes
    notes2 = list_notes(scope_root(project, "project"))
    assert notes2 == []


# --- AC19 / AC20 注入 + hash --------------------------------------------------


def test_load_context_includes_both_scopes(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    mgr._apply_operation(
        MemoryOperation(op="create", category="preference", body="prefer-zh"),
        sess.session_id,
    )
    mgr._apply_operation(
        MemoryOperation(
            op="create", category="project_knowledge", body="run pytest -q"
        ),
        sess.session_id,
    )
    # 主动 rebuild index（在 update_once 路径中是自动的；单测里手动）
    from mewcode.memory.index import rebuild_index

    rebuild_index(scope_root(project, "user"), "user")
    rebuild_index(scope_root(project, "project"), "project")

    ctx = mgr.load_context()
    assert ctx.text is not None
    assert "项目记忆" in ctx.text
    assert "用户记忆" in ctx.text
    assert "prefer-zh" in ctx.text
    assert "run pytest -q" in ctx.text


def test_refresh_only_when_hash_changes(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    # 准备一条记忆
    mgr._apply_operation(
        MemoryOperation(op="create", category="preference", body="prefer-zh"),
        sess.session_id,
    )
    from mewcode.memory.index import rebuild_index

    rebuild_index(scope_root(project, "user"), "user")

    calls: list[str | None] = []

    def rebuild(memory=None):
        calls.append(memory)

    # 第一次：hash 从空 → 有内容，必触发
    mgr.load_context()  # 设置 _last_hash
    # 把 _last_hash 强制清空，模拟启动初始
    mgr._last_hash = ""
    changed = mgr.refresh_system_prompt_if_changed(rebuild)
    assert changed is True
    assert len(calls) == 1

    # 第二次：内容未变，应跳过
    changed2 = mgr.refresh_system_prompt_if_changed(rebuild)
    assert changed2 is False
    assert len(calls) == 1


# --- schedule_update 异常容错 ------------------------------------------------


def test_schedule_update_swallows_exception(tmp_path: Path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _patch_home(tmp_path, monkeypatch)
    mgr = MemoryManager(project)
    sess = _FakeSession(project)

    async def _runner():
        with patch.object(
            mgr,
            "update_once",
            side_effect=RuntimeError("boom"),
        ):
            task = mgr.schedule_update(sess, recent_messages=[])
            assert task is not None
            await task  # 应该正常 await 完成（异常被吞）

    asyncio.run(_runner())
