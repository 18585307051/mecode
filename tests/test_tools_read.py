"""ReadTool 单元测试。

覆盖 task.md T10 的 6 个验证场景。
"""

import pytest
from pathlib import Path

from mewcode.tools.read import ReadTool
from mewcode.tools.sandbox import Sandbox


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> ReadTool:
    return ReadTool()


@pytest.mark.asyncio
async def test_读取整个文件(tool: ReadTool, sbx: Sandbox, tmp_path: Path) -> None:
    """无 offset/limit 时应返回整个文件内容。"""
    p = tmp_path / "a.txt"
    p.write_text("第一行\n第二行\n第三行\n第四行\n第五行\n", encoding="utf-8")
    res = await tool.execute({"path": "a.txt"}, sbx)
    assert res.success
    assert "第一行" in res.text
    assert "第五行" in res.text
    assert "共 5 行" in res.text


@pytest.mark.asyncio
async def test_offset_limit_按行(tool: ReadTool, sbx: Sandbox, tmp_path: Path) -> None:
    """offset=10, limit=5 应只返回第 10~14 行。"""
    lines = [f"line{i}\n" for i in range(1, 101)]  # line1..line100
    p = tmp_path / "big.txt"
    p.write_text("".join(lines), encoding="utf-8")

    res = await tool.execute({"path": "big.txt", "offset": 10, "limit": 5}, sbx)
    assert res.success
    # 第 10~14 行应在结果中
    for i in range(10, 15):
        assert f"line{i}\n" in res.text
    # 第 9 行与第 15 行不应出现（独立校验，避免 line10 包含 line1）
    body = res.text.split("\n", 2)[-1]  # 去掉前面的元信息
    assert "line9\n" not in body
    assert "line15\n" not in body


@pytest.mark.asyncio
async def test_文件不存在(tool: ReadTool, sbx: Sandbox) -> None:
    res = await tool.execute({"path": "none.txt"}, sbx)
    assert not res.success
    assert res.error_category == "文件不存在"


@pytest.mark.asyncio
async def test_路径越界(tool: ReadTool, sbx: Sandbox) -> None:
    res = await tool.execute({"path": "../outside.txt"}, sbx)
    assert not res.success
    assert res.error_category == "路径越界"


@pytest.mark.asyncio
async def test_大文件截断(tool: ReadTool, sbx: Sandbox, tmp_path: Path) -> None:
    """超过 256KB 应按字节截断 + "已截断" 提示。"""
    # 写一个 500KB 文件
    big = "x" * (500 * 1024)
    p = tmp_path / "big.txt"
    p.write_text(big, encoding="utf-8")

    res = await tool.execute({"path": "big.txt"}, sbx)
    assert res.success
    assert "已截断" in res.text
    # 整体字节数（含 header）≤ 256KB + 一些 header 余量（< 200 字节）
    assert len(res.text.encode("utf-8")) <= 256 * 1024 + 200


@pytest.mark.asyncio
async def test_非utf8_解码失败(tool: ReadTool, sbx: Sandbox, tmp_path: Path) -> None:
    """二进制 / 非 UTF-8 编码文件应返回解码失败。"""
    p = tmp_path / "bin.dat"
    # 写入 latin-1 / 二进制字节，确保 UTF-8 解码失败
    p.write_bytes(b"\xff\xfe\xfd\xfc abc")
    res = await tool.execute({"path": "bin.dat"}, sbx)
    assert not res.success
    assert res.error_category == "解码失败"


@pytest.mark.asyncio
async def test_offset超出文件不报错(tool: ReadTool, sbx: Sandbox, tmp_path: Path) -> None:
    """offset 超过文件总行数时返回空（不报错）。"""
    p = tmp_path / "a.txt"
    p.write_text("only one line\n", encoding="utf-8")
    res = await tool.execute({"path": "a.txt", "offset": 999}, sbx)
    assert res.success  # 不报错
    # body 部分（去掉 header）应为空
    body = "\n".join(
        ln for ln in res.text.splitlines() if not ln.startswith("#")
    )
    assert body.strip() == ""
