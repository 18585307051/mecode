"""GlobTool 单元测试。

覆盖 task.md T14 的 5 个验证场景。
"""

import pytest
from pathlib import Path

from mewcode.tools.glob import GlobTool
from mewcode.tools.sandbox import Sandbox


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> GlobTool:
    return GlobTool()


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


@pytest.mark.asyncio
async def test_基础_py文件(tool: GlobTool, sbx: Sandbox, tmp_path: Path) -> None:
    """**/*.py 应返回所有 .py 文件，不含 .md。"""
    _touch(tmp_path / "a.py")
    _touch(tmp_path / "b.py")
    _touch(tmp_path / "c.md")

    res = await tool.execute({"pattern": "**/*.py"}, sbx)
    assert res.success
    body = res.text.split("\n", 1)[1]
    assert "a.py" in body
    assert "b.py" in body
    assert "c.md" not in body


@pytest.mark.asyncio
async def test_排序(tool: GlobTool, sbx: Sandbox, tmp_path: Path) -> None:
    """结果应按字母序。"""
    _touch(tmp_path / "z.py")
    _touch(tmp_path / "a.py")
    _touch(tmp_path / "m.py")

    res = await tool.execute({"pattern": "**/*.py"}, sbx)
    assert res.success
    body = res.text.split("\n", 1)[1]
    paths = [ln for ln in body.splitlines() if ln.strip()]
    assert paths == sorted(paths)
    # 第一个应是 a.py
    assert paths[0] == "a.py"


@pytest.mark.asyncio
async def test_噪声目录排除(tool: GlobTool, sbx: Sandbox, tmp_path: Path) -> None:
    """__pycache__ / .git / node_modules 下的 .py 不应出现。"""
    _touch(tmp_path / "src" / "ok.py")
    _touch(tmp_path / "__pycache__" / "x.py")
    _touch(tmp_path / ".git" / "y.py")
    _touch(tmp_path / "node_modules" / "z.py")
    _touch(tmp_path / "foo.egg-info" / "PKG-INFO.py")

    res = await tool.execute({"pattern": "**/*.py"}, sbx)
    assert res.success
    body = res.text
    assert "src/ok.py" in body
    assert "__pycache__" not in body
    assert ".git" not in body
    assert "node_modules" not in body
    assert "egg-info" not in body


@pytest.mark.asyncio
async def test_无匹配(tool: GlobTool, sbx: Sandbox, tmp_path: Path) -> None:
    res = await tool.execute({"pattern": "**/*.nonexistent"}, sbx)
    assert res.success
    assert "未匹配到任何文件" in res.text


@pytest.mark.asyncio
async def test_pattern越界_含dot_dot(tool: GlobTool, sbx: Sandbox) -> None:
    res = await tool.execute({"pattern": "../**/*.py"}, sbx)
    assert not res.success
    assert res.error_category == "路径越界"


@pytest.mark.asyncio
async def test_pattern越界_绝对路径(tool: GlobTool, sbx: Sandbox) -> None:
    res = await tool.execute({"pattern": "/etc/*.conf"}, sbx)
    assert not res.success
    assert res.error_category == "路径越界"
