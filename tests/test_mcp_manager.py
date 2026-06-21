"""MCP manager 生命周期单测（spec AC20-AC23）。"""

from unittest.mock import AsyncMock

import pytest

from mewcode.mcp.client import ToolInfo
from mewcode.mcp.config import ServerConfig
from mewcode.mcp import manager
from mewcode.tools import ToolRegistry


class StubClient:
    def __init__(self, name="s"):
        self.name = name
        self.timeout = 60
        self.shutdown = AsyncMock()

    async def call_tool(self, name, arguments, timeout=None):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_start_all_空配置() -> None:
    assert await manager.start_all({}) == {}


@pytest.mark.asyncio
async def test_start_all_多Server全成功(monkeypatch) -> None:
    async def fake_start_one(cfg):
        return StubClient(cfg.name), [ToolInfo("tool", "desc", {"type": "object"})]

    monkeypatch.setattr(manager, "_start_one", fake_start_one)
    cfgs = {
        "a": ServerConfig(name="a", type="stdio", command="python"),
        "b": ServerConfig(name="b", type="http", url="https://x"),
    }
    out = await manager.start_all(cfgs)
    assert set(out) == {"a", "b"}
    assert len(out["a"][1]) == 1


@pytest.mark.asyncio
async def test_start_all_单失败跳过(monkeypatch, capsys) -> None:
    async def fake_start_one(cfg):
        if cfg.name == "bad":
            raise RuntimeError("boom")
        return StubClient(cfg.name), []

    monkeypatch.setattr(manager, "_start_one", fake_start_one)
    cfgs = {
        "ok": ServerConfig(name="ok", type="stdio", command="python"),
        "bad": ServerConfig(name="bad", type="stdio", command="missing"),
    }
    out = await manager.start_all(cfgs)
    assert set(out) == {"ok"}
    assert "bad" in capsys.readouterr().out


def test_register_to() -> None:
    registry = ToolRegistry()
    client = StubClient("fs")
    started = {
        "fs": (
            client,
            [
                ToolInfo("read_file", "Read", {"type": "object"}),
                ToolInfo("write_file", "Write", {"type": "object"}),
            ],
        )
    }
    count = manager.register_to(registry, started)
    assert count == 2
    assert "mcp__fs__read_file" in registry
    assert "mcp__fs__write_file" in registry


@pytest.mark.asyncio
async def test_shutdown_all() -> None:
    c1 = StubClient("a")
    c2 = StubClient("b")
    await manager.shutdown_all({"a": (c1, []), "b": (c2, [])})
    c1.shutdown.assert_awaited_once()
    c2.shutdown.assert_awaited_once()
