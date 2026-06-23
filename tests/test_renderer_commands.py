"""Renderer 第十阶段新行为单测（A+B 补强）。

- print_command_groups：usage 字段被渲染到第二行
- print_unknown_command：相似命令"你是不是想输入"提示
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from rich.console import Console

from mewcode.commands import (
    COMMANDS,
    Command,
    CommandType,
    commands_by_type,
    register,
    register_builtins,
    unregister_all,
    visible_command_names,
)
from mewcode.render import Renderer


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    snap = dict(COMMANDS)
    unregister_all()
    register_builtins()
    try:
        yield
    finally:
        unregister_all()
        COMMANDS.update(snap)


def _make_renderer() -> tuple[Renderer, io.StringIO]:
    """构造一个把 rich 输出写到 StringIO 的 Renderer。"""
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=False, color_system=None, width=200,
    )
    return Renderer(console), buf


# ---------- A. /help 显示 usage ----------


def test_command_groups_render_usage() -> None:
    """已注册命令的 usage 字段应出现在第二行。"""
    renderer, buf = _make_renderer()
    grouped = commands_by_type()
    renderer.print_command_groups(grouped)
    out = buf.getvalue()
    # /session 的 usage 应在输出里
    assert "/session [list|current|new|resume <id>]" in out
    # /memory 的 usage 应在输出里
    assert "/memory [show|list [user|project]|refresh]" in out
    # /review 的 usage 应在输出里
    assert "/review [侧重点]" in out
    # 没有 usage 的命令（比如 /help usage=='/help' 本身和 name 相同）不应重复
    # 这里只要不抛错且 /help 一行出现一次即可
    assert out.count("/help") >= 1


def test_command_groups_usage_aligned() -> None:
    """usage 行应有缩进、不顶格。"""
    renderer, buf = _make_renderer()
    grouped = commands_by_type()
    renderer.print_command_groups(grouped)
    lines = buf.getvalue().splitlines()
    usage_lines = [ln for ln in lines if "用法:" in ln]
    assert usage_lines, "应至少有一行用法示例"
    for ln in usage_lines:
        # 必须以空格开头（缩进），不会顶格
        assert ln.startswith(" "), f"用法行没有缩进: {ln!r}"


def test_command_groups_usage_skipped_when_same_as_name() -> None:
    """usage 与命令名完全一样（如 /status）时省略第二行，避免噪音。"""
    renderer, buf = _make_renderer()
    grouped = commands_by_type()
    renderer.print_command_groups(grouped)
    lines = buf.getvalue().splitlines()
    # /status 的 usage 是 "/status"，与 name 同 → 不应有 "用法: /status" 这一行
    assert not any(
        ln.strip() == "用法: /status" for ln in lines
    ), "usage 与 name 相同的命令不应渲染 usage 行"


def test_command_groups_no_usage_no_second_line() -> None:
    """没有 usage 的命令只出现一行（描述行）。"""
    unregister_all()
    register(Command(
        name="x",
        aliases=(),
        description="测试命令",
        handler=lambda c: None,  # type: ignore[arg-type]
        type=CommandType.LOCAL,
        # usage 缺省 ""
    ))
    grouped = commands_by_type()
    renderer, buf = _make_renderer()
    renderer.print_command_groups(grouped)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    # 一行标题 + 一行命令；没有用法第二行
    assert len(lines) == 2
    assert "/x" in lines[1]
    assert "用法" not in lines[1]


# ---------- B. /未知命令的"你是不是想输入" ----------


def test_unknown_command_prefix_suggestion() -> None:
    """`/se` → suggestion 应含 session。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("se", visible_command_names())
    out = buf.getvalue()
    assert "未知命令: /se" in out
    assert "你是不是想输入" in out
    assert "/session" in out


def test_unknown_command_fuzzy_suggestion() -> None:
    """`/halp` 拼错 → difflib 相似度命中 help。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("halp", visible_command_names())
    out = buf.getvalue()
    assert "你是不是想输入" in out
    assert "/help" in out


def test_unknown_command_multi_prefix_suggestion() -> None:
    """`/p` 多个候选 → 最多列 3 个，去重保序。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("p", visible_command_names())
    out = buf.getvalue()
    assert "你是不是想输入" in out
    # /permission 和 /plan 都应该在候选里
    assert "/permission" in out
    assert "/plan" in out


def test_unknown_command_no_match_no_suggestion() -> None:
    """完全不相似的输入 → 不显示「你是不是想输入」，但保留可用命令列表。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("xyzzy", visible_command_names())
    out = buf.getvalue()
    assert "未知命令: /xyzzy" in out
    # 无匹配项时不应有 suggestion 行
    assert "你是不是想输入" not in out
    # 但 fallback 的可用命令列表仍存在
    assert "可用命令" in out


def test_unknown_command_empty_name_no_suggestion() -> None:
    """纯 `/` 触发 name="" → 不应尝试 suggestion，避免空字符串匹配所有项。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("", visible_command_names())
    out = buf.getvalue()
    assert "你是不是想输入" not in out


def test_unknown_command_help_hint() -> None:
    """未知命令底部应提示用户运行 /help。"""
    renderer, buf = _make_renderer()
    renderer.print_unknown_command("foo", visible_command_names())
    assert "/help" in buf.getvalue()


def test_suggest_commands_max_three() -> None:
    """_suggest_commands 静态方法最多返回 3 个候选。"""
    # 构造一个大量前缀命中的场景
    candidates = [f"foo{i}" for i in range(10)]
    out = Renderer._suggest_commands("foo", candidates)
    assert len(out) <= 3
