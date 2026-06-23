"""LOCAL/STATEFUL 视图命令单测（spec 第十阶段 F6 / F8 / F9 / F10 / F11）。

覆盖：
- /help 三段分组、隐藏命令不出现、/exit /quit 在 STATEFUL 段
- /status 六节标题齐 + 子系统未启用降级
- /session list/current/new/resume + resume <missing>
- /memory show/list user filter/refresh
- /permission 主名 + 旧名别名兼容

通过 stub 各子系统（archive / memory_manager / policy / instructions /
compactor）来隔离对真实文件 IO 与 LLM 的依赖。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest

from mewcode.commands import (
    COMMANDS,
    CommandContext,
    CommandType,
    dispatch,
    register_builtins,
    unregister_all,
)


# ---------- 通用 stub ----------


class _StubRenderer:
    """记录所有调用的渲染器替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record

    def has(self, method: str) -> bool:
        return any(c[0] == method for c in self.calls)

    def first(self, method: str) -> tuple:
        for c in self.calls:
            if c[0] == method:
                return c
        raise AssertionError(f"未调用 {method}")

    def infos(self) -> list[str]:
        return [c[1][0] for c in self.calls if c[0] == "print_info"]


class _StubProvider:
    def __init__(self, protocol="anthropic", model="m"):
        self.protocol = protocol
        self.model = model

    async def stream_chat(self, *_a, **_kw):  # pragma: no cover
        if False:
            yield


class _StubSession:
    def __init__(self) -> None:
        self.provider = _StubProvider()
        self.current_provider_name = "alpha"
        self.thinking_enabled = False
        self.mode = "do"
        self.session_id = "20260623-101530-aaaa"
        self.messages: list = []
        self.cleared = False

    def clear(self) -> None:
        self.messages.clear()
        self.cleared = True
        # 模拟真实 Session 的 rotate 副作用
        self.session_id = "20260623-200000-bbbb"


@dataclass
class _StubSummary:
    session_id: str
    path: object
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    bad_lines: int = 0


@dataclass
class _StubRestoreResult:
    session_id: str
    path: object
    messages: list
    summary: object = None
    bad_lines: int = 0
    truncated: bool = False
    inserted_gap_reminder: bool = False
    restored: bool = True


class _StubArchive:
    def __init__(self, summaries=None, restore_map=None) -> None:
        self._summaries = summaries or []
        self._restore = restore_map or {}
        self.rotate_called = False
        self.attached: list = []

    def scan_summaries(self):
        return list(self._summaries)

    def session_path(self, sid: str):
        return f"/fake/.mewcode/sessions/{sid}.jsonl"

    def load_by_id(self, sid: str) -> _StubRestoreResult:
        if sid in self._restore:
            return self._restore[sid]
        return _StubRestoreResult(
            session_id=sid, path=None, messages=[], restored=False,
        )

    def find_by_prefix(self, prefix: str) -> list[str]:
        return [s.session_id for s in self._summaries if s.session_id.startswith(prefix)]

    def attach(self, session, result) -> None:
        # 模拟 archive.attach 把 messages 替换、session_id 改写
        session.messages[:] = list(result.messages)
        session.session_id = result.session_id
        self.attached.append((session, result))

    def rotate(self, session) -> str:
        self.rotate_called = True
        session.session_id = "20260623-999999-cccc"
        return session.session_id


class _StubMemoryManager:
    def __init__(self, combined: str = "", notes=None) -> None:
        self._combined = combined
        self._notes = notes or []
        self.refresh_called = False

    def get_combined_index_text(self) -> str:
        return self._combined

    def list_notes(self, scope=None):
        if scope is None:
            return list(self._notes)
        return [n for n in self._notes if n.get("scope") == scope]

    async def refresh(self, rebuild_system_prompt=None) -> bool:
        self.refresh_called = True
        return True


class _StubPolicy:
    mode = "default"
    all_allow: list = []
    all_deny: list = []
    session_allow: list = []
    session_deny: list = []


class _StubLoader:
    def loaded_layers(self):
        from dataclasses import dataclass
        @dataclass
        class _L:
            name: str
            display_path: str
            bytes_len: int

        return [_L("项目级", "./AGENTS.md", 128)]


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    """每个用例在干净注册表 + 注册 builtin 之上跑，跑完恢复。"""
    snapshot = dict(COMMANDS)
    unregister_all()
    register_builtins()
    try:
        yield
    finally:
        unregister_all()
        COMMANDS.update(snapshot)


def _make_ctx(**kwargs) -> CommandContext:
    return CommandContext(
        session=kwargs.pop("session", _StubSession()),
        app_config=None,  # type: ignore[arg-type]
        renderer=kwargs.pop("renderer", _StubRenderer()),
        **kwargs,
    )


# ---------- /help 分组 ----------


