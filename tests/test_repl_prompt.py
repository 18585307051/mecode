"""REPL prompt 字符串单测（spec 第十阶段 F14 / AC16）。

_make_prompt(session) 根据 session.mode 返回：
- PLAN 模式 → `[PLAN] > `
- 其他模式（do / default / 缺失字段）→ `> `

仅 mode 一项参与；thinking / permission 不在 PROMPT 上显示。
"""

from __future__ import annotations

from types import SimpleNamespace

from mewcode.repl.main_loop import _make_prompt


def test_plan_prefix() -> None:
    """session.mode == 'plan' → `[PLAN] > `。"""
    session = SimpleNamespace(mode="plan")
    assert _make_prompt(session) == "[PLAN] > "


def test_do_no_prefix() -> None:
    """session.mode == 'do' → `> `（无前缀）。"""
    session = SimpleNamespace(mode="do")
    assert _make_prompt(session) == "> "


def test_default_no_prefix() -> None:
    """session.mode == 'default' → `> `（视为非 plan）。"""
    session = SimpleNamespace(mode="default")
    assert _make_prompt(session) == "> "


def test_missing_mode_fallback() -> None:
    """session 上没有 mode 字段时兜底为 `> `。"""
    session = SimpleNamespace()
    assert _make_prompt(session) == "> "


def test_thinking_does_not_change_prompt() -> None:
    """spec F14：thinking on/off 不影响 PROMPT。"""
    session = SimpleNamespace(mode="do", thinking_enabled=True)
    assert _make_prompt(session) == "> "
    session.mode = "plan"
    assert _make_prompt(session) == "[PLAN] > "


def test_unknown_mode_treated_as_default() -> None:
    """非 plan 的任意 mode 值都不显示前缀。"""
    session = SimpleNamespace(mode="weird_mode")
    assert _make_prompt(session) == "> "
