"""WriteTool 单元测试。

覆盖 task.md T11 的 4 个验证场景。
"""

import pytest
from pathlib import Path

from mewcode.tools.sandbox import Sandbox
from mewcode.tools.write import WriteTool


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> WriteTool:
    return WriteTool()


@pytest.mark.asyncio
async def test_新建文件(tool: WriteTool, sbx: Sandbox, tmp_path: Path) -> None:
    res = await tool.execute({"path": "a.txt", "content": "hello"}, sbx)
    assert res.success
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_覆盖文件(tool: WriteTool, sbx: Sandbox, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("OLD", encoding="utf-8")
    res = await tool.execute({"path": "a.txt", "content": "NEW"}, sbx)
    assert res.success
    assert p.read_text(encoding="utf-8") == "NEW"


@pytest.mark.asyncio
async def test_自动创建父目录(tool: WriteTool, sbx: Sandbox, tmp_path: Path) -> None:
    target = tmp_path / "sub" / "deep" / "a.txt"
    res = await tool.execute(
        {"path": "sub/deep/a.txt", "content": "x"}, sbx
    )
    assert res.success
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_路径越界(tool: WriteTool, sbx: Sandbox, tmp_path: Path) -> None:
    res = await tool.execute(
        {"path": "../outside.txt", "content": "x"}, sbx
    )
    assert not res.success
    assert res.error_category == "路径越界"
    # 文件不应在沙盒内被创建
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.asyncio
async def test_缺参(tool: WriteTool, sbx: Sandbox) -> None:
    res = await tool.execute({"path": "a.txt"}, sbx)
    assert not res.success
    assert res.error_category == "参数错误"


def test_render_confirm_detail_含前20行(tool: WriteTool) -> None:
    """长内容应在 detail 中显示总行数与"仅显示前 20 行"。"""
    content = "\n".join(f"line{i}" for i in range(1, 51))  # 50 行
    detail = tool.render_confirm_detail({"path": "a.txt", "content": content})
    assert "a.txt" in detail
    assert "共 50 行" in detail
    assert "line1" in detail
    # 第 21 行起不应在预览中
    assert "line21" not in detail