@pytest.mark.asyncio
async def test_help_groups_three_sections() -> None:
    ctx = _make_ctx()
    await dispatch("/help", ctx)
    assert ctx.renderer.has("print_command_groups")
    call = ctx.renderer.first("print_command_groups")
    grouped = call[1][0]
    assert set(grouped.keys()) == {
        CommandType.LOCAL, CommandType.STATEFUL, CommandType.PROMPT,
    }
    # 每段都非空
    assert grouped[CommandType.LOCAL]
    assert grouped[CommandType.STATEFUL]
    assert grouped[CommandType.PROMPT]


@pytest.mark.asyncio
async def test_help_excludes_hidden() -> None:
    """/think /provider /providers /instructions /permissions 不出现。"""
    ctx = _make_ctx()
    await dispatch("/help", ctx)
    grouped = ctx.renderer.first("print_command_groups")[1][0]
    all_names = {
        cmd.name
        for bucket in grouped.values()
        for cmd in bucket
    }
    for hidden in ("think", "provider", "providers", "instructions"):
        assert hidden not in all_names, f"{hidden} 不应出现在 /help"
    # /permissions 是别名，主名 /permission 应出现
    assert "permission" in all_names
    # /exit 在 STATEFUL 段（可见）
    stateful_names = {c.name for c in grouped[CommandType.STATEFUL]}
    assert "exit" in stateful_names


# ---------- /status 仪表盘 ----------


@pytest.mark.asyncio
async def test_status_six_sections() -> None:
    ctx = _make_ctx(
        policy=_StubPolicy(),
        memory_manager=_StubMemoryManager(combined="some text\nrow2"),
        instructions=_StubLoader(),
        archive=_StubArchive(),
    )
    await dispatch("/status", ctx)
    snap = ctx.renderer.first("print_status")[1][0]
    expected = {"供应商", "模式", "会话", "权限", "长期记忆", "项目指令"}
    assert expected.issubset(snap.keys())


@pytest.mark.asyncio
async def test_status_handles_disabled_subsystems() -> None:
    """所有可选子系统为 None 时仍输出六节，每节显示『未启用』。"""
    ctx = _make_ctx()  # policy/memory/instructions 都是 None
    await dispatch("/status", ctx)
    snap = ctx.renderer.first("print_status")[1][0]
    for key in ("权限", "长期记忆", "项目指令"):
        assert any("未启用" in line for line in snap[key])


# ---------- /session 子命令 ----------


def _make_summary(sid: str, n: int = 3, hours_ago: int = 1) -> _StubSummary:
    now = datetime.now()
    return _StubSummary(
        session_id=sid,
        path=f"/fake/{sid}.jsonl",
        title=f"标题 {sid}",
        message_count=n,
        created_at=now - timedelta(hours=hours_ago + 1),
        updated_at=now - timedelta(hours=hours_ago),
    )


@pytest.mark.asyncio
async def test_session_list() -> None:
    summaries = [_make_summary(f"20260623-1015{i:02d}-aaaa", n=i+1) for i in range(3)]
    archive = _StubArchive(summaries=summaries)
    session = _StubSession()
    session.session_id = summaries[0].session_id
    ctx = _make_ctx(session=session, archive=archive)
    await dispatch("/session list", ctx)
    assert ctx.renderer.has("print_session_list")
    rows = ctx.renderer.first("print_session_list")[1][0]
    assert len(rows) == 3
    # current_id 关键字
    call = ctx.renderer.first("print_session_list")
    assert call[2].get("current_id") == summaries[0].session_id or session.session_id in str(call)


@pytest.mark.asyncio
async def test_session_default_is_list() -> None:
    """/session 不带子命令默认 list。"""
    archive = _StubArchive(summaries=[_make_summary("20260623-aaaaaa-bbbb")])
    ctx = _make_ctx(archive=archive)
    await dispatch("/session", ctx)
    assert ctx.renderer.has("print_session_list")


@pytest.mark.asyncio
async def test_session_current() -> None:
    session = _StubSession()
    ctx = _make_ctx(session=session, archive=_StubArchive())
    await dispatch("/session current", ctx)
    assert ctx.renderer.has("print_session_current")
    info = ctx.renderer.first("print_session_current")[1][0]
    assert session.session_id in str(info)


@pytest.mark.asyncio
async def test_session_new_rotates() -> None:
    session = _StubSession()
    session.messages = ["a", "b"]
    archive = _StubArchive()
    ctx = _make_ctx(session=session, archive=archive)
    await dispatch("/session new", ctx)
    # session.clear() 应被调用
    assert session.cleared is True
    assert session.messages == []
    # session_id 应已变化（_StubSession.clear 模拟 rotate）
    assert session.session_id != "20260623-101530-aaaa"


