"""三层 YAML 加载与合并单测（spec AC7）。"""

from pathlib import Path

import pytest
import yaml

from mewcode.permissions.loader import load_all, load_layer


# ---------- load_layer ----------


def test_load_layer_文件不存在(tmp_path: Path) -> None:
    mode, allow, deny = load_layer(tmp_path / "missing.yaml")
    assert mode is None
    assert allow == []
    assert deny == []


def test_load_layer_合法(tmp_path: Path) -> None:
    f = tmp_path / "perm.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "mode": "default",
                "allow": ["Bash(git *)", "Read(**/*)"],
                "deny": ["Edit(.git/**)"],
            }
        ),
        encoding="utf-8",
    )
    mode, allow, deny = load_layer(f)
    assert mode == "default"
    assert len(allow) == 2
    assert len(deny) == 1
    assert allow[0].pattern == "git *"


def test_load_layer_yaml错误(tmp_path: Path, capsys) -> None:
    f = tmp_path / "broken.yaml"
    f.write_text("mode: [invalid", encoding="utf-8")  # 不平衡的 YAML
    mode, allow, deny = load_layer(f)
    assert mode is None
    assert allow == []
    assert deny == []
    captured = capsys.readouterr()
    assert "解析失败" in captured.out


def test_load_layer_非法mode(tmp_path: Path, capsys) -> None:
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump({"mode": "wild"}), encoding="utf-8")
    mode, _, _ = load_layer(f)
    assert mode is None  # 非法 mode 被忽略
    captured = capsys.readouterr()
    assert "非法" in captured.out


def test_load_layer_非法规则跳过(tmp_path: Path, capsys) -> None:
    f = tmp_path / "p.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "allow": ["Bash(git *)", "invalid_no_paren", "Read(*)"],
            }
        ),
        encoding="utf-8",
    )
    _, allow, _ = load_layer(f)
    assert len(allow) == 2  # 跳过非法的
    captured = capsys.readouterr()
    assert "跳过非法" in captured.out


def test_load_layer_顶层非dict(tmp_path: Path, capsys) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("- a\n- b", encoding="utf-8")
    mode, allow, deny = load_layer(f)
    assert mode is None
    assert allow == []
    assert deny == []


# ---------- load_all 三层合并 ----------


@pytest.fixture
def layered_cwd(tmp_path: Path, monkeypatch) -> Path:
    """构造三层 YAML 测试环境。

    用户级写到 tmp_path/home/.mewcode/permissions.yaml；
    项目级 tmp_path/.mewcode/permissions.yaml；
    本地级 tmp_path/.mewcode/permissions.local.yaml。

    通过 monkeypatch 把 Path.home() 重定向到 tmp_path/home。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    (home / ".mewcode").mkdir()
    (tmp_path / ".mewcode").mkdir()
    return tmp_path


def test_load_all_仅本地级(layered_cwd: Path) -> None:
    """只有本地级文件时，mode 与规则全来自本地。"""
    local = layered_cwd / ".mewcode" / "permissions.local.yaml"
    local.write_text(
        yaml.safe_dump({"mode": "yolo", "allow": ["Bash(*)"]}),
        encoding="utf-8",
    )
    cfg = load_all(layered_cwd)
    assert cfg.mode == "yolo"
    assert len(cfg.allow) == 1


def test_load_all_三层合并_mode优先级(layered_cwd: Path) -> None:
    """本地 mode 覆盖项目 mode 覆盖用户 mode。"""
    user = Path.home() / ".mewcode" / "permissions.yaml"
    project = layered_cwd / ".mewcode" / "permissions.yaml"
    local = layered_cwd / ".mewcode" / "permissions.local.yaml"

    user.write_text(yaml.safe_dump({"mode": "strict"}), encoding="utf-8")
    project.write_text(yaml.safe_dump({"mode": "default"}), encoding="utf-8")
    local.write_text(yaml.safe_dump({"mode": "yolo"}), encoding="utf-8")

    cfg = load_all(layered_cwd)
    assert cfg.mode == "yolo"  # 本地最优先


def test_load_all_三层合并_allow顺序(layered_cwd: Path) -> None:
    """allow 列表按 本地→项目→用户 顺序拼接。"""
    user = Path.home() / ".mewcode" / "permissions.yaml"
    project = layered_cwd / ".mewcode" / "permissions.yaml"
    local = layered_cwd / ".mewcode" / "permissions.local.yaml"

    user.write_text(
        yaml.safe_dump({"allow": ["Bash(user-cmd)"]}), encoding="utf-8"
    )
    project.write_text(
        yaml.safe_dump({"allow": ["Bash(project-cmd)"]}), encoding="utf-8"
    )
    local.write_text(
        yaml.safe_dump({"allow": ["Bash(local-cmd)"]}), encoding="utf-8"
    )

    cfg = load_all(layered_cwd)
    assert len(cfg.allow) == 3
    # 本地在前，先匹配先生效
    assert cfg.allow[0].pattern == "local-cmd"
    assert cfg.allow[1].pattern == "project-cmd"
    assert cfg.allow[2].pattern == "user-cmd"


def test_load_all_全部缺失(layered_cwd: Path) -> None:
    """三层都缺失 → 默认配置。"""
    cfg = load_all(layered_cwd)
    assert cfg.mode == "default"
    assert cfg.allow == []
    assert cfg.deny == []


def test_load_all_仅用户级mode(layered_cwd: Path) -> None:
    """仅用户级有 mode → 取用户级。"""
    user = Path.home() / ".mewcode" / "permissions.yaml"
    user.write_text(yaml.safe_dump({"mode": "strict"}), encoding="utf-8")
    cfg = load_all(layered_cwd)
    assert cfg.mode == "strict"
