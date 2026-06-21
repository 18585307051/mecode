"""把 MCP 工具包装为 MewCode Tool（spec F8 / F9 / F10 / F13）。

MCPToolAdapter 继承 Tool，把远端 MCP 工具适配到 MewCode 的本地工具接口。
模型调用时完全无感——只知道名字叫 mcp__<server>__<tool>。
"""

import asyncio
from typing import TYPE_CHECKING

from mewcode.mcp.client import MCPClient
from mewcode.mcp.protocol import MCPProtocolError
from mewcode.tools.base import DangerLevel, Tool, ToolResult

if TYPE_CHECKING:
    from mewcode.tools.sandbox import Sandbox


class MCPToolAdapter(Tool):
    """适配单个 MCP 工具到 MewCode Tool 接口。

    spec F9 / D5：name 加前缀 mcp__<server>__<tool> 避免冲突。
    spec D13：danger_level 默认 SAFE，readonly=False（Plan Mode 不可用）。
    """

    danger_level = DangerLevel.SAFE
    readonly = False

    def __init__(
        self,
        client: MCPClient,
        original_name: str,
        description: str,
        input_schema: dict,
        timeout: float,
    ) -> None:
        self._client = client
        self._original_name = original_name
        self._description = description
        self._input_schema = input_schema
        self._timeout = timeout
        self._name = f"mcp__{client.name}__{original_name}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict:
        return self._input_schema

    async def execute(
        self, params: dict, sandbox: "Sandbox", render_event=None
    ) -> ToolResult:
        """调远端 tools/call，把结果转 ToolResult。"""
        try:
            result = await self._client.call_tool(
                self._original_name, params, timeout=self._timeout
            )
            return ToolResult(
                success=not result.is_error,
                text=result.text,
                error_category=None if not result.is_error else "MCP 工具返回错误",
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                text=f"MCP 工具调用超时（{self._timeout}s）：{self._original_name}",
                error_category="MCP 超时",
            )
        except MCPProtocolError as e:
            return ToolResult(
                success=False,
                text=f"MCP 协议错误：{e}",
                error_category="MCP 协议错误",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                text=f"MCP 调用失败：{type(e).__name__}: {e}",
                error_category="MCP 错误",
            )

    def render_call_summary(self, params: dict) -> str:
        """动词式 UI（与第四阶段工具风格一致）。"""
        return f"MCP {self._original_name}"
