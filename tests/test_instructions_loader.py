"""项目指令加载器单测（spec AC1-AC8, AC11, AC12, AC17）。"""

from pathlib import Path

import pytest

from mewcode.instructions.loader import InstructionsLoader, _read_layer


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch) -> Path:
    """构造独立 cwd + 重定向 ~ 到 tmp_path/home。"""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".mewcode").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return tmp_path


# ---------- 文件查找（AC1 / AC2 / AC17） ----------


def test_find_AGENTS_md_first(isolated_cwd: Path) -> None:
    """同时存在 AGENTS.md / CLAUDE.md → 选 AGENTS.md（spec AC1）。"""
    (isolated_cwd / "AGENTS.md").write_text("agents content", encoding="utf-8")
    (isolated_cwd / "CLAUDE.md").write_text("claude content", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None
    assert "agents content" in text
    assert "claude content" not in text
    assert "./AGENTS.md" in text


def test_find_CLAUDE_md_when_no_AGENTS(isolated_cwd: Path) -> None:
    """仅 CLAUDE.md → 加载 CLAUDE.md（spec AC17）。"""
    (isolated_cwd / "CLAUDE.md").write_text("claude only", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None
    assert "claude only" in text
    assert "./CLAUDE.md" in text


def test_find_mewcoderc_fallback(isolated_cwd: Path) -> None:
    """仅 .mewcoderc → 加载 .mewcoderc。"""
    (isolated_cwd / ".mewcoderc").write_text("rc rules", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None
    assert "rc rules" in text


def test_no_candidates_returns_none(isolated_cwd: Path) -> None:
    """三层全空 → load_all 返回 None（spec AC2 / AC3）。"""
    loader = InstructionsLoader(isolated_cwd)
    assert loader.load_all() is None


# ---------- 三层拼接（AC4 / AC5） ----------


def test_three_layers_order(isolated_cwd: Path) -> None:
    """三层都有内容 → 顺序为用户→项目→本地（spec AC4）。"""
    home = Path.home()
    (home / ".mewcode" / "AGENTS.md").write_text("USER LEVEL", encoding="utf-8")
    (isolated_cwd / "AGENTS.md").write_text("PROJECT LEVEL", encoding="utf-8")
    (isolated_cwd / ".mewcode" / "AGENTS.local.md").parent.mkdir(exist_ok=True)
    (isolated_cwd / ".mewcode" / "AGENTS.local.md").write_text(
        "LOCAL LEVEL", encoding="utf-8"
    )

    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None

    # 顺序验证
    pos_user = text.index("USER LEVEL")
    pos_project = text.index("PROJECT LEVEL")
    pos_local = text.index("LOCAL LEVEL")
    assert pos_user < pos_project < pos_local

    # H3 标题验证
    assert "### 用户全局规则" in text
    assert "### 项目规则" in text
    assert "### 本地规则" in text

    # framing
    assert "应当严格遵守" in text


def test_only_project_layer(isolated_cwd: Path) -> None:
    """只有项目级 → 输出仅项目段，无空标题（spec AC5）。"""
    (isolated_cwd / "AGENTS.md").write_text("PROJECT", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None
    assert "### 项目规则" in text
    assert "### 用户全局规则" not in text
    assert "### 本地规则" not in text


def test_loaded_layers_metadata(isolated_cwd: Path) -> None:
    """loaded_layers 返回正确元信息。"""
    (isolated_cwd / "AGENTS.md").write_text("hello", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    loader.load_all()
    layers = loader.loaded_layers()
    assert len(layers) == 1
    assert layers[0].name == "项目级"
    assert layers[0].display_path == "./AGENTS.md"
    assert layers[0].bytes_len == 5  # "hello"


# ---------- 8KB 限制（AC6） ----------


def test_8kb_truncation(isolated_cwd: Path, capsys) -> None:
    """spec AC6：写 9KB 文件 → 截断 + warning。"""
    big = "x" * (9 * 1024)
    (isolated_cwd / "AGENTS.md").write_text(big, encoding="utf-8")

    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()
    assert text is not None

    out = capsys.readouterr().out
    assert "超过 8KB" in out
    assert "已截断" in out
    assert "[... 内容已截断" in text

    # 字节数验证：截断后内容字节 ≤ 8KB
    layers = loader.loaded_layers()
    assert layers[0].bytes_len <= 8 * 1024


# ---------- 错误容错（AC7 / AC8） ----------


def test_non_utf8_skipped(isolated_cwd: Path, capsys) -> None:
    """spec AC8：非 UTF-8 文件 → warning + 视为空。"""
    # 写入一个 GBK 编码的中文字节，作为非 UTF-8 内容
    gbk_bytes = "中文内容".encode("gbk")
    (isolated_cwd / "AGENTS.md").write_bytes(gbk_bytes)

    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()

    out = capsys.readouterr().out
    assert "非 UTF-8" in out
    # 该层视为空 → 三层全空 → None
    assert text is None


def test_oserror_skipped(isolated_cwd: Path, capsys, monkeypatch) -> None:
    """spec AC7：mock 文件读取抛 PermissionError → warning + 视为空。"""
    target = isolated_cwd / "AGENTS.md"
    target.write_text("hi", encoding="utf-8")

    real_read_bytes = Path.read_bytes

    def fake_read_bytes(self):
        if self == target:
            raise PermissionError("denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)

    loader = InstructionsLoader(isolated_cwd)
    text = loader.load_all()

    out = capsys.readouterr().out
    assert "读不了" in out
    assert text is None


# ---------- reload_and_check（AC11 / AC12） ----------


def test_reload_no_change(isolated_cwd: Path) -> None:
    """spec AC11：内容不变 → reload_and_check 返回 (False, ...)。"""
    (isolated_cwd / "AGENTS.md").write_text("stable", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    loader.load_all()  # 第一次

    changed, _ = loader.reload_and_check()
    assert changed is False


def test_reload_with_change(isolated_cwd: Path) -> None:
    """spec AC12：改文件后 reload → 返回 (True, new_text)。"""
    target = isolated_cwd / "AGENTS.md"
    target.write_text("v1", encoding="utf-8")
    loader = InstructionsLoader(isolated_cwd)
    loader.load_all()

    target.write_text("v2 changed", encoding="utf-8")
    changed, new_text = loader.reload_and_check()
    assert changed is True
    assert new_text is not None
    assert "v2 changed" in new_text


def test_reload_first_time(isolated_cwd: Path) -> None:
    """初始为 None → 出现文件 → reload 应当返回 changed=True。"""
    loader = InstructionsLoader(isolated_cwd)
    loader.load_all()  # None
    assert loader.current_text() is None

    (isolated_cwd / "AGENTS.md").write_text("new file", encoding="utf-8")
    changed, new_text = loader.reload_and_check()
    assert changed is True
    assert "new file" in (new_text or "")


# ---------- _read_layer 直接测 ----------


def test_read_layer_目录不存在(tmp_path: Path) -> None:
    """目录不存在时 _read_layer 不报错。"""
    info = _read_layer(
        tmp_path / "nonexistent",
        ["AGENTS.md"],
        "项目级",
        "./",
    )
    assert info is None


def test_read_layer_是目录非文件(isolated_cwd: Path) -> None:
    """候选名是目录而非文件 → 跳过该名继续查下一个。"""
    (isolated_cwd / "AGENTS.md").mkdir()
    (isolated_cwd / "CLAUDE.md").write_text("ok", encoding="utf-8")

    info = _read_layer(
        isolated_cwd,
        ["AGENTS.md", "CLAUDE.md"],
        "项目级",
        "./",
    )
    assert info is not None
    assert info.display_path == "./CLAUDE.md"
