"""第九阶段 F1 / F2：项目指令优先级 + @include 展开测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.instructions.loader import (
    InstructionsLoader,
    _MAX_INCLUDE_DEPTH,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstructionsLoader:
    """构造 loader，并把用户 home 重定向到 tmp_path/home，避免污染真实 ~/。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    project = tmp_path / "project"
    project.mkdir()
    return InstructionsLoader(cwd=project)


# --- AC1 三层优先级 -----------------------------------------------------------


def test_priority_local_project_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loader = _make_loader(tmp_path, monkeypatch)

    home = Path.home()
    project = loader._cwd

    _write(home / ".mewcode" / "AGENTS.md", "USER LAYER\n")
    _write(project / "AGENTS.md", "PROJECT LAYER\n")
    _write(project / ".mewcode" / "AGENTS.local.md", "LOCAL LAYER\n")

    text = loader.load_all()
    assert text is not None
    assert text.find("LOCAL LAYER") < text.find("PROJECT LAYER") < text.find(
        "USER LAYER"
    ), "本地级应在最前，用户级应在最后"


def test_no_layers_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loader = _make_loader(tmp_path, monkeypatch)
    assert loader.load_all() is None


# --- AC2 include 展开 ---------------------------------------------------------


def test_include_expands_with_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    _write(project / "docs" / "rules.md", "RULE BODY\n")
    _write(
        project / "AGENTS.md",
        "before\n@include docs/rules.md\nafter\n",
    )

    text = loader.load_all() or ""
    assert "RULE BODY" in text
    assert "<!-- begin include: docs/rules.md -->" in text
    assert "<!-- end include: docs/rules.md -->" in text
    # 展开顺序：before 在 begin 前；after 在 end 后
    assert text.index("before") < text.index("<!-- begin include")
    assert text.index("<!-- end include") < text.index("after")


# --- AC3 include 防环 ---------------------------------------------------------


def test_include_cycle_does_not_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    _write(project / "AGENTS.md", "@include a.md\n")
    _write(project / "a.md", "A1\n@include b.md\nA2\n")
    _write(project / "b.md", "B1\n@include a.md\nB2\n")

    text = loader.load_all() or ""
    # B 中再 include a.md 时应被防环跳过；不会重复出现 A1
    assert text.count("A1") == 1
    assert "B1" in text
    captured = capsys.readouterr().out
    assert "环路" in captured


# --- AC4 include 深度限制 -----------------------------------------------------


def test_include_depth_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    # 主文件 -> 1.md -> 2.md -> 3.md -> 4.md（4.md 应被深度限制拒绝）
    _write(project / "AGENTS.md", "@include 1.md\n")
    _write(project / "1.md", "L1\n@include 2.md\n")
    _write(project / "2.md", "L2\n@include 3.md\n")
    _write(project / "3.md", "L3\n@include 4.md\n")
    _write(project / "4.md", "L4-SHOULD-NOT-APPEAR\n")

    text = loader.load_all() or ""
    assert "L1" in text
    assert "L2" in text
    assert "L3" in text
    assert "L4-SHOULD-NOT-APPEAR" not in text
    captured = capsys.readouterr().out
    assert f"{_MAX_INCLUDE_DEPTH}" in captured
    assert "嵌套深度" in captured


# --- AC5 include 越界拦截 -----------------------------------------------------


def test_include_outside_project_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE-CONTENT\n", encoding="utf-8")
    _write(project / "AGENTS.md", "@include ../outside.md\n")

    text = loader.load_all() or ""
    assert "OUTSIDE-CONTENT" not in text
    captured = capsys.readouterr().out
    assert "越出允许目录" in captured


def test_include_missing_file_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    _write(project / "AGENTS.md", "head\n@include not-exist.md\ntail\n")
    text = loader.load_all() or ""
    assert "head" in text and "tail" in text
    captured = capsys.readouterr().out
    assert "不存在" in captured


def test_include_non_utf8_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    loader = _make_loader(tmp_path, monkeypatch)
    project = loader._cwd

    bad = project / "bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe\x00broken\n")
    _write(project / "AGENTS.md", "before\n@include bad.md\nafter\n")

    text = loader.load_all() or ""
    assert "before" in text and "after" in text
    captured = capsys.readouterr().out
    assert "非 UTF-8" in captured