@pytest.mark.asyncio
async def test_session_resume_loads() -> None:
    sid = "20260623-111111-aaaa"
    summary = _make_summary(sid, n=5)
    restored = _StubRestoreResult(
        session_id=sid, path=None,
        messages=["m1", "m2", "m3", "m4", "m5"],
        restored=True,
    )
    archive = _StubArchive(
        summaries=[summary], restore_map={sid: restored},
    )
    session = _StubSession()
    ctx = _make_ctx(session=session, archive=archive)
    await dispatch(f"/session resume {sid}", ctx)
    assert session.session_id == sid
    assert session.messages == ["m1", "m2", "m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_session_resume_missing_id() -> None:
    archive = _StubArchive()
    session = _StubSession()
    original_id = session.session_id
    ctx = _make_ctx(session=session, archive=archive)
    await dispatch("/session resume nonexistent", ctx)
    # 不切换
    assert session.session_id == original_id
    # 给出找不到提示
    assert any("找不到" in s for s in ctx.renderer.infos())


@pytest.mark.asyncio
async def test_session_resume_prefix_match() -> None:
    """前缀模糊匹配命中单条 → 切换。"""
    sid = "20260623-111111-aaaa"
    summary = _make_summary(sid)
    restored = _StubRestoreResult(
        session_id=sid, path=None, messages=["only"], restored=True,
    )
    archive = _StubArchive(
        summaries=[summary], restore_map={sid: restored},
    )
    session = _StubSession()
    ctx = _make_ctx(session=session, archive=archive)
    await dispatch("/session resume 20260623-1111", ctx)
    assert session.session_id == sid


# ---------- /memory 子命令 ----------


@pytest.mark.asyncio
async def test_memory_show() -> None:
    mm = _StubMemoryManager(combined="## 长期记忆\n\n### 项目记忆\n- foo\n")
    ctx = _make_ctx(memory_manager=mm)
    await dispatch("/memory show", ctx)
    assert ctx.renderer.has("print_memory_index")
    text = ctx.renderer.first("print_memory_index")[1][0]
    assert "长期记忆" in text


@pytest.mark.asyncio
async def test_memory_default_is_show() -> None:
    mm = _StubMemoryManager(combined="xx")
    ctx = _make_ctx(memory_manager=mm)
    await dispatch("/memory", ctx)
    assert ctx.renderer.has("print_memory_index")


@pytest.mark.asyncio
async def test_memory_list_all() -> None:
    notes = [
        {"note_id": "n1", "scope": "user", "category": "preference",
         "updated_at": "2026-06-23T10:00:00", "title": "中文回答"},
        {"note_id": "n2", "scope": "project", "category": "project_knowledge",
         "updated_at": "2026-06-23T11:00:00", "title": "用 pytest"},
    ]
    mm = _StubMemoryManager(notes=notes)
    ctx = _make_ctx(memory_manager=mm)
    await dispatch("/memory list", ctx)
    assert ctx.renderer.has("print_note_list")
    rows = ctx.renderer.first("print_note_list")[1][0]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_memory_list_user_filter() -> None:
    notes = [
        {"note_id": "n1", "scope": "user", "category": "preference",
         "updated_at": "x", "title": "a"},
        {"note_id": "n2", "scope": "project", "category": "project_knowledge",
         "updated_at": "x", "title": "b"},
    ]
    mm = _StubMemoryManager(notes=notes)
    ctx = _make_ctx(memory_manager=mm)
    await dispatch("/memory list user", ctx)
    rows = ctx.renderer.first("print_note_list")[1][0]
    assert len(rows) == 1
    assert rows[0]["scope"] == "user"


@pytest.mark.asyncio
async def test_memory_refresh_calls_manager() -> None:
    mm = _StubMemoryManager()
    ctx = _make_ctx(memory_manager=mm)
    await dispatch("/memory refresh", ctx)
    assert mm.refresh_called is True


# ---------- /permission 主名 + 别名 ----------


@pytest.mark.asyncio
async def test_permission_main_name() -> None:
    policy = _StubPolicy()
    ctx = _make_ctx(policy=policy)
    await dispatch("/permission show", ctx)
    # /permission show 调 print_info 至少一次
    assert ctx.renderer.has("print_info")


@pytest.mark.asyncio
async def test_permission_alias_compat() -> None:
    """旧名 /permissions 作为别名仍能命中同一 handler。"""
    policy = _StubPolicy()
    ctx = _make_ctx(policy=policy)
    await dispatch("/permissions show", ctx)
    assert ctx.renderer.has("print_info")


@pytest.mark.asyncio
async def test_help_lists_only_permission_main_name() -> None:
    """/help 输出含 /permission 主名，不单独列 /permissions 别名行。"""
    ctx = _make_ctx()
    await dispatch("/help", ctx)
    grouped = ctx.renderer.first("print_command_groups")[1][0]
    stateful_names = {c.name for c in grouped[CommandType.STATEFUL]}
    assert "permission" in stateful_names
    assert "permissions" not in stateful_names
