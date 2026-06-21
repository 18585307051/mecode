"""生命周期管理：并发启动 + 注册 + 退出清理（spec F11 / F12 / F14）。

start_all：asyncio.gather 并发启动所有 Server，单 Server 失败不影响其他。
register_to：把 MCP 工具包装为 MCPToolAdapter 注册到 ToolRegistry。
shutdown_all：退出时关闭所有 Server 连接。
"""

import asyncio

from mewcode.mcp.adapter import MCPToolAdapter
from mewcode.mcp.client import MCPClient, ToolInfo
from mewcode.mcp.config import ServerConfig
from mewcode.mcp.transport import HttpTransport, StdioTransport, Transport
from mewcode.tools import ToolRegistry


def _build_transport(cfg: ServerConfig) -> Transport:
    """按 cfg.type 构造对应 Transport。"""
    if cfg.type == "stdio":
        return StdioTransport(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            cwd=cfg.cwd,
        )
    return HttpTransport(url=cfg.url, headers=cfg.headers)


async def _start_one(cfg: ServerConfig) -> tuple[MCPClient, list[ToolInfo]]:
    """启动一个 Server：连接 + initialize + tools/list。"""
    transport = _build_transport(cfg)
    client = MCPClient(name=cfg.name, transport=transport, timeout=cfg.timeout)
    await client.initialize()
    tools = await client.list_tools()
    return client, tools


async def start_all(
    configs: dict[str, ServerConfig]
) -> dict[str, tuple[MCPClient, list[ToolInfo]]]:
    """并发启动所有 Server（spec F11 / D12）。

    单 Server 失败 → warning + 跳过（不抛异常）。

    Returns:
        {server_name: (client, [ToolInfo, ...])}
    """
    if not configs:
        return {}

    items = list(configs.items())
    tasks = [asyncio.create_task(_start_one(cfg)) for _, cfg in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, tuple[MCPClient, list[ToolInfo]]] = {}
    for (name, _cfg), result in zip(items, results):
        if isinstance(result, BaseException):
            print(f"⚠️ MCP Server {name!r} 启动失败：{result}（已跳过）")
            continue
        client, tools = result
        out[name] = (client, tools)
        # 不在底层 manager 里打印 emoji，避免非 UTF-8 stdout（如临时脚本/
        # Windows GBK 控制台）触发 UnicodeEncodeError。用户可见横幅由
        # main.py 的 Renderer 统一输出。
    return out


def register_to(
    registry: ToolRegistry,
    started: dict[str, tuple[MCPClient, list[ToolInfo]]],
) -> int:
    """把 MCP 工具注册到 ToolRegistry（spec F9）。

    Returns:
        注册的工具总数。
    """
    count = 0
    for _name, (client, tools) in started.items():
        for tool_info in tools:
            adapter = MCPToolAdapter(
                client=client,
                original_name=tool_info.name,
                description=tool_info.description,
                input_schema=tool_info.input_schema,
                timeout=client.timeout,
            )
            registry.register(adapter)
            count += 1
    return count


async def shutdown_all(
    started: dict[str, tuple[MCPClient, list[ToolInfo]]]
) -> None:
    """退出时关闭所有 Server（spec F14）。"""
    if not started:
        return
    tasks = [
        asyncio.create_task(client.shutdown())
        for client, _ in started.values()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
