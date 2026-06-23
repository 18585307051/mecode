"""SlashCommandCompleter 单测（spec 第十阶段 F7 / AC8）。

约束：
- 仅当行以 / 开头且光标在第一个空格之前时返回候选。
- 候选 = visible_command_names()；别名不参与；隐藏命令不出现。
- 空 prefix（仅 `/`）→ 与 spec F7 一致，不返回任何候选。
- 多匹配返回多条；单匹配返回单条。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mewcode.commands import COMMANDS, register_builtins, unregister_all
from mewcode.repl.completer import SlashCommandCompleter


class _FakeDocument:
    """最小化的 Document 替身，只暴露 text_before_cursor。"""

    def __init__(self, text: str) -> None:
        self.text_before_cursor = text


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    snapshot = dict(COMMANDS)
    unregister_all()
    register_builtins()
    try:
        yield
    finally:
        unregister_all()
        COMMANDS.update(snapshot)


def _candidate_names(text: str) -> list[str]:
    """跑一次 Completer，返回所有候选 name 文本。"""
    comp = SlashCommandCompleter()
    return [c.text for c in comp.get_completions(_FakeDocument(text), None)]


def test_single_match() -> None:
    """/se → 候选含 session（在第十阶段可见命令里 se 前缀仅命中 session）。"""
    cands = _candidate_names("/se")
    assert "session" in cands
    # 单匹配：只命中 session（其他可见命令无 se 前缀）
    assert cands == ["session"]


def test_multi_match() -> None:
    """/p → 候选含 permission 与 plan。"""
    cands = _candidate_names("/p")
    assert "permission" in cands
    assert "plan" in cands


def test_hidden_excluded() -> None:
    """/t → 候选不含 think（hidden=True）。"""
    cands = _candidate_names("/t")
    assert "think" not in cands


def test_no_complete_after_space() -> None:
    """参数区不补全：/help xxx → 候选为空。"""
    cands = _candidate_names("/help xxx")
    assert cands == []


def test_no_complete_for_plain_text() -> None:
    """非斜杠开头 → 候选为空。"""
    assert _candidate_names("xxx") == []
    assert _candidate_names("hello") == []


def test_no_complete_for_empty_prefix() -> None:
    """spec F7：仅 `/`（空 prefix）→ 不返回任何候选。

    避免按 Tab 弹出全表噪音；用户要看全表请用 /help。
    """
    cands = _candidate_names("/")
    assert cands == []


def test_aliases_not_in_candidates() -> None:
    """spec F7：候选只列 name，不列 alias。/permissions 是 /permission 的别名，不出现。"""
    cands = _candidate_names("/per")
    assert "permission" in cands
    assert "permissions" not in cands


def test_case_insensitive_prefix() -> None:
    """补全时 prefix 应按小写匹配（命令注册也按小写）。"""
    # 输入 /SE → 应命中 session
    cands = _candidate_names("/SE")
    assert "session" in cands
