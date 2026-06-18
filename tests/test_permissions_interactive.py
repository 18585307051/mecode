"""人在回路单测（spec AC10）。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from mewcode.permissions.interactive import PermissionAsker
from mewcode.tools.confirmer import ConfirmCancelled


@pytest.fixture
def asker() -> PermissionAsker:
    return PermissionAsker()


# ---------- 4 种回答行为 ----------


@pytest.mark.asyncio
async def test_ask_y_返回once(asker: PermissionAsker, tmp_path: Path) -> None:
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value="y"))
    ):
        result = await asker.ask("run", "git status", tmp_path)
        assert result == "once"


@pytest.mark.asyncio
async def test_ask_yes_返回once(asker: PermissionAsker, tmp_path: Path) -> None:
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value="yes"))
    ):
        assert await asker.ask("run", "git status", tmp_path) == "once"


@pytest.mark.asyncio
async def test_ask_s_返回session(asker: PermissionAsker, tmp_path: Path) -> None:
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value="s"))
    ):
        assert await asker.ask("run", "git status", tmp_path) == "session"


@pytest.mark.asyncio
async def test_ask_a_写入local_yaml(asker: PermissionAsker, tmp_path: Path) -> None:
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value="a"))
    ):
        result = await asker.ask("run", "git status", tmp_path)
        assert result == "forever"

    # 验证文件已写入
    local_yaml = tmp_path / ".mewcode" / "permissions.local.yaml"
    assert local_yaml.exists()
    data = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
    assert "Bash(git status)" in data["allow"]


@pytest.mark.asyncio
async def test_ask_n_返回deny(asker: PermissionAsker, tmp_path: Path) -> None:
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value="n"))
    ):
        assert await asker.ask("run", "git status", tmp_path) == "deny"


@pytest.mark.asyncio
async def test_ask_回车默认deny(asker: PermissionAsker, tmp_path: Path) -> None:
    """回车（空字符串）默认拒绝。"""
    with patch.object(
        asker, "_session", return_value=AsyncMock(prompt_async=AsyncMock(return_value=""))
    ):
        assert await asker.ask("run", "git status", tmp_path) == "deny"


@pytest.mark.asyncio
async def test_ask_eof_返回deny(asker: PermissionAsker, tmp_path: Path) -> None:
    """Ctrl+D（EOFError）→ deny。"""
    mock_session = AsyncMock()
    mock_session.prompt_async = AsyncMock(side_effect=EOFError())
    with patch.object(asker, "_session", return_value=mock_session):
        assert await asker.ask("run", "git status", tmp_path) == "deny"


@pytest.mark.asyncio
async def test_ask_ctrl_c_抛ConfirmCancelled(
    asker: PermissionAsker, tmp_path: Path
) -> None:
    """Ctrl+C 抛 ConfirmCancelled，让 chat.engine 取消整个 turn。"""
    mock_session = AsyncMock()
    mock_session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt())
    with patch.object(asker, "_session", return_value=mock_session):
        with pytest.raises(ConfirmCancelled):
            await asker.ask("run", "git status", tmp_path)


# ---------- 写盘行为 ----------


@pytest.mark.asyncio
async def test_a_去重_重复添加(asker: PermissionAsker, tmp_path: Path) -> None:
    """连续两次选 a 添加同一规则 → 文件中只出现一次。"""
    mock_session = AsyncMock()
    mock_session.prompt_async = AsyncMock(return_value="a")
    with patch.object(asker, "_session", return_value=mock_session):
        await asker.ask("run", "git status", tmp_path)
        await asker.ask("run", "git status", tmp_path)

    local_yaml = tmp_path / ".mewcode" / "permissions.local.yaml"
    data = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
    # 只出现一次
    assert data["allow"].count("Bash(git status)") == 1


@pytest.mark.asyncio
async def test_a_文件已存在_追加(asker: PermissionAsker, tmp_path: Path) -> None:
    """文件已有规则 → a 选项追加新规则，保留旧规则。"""
    local_yaml = tmp_path / ".mewcode" / "permissions.local.yaml"
    local_yaml.parent.mkdir()
    local_yaml.write_text(
        yaml.safe_dump({"allow": ["Bash(npm *)"]}),
        encoding="utf-8",
    )

    mock_session = AsyncMock()
    mock_session.prompt_async = AsyncMock(return_value="a")
    with patch.object(asker, "_session", return_value=mock_session):
        await asker.ask("run", "git status", tmp_path)

    data = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
    assert "Bash(npm *)" in data["allow"]
    assert "Bash(git status)" in data["allow"]


# ---------- 动词式格式 ----------


def test_format_call_所有工具(asker: PermissionAsker) -> None:
    assert asker._format_call("run", "git status") == "Bash git status"
    assert asker._format_call("read", "a.py") == "Read a.py"
    assert asker._format_call("write", "x.txt") == "Wrote x.txt"
    assert asker._format_call("edit", "y.py") == "Edit y.py"
    assert asker._format_call("glob", "**/*.py") == "Glob **/*.py"
    assert asker._format_call("search", "TODO") == "Search TODO"
