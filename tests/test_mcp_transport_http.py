"""HTTP Transport 单测（spec AC12-AC13）。"""

import json

import httpx
import pytest

from mewcode.mcp.protocol import MCPProtocolError
from mewcode.mcp.transport import HttpTransport


@pytest.mark.asyncio
async def test_call_json响应(monkeypatch) -> None:
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
        )

    t = HttpTransport("https://example.com/mcp", {"X-Test": "1"})
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await t.call({"jsonrpc": "2.0", "id": 1, "method": "x"}, 10)
    assert result == {"ok": True}
    assert captured["headers"]["x-test"] == "1"
    await t.shutdown()


@pytest.mark.asyncio
async def test_call_sse响应() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": "sse"}}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"event: message\ndata: {json.dumps(payload)}\n\n",
        )

    t = HttpTransport("https://example.com/mcp", {})
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await t.call({"jsonrpc": "2.0", "id": 1, "method": "x"}, 10)
    assert result == {"ok": "sse"}
    await t.shutdown()


@pytest.mark.asyncio
async def test_call_http错误() -> None:
    t = HttpTransport("https://example.com/mcp", {})
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, text="no"))
    )
    with pytest.raises(MCPProtocolError):
        await t.call({"id": 1, "method": "x"}, 10)
    await t.shutdown()


@pytest.mark.asyncio
async def test_call_response含error() -> None:
    t = HttpTransport("https://example.com/mcp", {})
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"id": 1, "error": {"code": -1, "message": "bad"}},
            )
        )
    )
    with pytest.raises(MCPProtocolError) as ei:
        await t.call({"id": 1, "method": "x"}, 10)
    assert ei.value.code == -1
    await t.shutdown()


@pytest.mark.asyncio
async def test_call_未知content_type() -> None:
    t = HttpTransport("https://example.com/mcp", {})
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="hello",
            )
        )
    )
    with pytest.raises(MCPProtocolError):
        await t.call({"id": 1, "method": "x"}, 10)
    await t.shutdown()


def test_parse_first_sse_data_无data() -> None:
    with pytest.raises(MCPProtocolError):
        HttpTransport._parse_first_sse_data("event: ping\n\n")
