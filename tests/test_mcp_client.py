"""MCPClient 单测（spec AC14-AC16）。"""

import pytest

from mewcode.mcp.client import MCPClient


class StubTransport:
    def __init__(self, results=None):
        self.started = False
        self.shutdown_called = False
        self.calls = []
        self.notifies = []
        self.results = list(results or [])

    async def start(self):
        self.started = True

    async def call(self, msg, timeout):
        self.calls.append((msg, timeout))
        if self.results:
            item = self.results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return {}

    async def notify(self, msg):
        self.notifies.append(msg)

    async def shutdown(self):
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_initialize_三步流程() -> None:
    t = StubTransport(results=[{"serverInfo": {"name": "s"}}])
    c = MCPClient("s", t, 60)
    await c.initialize()
    assert t.started
    assert t.calls[0][0]["method"] == "initialize"
    assert t.notifies[0]["method"] == "notifications/initialized"


@pytest.mark.asyncio
async def test_initialize_protocol_version() -> None:
    t = StubTransport(results=[{}])
    c = MCPClient("s", t, 60)
    await c.initialize()
    params = t.calls[0][0]["params"]
    assert params["protocolVersion"] == "2025-03-26"
    assert params["clientInfo"]["name"] == "mewcode"


@pytest.mark.asyncio
async def test_list_tools_解析() -> None:
    t = StubTransport(
        results=[
            {
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read file",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        ]
    )
    c = MCPClient("fs", t, 60)
    tools = await c.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "read_file"
    assert tools[0].description == "Read file"
    assert tools[0].input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_call_tool_text() -> None:
    t = StubTransport(
        results=[{"content": [{"type": "text", "text": "hello"}], "isError": False}]
    )
    c = MCPClient("fs", t, 60)
    r = await c.call_tool("echo", {"x": 1}, timeout=5)
    assert r.text == "hello"
    assert r.is_error is False
    assert t.calls[0][0]["method"] == "tools/call"
    assert t.calls[0][0]["params"] == {"name": "echo", "arguments": {"x": 1}}
    assert t.calls[0][1] == 5


@pytest.mark.asyncio
async def test_call_tool_image占位() -> None:
    t = StubTransport(
        results=[
            {
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "image", "mimeType": "image/png", "data": "abcd"},
                ],
                "isError": False,
            }
        ]
    )
    c = MCPClient("fs", t, 60)
    r = await c.call_tool("img", {})
    assert "before" in r.text
    assert "[image:image/png, 4 bytes" in r.text


@pytest.mark.asyncio
async def test_call_tool_isError() -> None:
    t = StubTransport(
        results=[{"content": [{"type": "text", "text": "bad"}], "isError": True}]
    )
    c = MCPClient("fs", t, 60)
    r = await c.call_tool("bad", {})
    assert r.is_error is True
    assert r.text == "bad"


@pytest.mark.asyncio
async def test_shutdown() -> None:
    t = StubTransport()
    c = MCPClient("s", t, 60)
    await c.shutdown()
    assert t.shutdown_called
