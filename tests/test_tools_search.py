"""SearchTool 单元测试。

覆盖 task.md T15 的 7 个验证场景。
"""

import pytest
from pathlib import Path

from mewcode.tools.sandbox import Sandbox
from mewcode.tools.search import SearchTool


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> SearchTool:
    return SearchTool()


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_基础匹配(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    """两个文件各一行 NEEDLE_XYZ → 应返回 2 条匹配。"""
    _write(tmp_path / "a.py", "x = 1\nNEEDLE_XYZ\n")
    _write(tmp_path / "b.py", "NEEDLE_XYZ\nfoo")
    res = await tool.execute({"pattern": "NEEDLE_XYZ"}, sbx)
    assert res.success
    assert "匹配 2 处" in res.text
    assert "a.py:2:" in res.text
    assert "b.py:1:" in res.text


@pytest.mark.asyncio
async def test_正则模式(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    """def \\w+ 应匹配多个函数定义。"""
    _write(
        tmp_path / "x.py",
        "def foo():\n    pass\ndef bar():\n    pass\nclass Baz: pass\n",
    )
    res = await tool.execute({"pattern": r"def \w+"}, sbx)
    assert res.success
    # 应匹配 def foo 与 def bar 两条
    assert res.text.count("x.py:") == 2


@pytest.mark.asyncio
async def test_单行截断_500(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    """超长行应被截断到 500 字符。"""
    long = "x" * 600 + "NEEDLE"
    _write(tmp_path / "long.py", long)
    res = await tool.execute({"pattern": "NEEDLE"}, sbx)
    assert res.success
    # 找出 long.py: 那一行的内容部分
    for line in res.text.splitlines():
        if line.startswith("long.py:"):
            # 形如 "long.py:1: <content>"
            content = line.split(":", 2)[-1].lstrip(" ")
            assert len(content) <= 500
            break
    else:
        pytest.fail("未找到 long.py 的匹配行")


@pytest.mark.asyncio
async def test_file_glob过滤(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "NEEDLE\n")
    _write(tmp_path / "b.md", "NEEDLE\n")
    res = await tool.execute(
        {"pattern": "NEEDLE", "file_glob": "**/*.py"}, sbx
    )
    assert res.success
    assert "a.py" in res.text
    assert "b.md" not in res.text


@pytest.mark.asyncio
async def test_噪声目录排除(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    _write(tmp_path / "src" / "ok.py", "NEEDLE\n")
    _write(tmp_path / "__pycache__" / "x.py", "NEEDLE\n")
    res = await tool.execute({"pattern": "NEEDLE"}, sbx)
    assert res.success
    assert "src/ok.py" in res.text
    assert "__pycache__" not in res.text


@pytest.mark.asyncio
async def test_is_literal_true(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    """is_literal=True 时 pattern 不解析为正则。"""
    _write(tmp_path / "a.py", "literal a.b match\nregex axb match\n")
    res = await tool.execute(
        {"pattern": "a.b", "is_literal": True}, sbx
    )
    assert res.success
    # 应只匹配字面量 "a.b" 那一行；不匹配 "axb"
    assert "literal a.b" in res.text
    assert "regex axb" not in res.text


@pytest.mark.asyncio
async def test_无效正则(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    res = await tool.execute({"pattern": "(unclosed"}, sbx)
    assert not res.success
    assert res.error_category == "正则错误"


@pytest.mark.asyncio
async def test_无匹配(tool: SearchTool, sbx: Sandbox, tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "no needle here\n")
    res = await tool.execute({"pattern": "ABSENT_TOKEN"}, sbx)
    assert res.success
    assert "未匹配" in res.text
