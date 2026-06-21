"""JSON-RPC 协议层单测（spec AC5-AC8）。"""

import asyncio

import pytest

from mewcode.mcp.protocol import (
    MCPProtocolError,
    PendingRegistry,
    encode_notification,
    encode_request,
)


def test_encode_request() -> None:
    assert encode_request(1, "tools/list") == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
    assert encode_request(2, "tools/call", {"x": 1}) == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"x": 1},
    }


def test_encode_notification() -> None:
    assert encode_notification("notifications/initialized") == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }


def test_alloc_id_自增() -> None:
    p = PendingRegistry()
    assert p.alloc_id() == 1
    assert p.alloc_id() == 2
    assert p.alloc_id() == 3


@pytest.mark.asyncio
async def test_pending_resolve_OK_响应() -> None:
    p = PendingRegistry()
    fut = asyncio.get_event_loop().create_future()
    p.register(1, "foo", fut)
    assert p.resolve({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert await fut == {"ok": True}


@pytest.mark.asyncio
async def test_pending_resolve_error响应() -> None:
    p = PendingRegistry()
    fut = asyncio.get_event_loop().create_future()
    p.register(1, "foo", fut)
    assert p.resolve(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32602, "message": "bad params"},
        }
    )
    with pytest.raises(MCPProtocolError) as ei:
        await fut
    assert ei.value.code == -32602
    assert "bad params" in str(ei.value)


def test_resolve_找不到id返回False() -> None:
    p = PendingRegistry()
    assert not p.resolve({"jsonrpc": "2.0", "id": 999, "result": {}})


@pytest.mark.asyncio
async def test_fail_all() -> None:
    p = PendingRegistry()
    fut1 = asyncio.get_event_loop().create_future()
    fut2 = asyncio.get_event_loop().create_future()
    p.register(1, "a", fut1)
    p.register(2, "b", fut2)
    p.fail_all(ConnectionError("closed"))
    with pytest.raises(ConnectionError):
        await fut1
    with pytest.raises(ConnectionError):
        await fut2


@pytest.mark.asyncio
async def test_cancel() -> None:
    p = PendingRegistry()
    fut = asyncio.get_event_loop().create_future()
    p.register(1, "a", fut)
    p.cancel(1)
    assert not p.resolve({"id": 1, "result": {}})
    assert not fut.done()
