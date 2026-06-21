"""MCP 三步会话流程（spec F5 / D11）。

每个 Server 启动后：
1. initialize 握手
2. notifications/initialized 通知
3. tools/list 列工具

运行时按需 tools/call。
"""

from dataclasses import dataclass

from mewcode.mcp.protocol import (
    encode_notification,
    encode_request,
)
from mewcode.mcp.transport import Transport

_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_NAME = "mewcode"
_CLIENT_VERSION = "0.1.0"


@dataclass
class ToolInfo:
    """MCP Server 返回的工具元信息。"""

    name: str
    description: str
    input_schema: dict


@dataclass
class CallResult:
    """tools/call 的解析结果。"""

    text: str
    is_error: bool


class MCPClient:
    """单个 MCP Server 的客户端。"""

    def __init__(
        self, name: str, transport: Transport, timeout: float
    ) -> None:
        self.name = name
        self._transport = transport
        self._timeout = timeout
        self._id_counter = 0
        self._initialized = False

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def initialize(self) -> None:
        """三步：start → initialize → notifications/initialized。"""
        await self._transport.start()

        # Step 1: initialize 请求
        req = encode_request(
            self._next_id(),
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "clientInfo": {
                    "name": _CLIENT_NAME,
                    "version": _CLIENT_VERSION,
                },
                "capabilities": {},
            },
        )
        await self._transport.call(req, self._timeout)

        # Step 2: notifications/initialized（不等响应）
        await self._transport.notify(
            encode_notification("notifications/initialized")
        )

        self._initialized = True

    async def list_tools(self) -> list[ToolInfo]:
        """调 tools/list，返回工具列表。"""
        req = encode_request(self._next_id(), "tools/list")
        result = await self._transport.call(req, self._timeout)
        tools: list[ToolInfo] = []
        for raw in result.get("tools", []):
            tools.append(
                ToolInfo(
                    name=raw.get("name", ""),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {"type": "object"}),
                )
            )
        return tools

    async def call_tool(
        self, name: str, arguments: dict, timeout: float | None = None
    ) -> CallResult:
        """调 tools/call。"""
        req = encode_request(
            self._next_id(),
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = await self._transport.call(
            req, timeout or self._timeout
        )
        return self._parse_call_result(result)

    @staticmethod
    def _parse_call_result(result: dict) -> CallResult:
        """spec F10 / D14：把 content 数组拼成纯文本。"""
        is_error = bool(result.get("isError", False))
        parts: list[str] = []
        for item in result.get("content", []):
            t = item.get("type")
            if t == "text":
                parts.append(item.get("text", ""))
            elif t == "image":
                mime = item.get("mimeType", "image")
                data = item.get("data", "")
                parts.append(
                    f"[image:{mime}, {len(data)} bytes (base64)]"
                )
            elif t == "audio":
                parts.append(f"[audio:{item.get('mimeType', 'audio')}]")
            elif t == "resource":
                resource = item.get("resource", {})
                uri = resource.get("uri", "?")
                parts.append(f"[resource:{uri}]")
            else:
                parts.append(f"[unknown:{t}]")
        return CallResult(text="\n".join(parts), is_error=is_error)

    async def shutdown(self) -> None:
        await self._transport.shutdown()
        self._initialized = False

    @property
    def timeout(self) -> float:
        return self._timeout
