"""MCP 端到端验证脚本。

不依赖 npx，动态生成一个最小 Python stdio MCP Server：
- initialize 返回 serverInfo
- tools/list 返回 echo 工具
- tools/call echo 返回文本

验证路径：config → manager.start_all → register_to → ToolRegistry →
MCPToolAdapter.execute → tools/call。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mewcode.mcp.config import ServerConfig
from mewcode.mcp.manager import register_to, shutdown_all, start_all
from mewcode.tools import Sandbox, ToolRegistry


SERVER_CODE = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "protocolVersion": msg.get("params", {}).get("protocolVersion"),
                "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
                "capabilities": {"tools": {}}
            }
        }), flush=True)
    elif method == "notifications/initialized":
        # 通知无响应
        continue
    elif method == "tools/list":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text back",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]
                        }
                    }
                ]
            }
        }), flush=True)
    elif method == "tools/call":
        params = msg.get("params", {})
        args = params.get("arguments", {})
        text = args.get("text", "")
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {
                "content": [{"type": "text", "text": "echo:" + text}],
                "isError": False
            }
        }), flush=True)
'''


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        server_py = tmp / "fake_mcp_server.py"
        server_py.write_text(SERVER_CODE, encoding="utf-8")

        cfg = ServerConfig(
            name="fake",
            type="stdio",
            command=sys.executable,
            args=[str(server_py)],
            timeout=5,
        )

        print("[1] start_all...")
        started = await start_all({"fake": cfg})
        assert "fake" in started, "fake server 未启动"
        client, tools = started["fake"]
        print(f"    tools: {[t.name for t in tools]}")
        assert len(tools) == 1 and tools[0].name == "echo"

        print("[2] register_to ToolRegistry...")
        registry = ToolRegistry()
        count = register_to(registry, started)
        assert count == 1
        tool = registry.get("mcp__fake__echo")
        assert tool is not None
        print(f"    registered: {tool.name}")

        print("[3] execute MCP tool...")
        result = await tool.execute({"text": "hello"}, Sandbox(cwd=Path.cwd()))
        assert result.success, result.text
        assert result.text == "echo:hello"
        print(f"    result: {result.text}")

        print("[4] shutdown_all...")
        await shutdown_all(started)

    print("\n✓ MCP 端到端通过")


if __name__ == "__main__":
    asyncio.run(main())
