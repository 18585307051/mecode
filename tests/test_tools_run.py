"""RunTool 单元测试。

覆盖 task.md T13 的 4 个验证场景。
超时测试用实例属性 monkey-patch 把 timeout 改为 2s 加速验证。
"""

import sys
import pytest
from pathlib import Path

from mewcode.tools.run import RunTool
from mewcode.tools.sandbox import Sandbox


@pytest.fixture
def sbx(tmp_path: Path) -> Sandbox:
    return Sandbox(cwd=tmp_path)


@pytest.fixture
def tool() -> RunTool:
    return RunTool()


@pytest.mark.asyncio
async def test_命令成功(tool: RunTool, sbx: Sandbox) -> None:
    """python --version 成功；exit_code == 0；text 含 'Python'。"""
    res = await tool.execute(
        {"command": f'"{sys.executable}" --version'}, sbx
    )
    assert res.success
    assert "退出码：0" in res.text
    assert "Python" in res.text


@pytest.mark.asyncio
async def test_命令非零退出(tool: RunTool, sbx: Sandbox) -> None:
    """sys.exit(7) 后 success=False，text 含 '退出码：7'。"""
    res = await tool.execute(
        {"command": f'"{sys.executable}" -c "import sys; sys.exit(7)"'}, sbx
    )
    assert not res.success
    assert res.error_category == "非零退出"
    assert "退出码：7" in res.text


@pytest.mark.asyncio
async def test_超时(tool: RunTool, sbx: Sandbox) -> None:
    """sleep(10) 配合 timeout=1s，应在 ~1s 内超时返回。"""
    tool.timeout = 1.0  # monkey-patch 实例属性，不影响其他测试
    res = await tool.execute(
        {
            "command": (
                f'"{sys.executable}" -c "import time; time.sleep(10)"'
            )
        },
        sbx,
    )
    assert not res.success
    assert res.error_category == "超时"


@pytest.mark.asyncio
async def test_工作目录是CWD(tool: RunTool, sbx: Sandbox, tmp_path: Path) -> None:
    """子进程的工作目录应是 sandbox.cwd。

    通过让子进程在 cwd 下创建一个标记文件 + 验证文件在 sandbox.cwd 下来
    检测，避免中文路径因 Windows GBK 编码导致 stdout 字符串比较失败。
    """
    marker = "marker_cwd_check.txt"
    res = await tool.execute(
        {
            "command": (
                f'"{sys.executable}" -c "open(\'{marker}\', \'w\').write(\'ok\')"'
            )
        },
        sbx,
    )
    assert res.success, res.text
    # 文件应当出现在 sandbox.cwd（即 tmp_path）下，而不是项目根
    assert (tmp_path / marker).exists()
    assert (tmp_path / marker).read_text() == "ok"


@pytest.mark.asyncio
async def test_缺参(tool: RunTool, sbx: Sandbox) -> None:
    res = await tool.execute({}, sbx)
    assert not res.success
    assert res.error_category == "参数错误"
