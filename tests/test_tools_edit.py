"""EditTool 单元测试。

覆盖 task.md T12 的 6 个验证场景。
"""

import pytest
from pathlib import Path

from mewcode.tools.edit import EditTool
from mewcode.tools.sandbox import Sandbox


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> EditTool:
    return EditTool()


@pytest.mark.asyncio
async def test_唯一匹配替换(tool: EditTool, sbx: Sandbox, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello ALPHA world", encoding="utf-8")
    res = await tool.execute(
        {"path": "a.txt", "old_text": "ALPHA", "new_text": "BETA"}, sbx
    )
    assert res.success
    assert p.read_text(encoding="utf-8") == "hello BETA world"


@pytest.mark.asyncio
async def test_未找到匹配(tool: EditTool, sbx: Sandbox, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello world", encoding="utf-8")
    res = await tool.execute(
        {"path": "a.txt", "old_text": "GAMMA", "new_text": "DELTA"}, sbx
    )
    assert not res.success
    assert res.error_category == "未找到匹配"
    # 文件未变
    assert p.read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_匹配多次报错(tool: EditTool, sbx: Sandbox, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("ALPHA ALPHA ALPHA", encoding="utf-8")
    res = await tool.execute(
        {"path": "a.txt", "old_text": "ALPHA", "new_text": "BETA"}, sbx
    )
    assert not res.success
    assert res.error_category == "匹配多次需更多上下文"
    assert "3" in res.text  # 错误信息含次数
    # 文件未变
    assert p.read_text(encoding="utf-8") == "ALPHA ALPHA ALPHA"


@pytest.mark.asyncio
async def test_路径越界(tool: EditTool, sbx: Sandbox) -> None:
    res = await tool.execute(
        {"path": "../outside.txt", "old_text": "x", "new_text": "y"}, sbx
    )
    assert not res.success
    assert res.error_category == "路径越界"


@pytest.mark.asyncio
async def test_文件不存在(tool: EditTool, sbx: Sandbox) -> None:
    res = await tool.execute(
        {"path": "none.txt", "old_text": "x", "new_text": "y"}, sbx
    )
    assert not res.success
    assert res.error_category == "文件不存在"


def test_render_confirm_detail_含diff(tool: EditTool) -> None:
    """确认提示中应含 unified_diff 风格的 - / + 行。"""
    detail = tool.render_confirm_detail(
        {"path": "a.txt", "old_text": "foo", "new_text": "bar"}
    )
    assert "a.txt" in detail
    # diff 中应同时含 - 和 + 标记
    assert "-foo" in detail
    assert "+bar" in detail


@pytest.mark.asyncio
async def test_缺参_old_text_为空(tool: EditTool, sbx: Sandbox, tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    res = await tool.execute(
        {"path": "a.txt", "old_text": "", "new_text": "y"}, sbx
    )
    assert not res.success
    assert res.error_category == "参数错误"
