"""MCPToolAdapter 单测（spec AC17-AC19）。"""

import asyncio

import pytest

from mewcode.mcp.adapter import MCPToolAdapter
from mewcode.mcp.client import CallResult
from mewcode.mcp.protocol import MCPProtocolError
from mewcode.tools.base import DangerLevel
from mewcode.tools.sandbox import Sandbox


class StubClient:
    name = "fs"

    def __init__(self, result=None, exc=None):
        self.result = result or CallResult(text="ok", is_error=False)
        self.exc = exc
        self.calls = []

    async def call_tool(self, name, arguments, timeout=None):
        self.calls.append((name, arguments, timeout))
        if self.exc:
            raise self.exc
        return self.result


def _adapter(client=None):
    return MCPToolAdapter(
        client=client or StubClient(),
        original_name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        timeout=12,
    )


def test_name_前缀() -> None:
    assert _adapter().name == "mcp__fs__read_file"


def test_parameters_schema_透传() -> None:
    schema = _adapter().parameters_schema
    assert schema["properties"]["path"]["type"] == "string"


def test_description_透传() -> None:
    assert _adapter().description == "Read file"


def test_默认SAFE_readonly_False() -> None:
    a = _adapter()
    assert a.danger_level == DangerLevel.SAFE
    assert a.readonly is False


@pytest.mark.asyncio
async def test_execute_text(tmp_path) -> None:
    client = StubClient(CallResult(text="hello", is_error=False))
    a = _adapter(client)
    r = await a.execute({"path": "x"}, Sandbox(tmp_path))
    assert r.success is True
    assert r.text == "hello"
    assert client.calls == [("read_file", {"path": "x"}, 12)]


@pytest.mark.asyncio
async def test_execute_isError(tmp_path) -> None:
    client = StubClient(CallResult(text="bad", is_error=True))
    a = _adapter(client)
    r = await a.execute({}, Sandbox(tmp_path))
    assert r.success is False
    assert r.text == "bad"
    assert r.error_category == "MCP 工具返回错误"


@pytest.mark.asyncio
async def test_execute_超时(tmp_path) -> None:
    client = StubClient(exc=asyncio.TimeoutError())
    a = _adapter(client)
    r = await a.execute({}, Sandbox(tmp_path))
    assert r.success is False
    assert r.error_category == "MCP 超时"


@pytest.mark.asyncio
async def test_execute_protocol_error(tmp_path) -> None:
    client = StubClient(exc=MCPProtocolError(-1, "bad"))
    a = _adapter(client)
    r = await a.execute({}, Sandbox(tmp_path))
    assert r.success is False
    assert r.error_category == "MCP 协议错误"


@pytest.mark.asyncio
async def test_execute_其他异常(tmp_path) -> None:
    client = StubClient(exc=RuntimeError("boom"))
    a = _adapter(client)
    r = await a.execute({}, Sandbox(tmp_path))
    assert r.success is False
    assert r.error_category == "MCP 错误"
