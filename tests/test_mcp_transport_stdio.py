"""stdio Transport 单测（spec AC9-AC11）。"""

import json
import sys
from pathlib import Path

import pytest

from mewcode.mcp.transport import StdioTransport


ECHO_SERVER = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" in msg:
        print(json.dumps({"jsonrpc":"2.0", "id": msg["id"], "result": {"echo": msg.get("method")}}), flush=True)
'''

SLEEP_SERVER = r'''
import sys, time
for line in sys.stdin:
    time.sleep(5)
'''


def _script(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "server.py"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_start_启动子进程(tmp_path: Path) -> None:
    p = _script(tmp_path, ECHO_SERVER)
    t = StdioTransport(sys.executable, [str(p)], {}, None)
    await t.start()
    assert t._proc is not None
    await t.shutdown()


@pytest.mark.asyncio
async def test_call_收发消息(tmp_path: Path) -> None:
    p = _script(tmp_path, ECHO_SERVER)
    t = StdioTransport(sys.executable, [str(p)], {}, None)
    await t.start()
    result = await t.call({"jsonrpc": "2.0", "id": 1, "method": "ping"}, 3)
    assert result == {"echo": "ping"}
    await t.shutdown()


@pytest.mark.asyncio
async def test_notify_不等待响应(tmp_path: Path) -> None:
    p = _script(tmp_path, ECHO_SERVER)
    t = StdioTransport(sys.executable, [str(p)], {}, None)
    await t.start()
    await t.notify({"jsonrpc": "2.0", "method": "notifications/test"})
    await t.shutdown()


@pytest.mark.asyncio
async def test_shutdown_关闭子进程(tmp_path: Path) -> None:
    p = _script(tmp_path, ECHO_SERVER)
    t = StdioTransport(sys.executable, [str(p)], {}, None)
    await t.start()
    await t.shutdown()
    assert t._proc is not None
    assert t._proc.returncode is not None


@pytest.mark.asyncio
async def test_start_命令不存在() -> None:
    t = StdioTransport("definitely-not-a-command-xyz", [], {}, None)
    with pytest.raises(FileNotFoundError):
        await t.start()


@pytest.mark.asyncio
async def test_call_timeout(tmp_path: Path) -> None:
    p = _script(tmp_path, SLEEP_SERVER)
    t = StdioTransport(sys.executable, [str(p)], {}, None)
    await t.start()
    with pytest.raises(TimeoutError):
        await t.call({"jsonrpc": "2.0", "id": 1, "method": "slow"}, 0.1)
    await t.shutdown()
