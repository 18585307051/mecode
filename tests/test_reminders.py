"""<system-reminder> 注入逻辑的单元测试。

覆盖 spec AC6 / AC7 / inject_into_user_text 行为。
"""

from mewcode.system_prompt import (
    build_plan_reminder,
    inject_into_user_text,
)
from mewcode.system_prompt.reminders import (
    PLAN_REMINDER_FULL,
    PLAN_REMINDER_SHORT,
)


# ---------- build_plan_reminder ----------


def test_count_0_不注入() -> None:
    assert build_plan_reminder(0) == ""


def test_count_负数_不注入() -> None:
    assert build_plan_reminder(-1) == ""


def test_count_1_完整版() -> None:
    """第 1 轮注入完整 reminder。"""
    r = build_plan_reminder(1)
    assert r == PLAN_REMINDER_FULL
    assert "Plan Mode" in r
    assert len(r) > 80


def test_count_6_完整版() -> None:
    """第 6 轮（间隔 5 后）注入完整 reminder。"""
    assert build_plan_reminder(6) == PLAN_REMINDER_FULL


def test_count_11_完整版() -> None:
    """第 11 轮注入完整 reminder。"""
    assert build_plan_reminder(11) == PLAN_REMINDER_FULL


def test_count_16_完整版() -> None:
    assert build_plan_reminder(16) == PLAN_REMINDER_FULL


def test_count_2到5_精简版() -> None:
    """第 2-5 轮注入精简 reminder。"""
    for c in (2, 3, 4, 5):
        r = build_plan_reminder(c)
        assert r == PLAN_REMINDER_SHORT, f"轮次 {c} 应当是 SHORT"
        assert "Plan Mode" in r
        assert len(r) <= 80


def test_count_7到10_精简版() -> None:
    for c in (7, 8, 9, 10):
        assert build_plan_reminder(c) == PLAN_REMINDER_SHORT


def test_full_含system_reminder标签() -> None:
    """完整 reminder 含 <system-reminder> 标签。"""
    assert PLAN_REMINDER_FULL.startswith("<system-reminder>")
    assert PLAN_REMINDER_FULL.endswith("</system-reminder>")


def test_short_含system_reminder标签() -> None:
    """精简 reminder 也含 <system-reminder> 标签。"""
    assert PLAN_REMINDER_SHORT.startswith("<system-reminder>")
    assert PLAN_REMINDER_SHORT.endswith("</system-reminder>")


# ---------- inject_into_user_text ----------


def test_inject_拼接顺序() -> None:
    """reminder 在 user_text 之前，用 \\n\\n 分隔。"""
    result = inject_into_user_text("REMINDER", "hi")
    assert result == "REMINDER\n\nhi"


def test_inject_空reminder_直接返回原文() -> None:
    """reminder 为空时不改动 user_text。"""
    assert inject_into_user_text("", "hi") == "hi"


def test_inject_user_text含换行() -> None:
    """user_text 内部的换行要保留。"""
    result = inject_into_user_text("R", "line1\nline2")
    assert result == "R\n\nline1\nline2"
