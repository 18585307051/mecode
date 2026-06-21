"""MCP 客户端子模块出口（spec 第六阶段）。

公共 API：
- ServerConfig / load_all：配置加载
- MCPClient / ToolInfo / CallResult：单个 Server 客户端
- MCPToolAdapter：工具适配层
- start_all / register_to / shutdown_all：生命周期管理
- MCPProtocolError / MCPTimeoutError：异常
"""

from mewcode.mcp.adapter import MCPToolAdapter
from mewcode.mcp.client import CallResult, MCPClient, ToolInfo
from mewcode.mcp.config import ServerConfig, load_all
from mewcode.mcp.manager import register_to, shutdown_all, start_all
from mewcode.mcp.protocol import MCPProtocolError, MCPTimeoutError

__all__ = [
    "CallResult",
    "MCPProtocolError",
    "MCPTimeoutError",
    "MCPClient",
    "MCPToolAdapter",
    "ServerConfig",
    "ToolInfo",
    "load_all",
    "register_to",
    "shutdown_all",
    "start_all",
]
