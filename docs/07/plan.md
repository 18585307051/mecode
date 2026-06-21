# MewCode 第六阶段 Plan

> 基于已批准的 `docs/07/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第五阶段的兼容矩阵。

## 1. 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│ main.py 启动流程                                                  │
│   1. 加载 mewcode.yaml + permissions YAML（前阶段已有）           │
│   2. 加载 mcp_servers.yaml（用户级 + 项目级合并） ← 第六阶段       │
│   3. await mcp.start_all(server_configs)                          │
│      → 并发对每个 Server: 连接 + initialize + tools/list           │
│   4. mcp.register_to(registry)                                    │
│      → 把每个 MCP 工具包装为 MCPToolAdapter，加前缀注册             │
│   5. 进入 REPL                                                    │
│   ...                                                              │
│   6. 退出时 await mcp.shutdown_all()                              │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│ mewcode/mcp/manager.py                        │
│   start_all(configs) → dict[name, MCPClient]  │
│   register_to(registry, clients)              │
│   shutdown_all(clients)                       │
└─────┬─────────────────────────────────────────┘
      │ 每个 Server 一个 MCPClient
      ▼
┌──────────────────────────────────────────────┐
│ mewcode/mcp/client.py                         │
│   MCPClient                                   │
│     - initialize() 三步流程                    │
│     - list_tools() → list[ToolInfo]           │
│     - call_tool(name, args) → CallResult      │
│     - shutdown()                              │
└─────┬─────────────────────────────────────────┘
      │ Transport 抽象
      ▼
┌──────────────────────────────────────────────┐
│ mewcode/mcp/transport.py                      │
│   Transport (Protocol)                        │
│     send(msg) / start() / shutdown()          │
│   StdioTransport: asyncio.subprocess          │
│     + reader_loop + stderr_loop               │
│   HttpTransport: httpx.AsyncClient            │
│     POST 单响应（JSON 或 SSE 首帧）             │
└─────┬─────────────────────────────────────────┘
      │ 协议层
      ▼
┌──────────────────────────────────────────────┐
│ mewcode/mcp/protocol.py                       │
│   encode_request / encode_notification        │
│   decode_message → Request/Response/Notif     │
│   PendingRegistry: id ↔ Future 异步配对       │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│ mewcode/mcp/adapter.py                        │
│   MCPToolAdapter(Tool)                        │
│     name = mcp__<server>__<tool>              │
│     execute() → 调 client.call_tool()         │
│     超时/错误转 ToolResult                     │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│ mewcode/mcp/config.py                         │
│   ServerConfig 数据类                          │
│   load_all(cwd) → dict[name, ServerConfig]    │
│   expand_vars + 跳过缺失变量的 Server          │
└──────────────────────────────────────────────┘
```

### 启动流程串联

```
main.py
  ├── ToolRegistry()
  ├── register_builtins(registry)         ← 第二阶段，6 个内置工具
  ├── PermissionPolicy(cwd)                ← 第五阶段
  ├── mcp_configs = mcp.load_all(cwd)      ← 第六阶段
  ├── mcp_clients = await mcp.start_all(mcp_configs)
  ├── mcp.register_to(registry, mcp_clients)
  ├── 启动 REPL (透传 registry / policy / mcp_clients)
  └── try: ... finally: await mcp.shutdown_all(mcp_clients)
```

### 模块依赖

```
mewcode/mcp/
  config.py   → stdlib + PyYAML
  protocol.py → stdlib (json, dataclasses, asyncio)
  transport.py → asyncio.subprocess + httpx + protocol.py
  client.py   → transport.py + protocol.py
  adapter.py  → mewcode.tools.Tool（继承）+ client.py
  manager.py  → client.py + adapter.py + 注册到外部 registry
```

`mewcode.mcp` 单向依赖 `mewcode.tools.Tool` 与 `mewcode.tools.ToolResult`。
chat / providers / commands 不感知 MCP 模块（注册后自然可用）。

## 2. 模块设计

### 2.1 mewcode/mcp/config.py

```python
"""MCP Server 配置加载（spec F1 / F2 / F3）。

两层文件：
  ~/.mewcode/mcp_servers.yaml      (用户级)
  <cwd>/.mewcode/mcp_servers.yaml  (项目级)

合并：项目级 server 完整覆盖用户级同名 server；不同名取并集。
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class ServerConfig:
    """单个 Server 的归一化配置。"""
    name: str
    type: Literal["stdio", "http"]
    # stdio
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # http
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # 通用
    timeout: float = 60.0


_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def expand_vars(value: str) -> tuple[str, list[str]]:
    """展开 ${VAR}。

    Returns:
        (expanded, missing_vars)
        - expanded: 展开后字符串（如有缺失变量，则缺失部分保留 ${VAR}）
        - missing_vars: 未定义的变量名列表（空 = 全部成功）
    """
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            missing.append(var)
            return m.group(0)  # 保留 ${VAR}
        return val

    return _VAR_RE.sub(_sub, value), missing


def _expand_dict(d: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """对 dict 的所有值做 ${VAR} 展开。"""
    out: dict[str, str] = {}
    all_missing: list[str] = []
    for k, v in d.items():
        expanded, missing = expand_vars(str(v))
        out[k] = expanded
        all_missing.extend(missing)
    return out, all_missing


def _parse_server(name: str, raw: dict) -> ServerConfig | None:
    """解析单个 server dict → ServerConfig。

    缺失必填字段或 ${VAR} 缺失 → 打印 warning 返回 None（跳过）。
    """
    server_type = raw.get("type")
    if server_type not in ("stdio", "http"):
        print(f"⚠️ MCP Server {name!r} 缺少 type 字段或类型非法（已跳过）")
        return None

    timeout = float(raw.get("timeout", 60.0))

    if server_type == "stdio":
        command = raw.get("command")
        if not command:
            print(f"⚠️ MCP Server {name!r}（stdio）缺少 command（已跳过）")
            return None
        args = list(raw.get("args") or [])
        env_raw = raw.get("env") or {}
        env, missing = _expand_dict(env_raw)
        if missing:
            print(f"⚠️ MCP Server {name!r} 配置含未定义环境变量 {missing}（已跳过）")
            return None
        return ServerConfig(
            name=name, type="stdio", command=command, args=args,
            env=env, cwd=raw.get("cwd"), timeout=timeout,
        )

    # http
    url = raw.get("url")
    if not url:
        print(f"⚠️ MCP Server {name!r}（http）缺少 url（已跳过）")
        return None
    headers_raw = raw.get("headers") or {}
    headers, missing = _expand_dict(headers_raw)
    if missing:
        print(f"⚠️ MCP Server {name!r} 配置含未定义环境变量 {missing}（已跳过）")
        return None
    return ServerConfig(
        name=name, type="http", url=url, headers=headers, timeout=timeout,
    )


def _load_layer(path: Path) -> dict[str, dict]:
    """加载单个 YAML 文件，返回 servers dict（不做归一化解析）。"""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"⚠️ MCP 配置文件 {path} 解析失败：{e}")
        return {}
    if not isinstance(data, dict):
        return {}
    servers = data.get("servers") or {}
    if not isinstance(servers, dict):
        return {}
    return servers


def load_all(cwd: Path) -> dict[str, ServerConfig]:
    """加载两层配置并合并（spec F2）。"""
    user_path = Path.home() / ".mewcode" / "mcp_servers.yaml"
    project_path = cwd / ".mewcode" / "mcp_servers.yaml"

    merged_raw: dict[str, dict] = {}
    merged_raw.update(_load_layer(user_path))
    merged_raw.update(_load_layer(project_path))  # 项目级覆盖

    out: dict[str, ServerConfig] = {}
    for name, raw in merged_raw.items():
        cfg = _parse_server(name, raw)
        if cfg is not None:
            out[name] = cfg
    return out
```

### 2.2 mewcode/mcp/protocol.py

```python
"""JSON-RPC 2.0 编解码 + 异步配对（spec F4）。

三种消息类型：
  Request:     {"jsonrpc":"2.0", "id":N, "method":"...", "params":{...}}
  Response OK: {"jsonrpc":"2.0", "id":N, "result":{...}}
  Response Err:{"jsonrpc":"2.0", "id":N, "error":{"code":N, "message":"..."}}
  Notification:{"jsonrpc":"2.0", "method":"...", "params":{...}}
"""

import asyncio
import itertools
from dataclasses import dataclass


class MCPProtocolError(Exception):
    """MCP 协议错误。"""
    def __init__(self, code: int, message: str, data=None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPTimeoutError(Exception):
    """MCP 调用超时。"""


def encode_request(req_id: int, method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def encode_notification(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


@dataclass
class _Pending:
    """一条 pending 请求。"""
    future: asyncio.Future
    method: str  # 用于错误 message


class PendingRegistry:
    """id ↔ Future 异步配对（spec F4）。"""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._pending: dict[int, _Pending] = {}

    def alloc_id(self) -> int:
        return next(self._counter)

    def register(self, req_id: int, method: str, future: asyncio.Future) -> None:
        self._pending[req_id] = _Pending(future=future, method=method)

    def resolve(self, msg: dict) -> bool:
        """根据响应消息找到 future 并 set_result / set_exception。

        Returns:
            True：成功路由；False：找不到对应 id（孤儿响应，丢弃）。
        """
        req_id = msg.get("id")
        if req_id not in self._pending:
            return False
        pending = self._pending.pop(req_id)
        if pending.future.done():
            return True
        if "error" in msg:
            err = msg["error"]
            pending.future.set_exception(
                MCPProtocolError(
                    err.get("code", -1),
                    err.get("message", "unknown"),
                    err.get("data"),
                )
            )
        else:
            pending.future.set_result(msg.get("result", {}))
        return True

    def fail_all(self, exc: Exception) -> None:
        """传输关闭时把所有 pending 设为异常（避免泄漏）。"""
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._pending.clear()
```

### 2.3 mewcode/mcp/transport.py

```python
"""传输层：stdio + http（spec F6 / F7）。

共同接口：
  start()                    建立连接
  send(msg) -> dict | None   发消息（http 模式直接同步拿响应）
  shutdown()                 关闭
"""

import asyncio
import json
import sys
from abc import ABC, abstractmethod

import httpx

from mewcode.mcp.protocol import MCPProtocolError, PendingRegistry


class Transport(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def call(self, msg: dict, timeout: float) -> dict:
        """发请求 + 等响应，返回 result dict。"""

    @abstractmethod
    async def notify(self, msg: dict) -> None:
        """发通知（无响应）。"""

    @abstractmethod
    async def shutdown(self) -> None: ...


class StdioTransport(Transport):
    """子进程 stdio 传输。"""

    def __init__(self, command: str, args: list[str], env: dict, cwd: str | None) -> None:
        import os
        self._command = command
        self._args = args
        # 合并 os.environ + 配置 env
        self._env = {**os.environ, **env}
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_buf: list[bytes] = []
        self._pending = PendingRegistry()
        self._closed = False

    async def start(self) -> None:
        kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._env,
            "cwd": self._cwd,
        }
        if sys.platform == "win32":
            import subprocess
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = await asyncio.create_subprocess_exec(
            self._command, *self._args, **kwargs
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _reader_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while not self._closed:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "id" in msg:
                    self._pending.resolve(msg)
                # else: notification，本阶段忽略
        except (asyncio.CancelledError, OSError):
            pass
        finally:
            self._pending.fail_all(
                ConnectionError("MCP stdio transport closed")
            )

    async def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            while not self._closed:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                self._stderr_buf.append(line)
        except (asyncio.CancelledError, OSError):
            pass

    async def call(self, msg: dict, timeout: float) -> dict:
        assert self._proc and self._proc.stdin
        req_id = msg["id"]
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending.register(req_id, msg["method"], future)
        try:
            self._proc.stdin.write(
                (json.dumps(msg) + "\n").encode("utf-8")
            )
            await self._proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending._pending.pop(req_id, None)  # 清理
            raise

    async def notify(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc:
            try:
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, BaseException):
                    pass


class HttpTransport(Transport):
    """Streamable HTTP 传输（POST 单响应模式）。"""

    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self._url = url
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        }
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def call(self, msg: dict, timeout: float) -> dict:
        assert self._client
        resp = await self._client.post(
            self._url,
            content=json.dumps(msg).encode("utf-8"),
            headers=self._headers,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise MCPProtocolError(
                resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            data = resp.json()
        elif "text/event-stream" in ct:
            data = self._parse_first_sse_data(resp.text)
        else:
            raise MCPProtocolError(-32000, f"未知 Content-Type: {ct}")

        if "error" in data:
            err = data["error"]
            raise MCPProtocolError(
                err.get("code", -1), err.get("message", ""), err.get("data")
            )
        return data.get("result", {})

    @staticmethod
    def _parse_first_sse_data(text: str) -> dict:
        """解析 SSE 流的第一个 data 帧。"""
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    return json.loads(payload)
        raise MCPProtocolError(-32000, "SSE 响应无 data 帧")

    async def notify(self, msg: dict) -> None:
        """HTTP 模式下，通知用 POST 不等响应。"""
        assert self._client
        try:
            await self._client.post(
                self._url,
                content=json.dumps(msg).encode("utf-8"),
                headers=self._headers,
                timeout=10.0,
            )
        except Exception:
            pass  # 通知失败不影响后续

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
```

### 2.4 mewcode/mcp/client.py

```python
"""MCP 三步会话流程（spec F5）。"""

from dataclasses import dataclass

from mewcode.mcp.protocol import (
    PendingRegistry, encode_request, encode_notification,
)
from mewcode.mcp.transport import Transport


_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_NAME = "mewcode"
_CLIENT_VERSION = "0.1.0"


@dataclass
class ToolInfo:
    name: str
    description: str
    input_schema: dict


@dataclass
class CallResult:
    text: str
    is_error: bool


class MCPClient:
    """单个 MCP Server 的客户端。"""

    def __init__(self, name: str, transport: Transport, timeout: float) -> None:
        self.name = name
        self._transport = transport
        self._timeout = timeout
        self._pending = PendingRegistry()
        self._initialized = False

    async def initialize(self) -> None:
        """三步：initialize → notifications/initialized → tools/list。"""
        await self._transport.start()

        # Step 1: initialize
        req = encode_request(
            self._pending.alloc_id(),
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
                "capabilities": {},
            },
        )
        # 注：Stdio/Http transport 内部各自管理 PendingRegistry；
        # 我们直接调 transport.call。
        await self._transport.call(req, self._timeout)

        # Step 2: notifications/initialized（不等响应）
        await self._transport.notify(
            encode_notification("notifications/initialized")
        )

        self._initialized = True

    async def list_tools(self) -> list[ToolInfo]:
        req = encode_request(self._pending.alloc_id(), "tools/list")
        result = await self._transport.call(req, self._timeout)
        tools = []
        for raw in result.get("tools", []):
            tools.append(ToolInfo(
                name=raw["name"],
                description=raw.get("description", ""),
                input_schema=raw.get("inputSchema", {"type": "object"}),
            ))
        return tools

    async def call_tool(
        self, name: str, arguments: dict, timeout: float | None = None
    ) -> CallResult:
        req = encode_request(
            self._pending.alloc_id(),
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = await self._transport.call(req, timeout or self._timeout)
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
                parts.append(f"[image:{mime}, {len(data)} bytes (base64)]")
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
```

注：transport 实际上各自维护 PendingRegistry（stdio 需要，http 不需要）。
client.py 不重复管理 pending；这里 self._pending 仅用于 alloc_id（id
归属客户端逻辑层）。

### 2.5 mewcode/mcp/adapter.py

```python
"""把 MCP 工具包装为 MewCode Tool（spec F8 / F9 / F10 / F13）。"""

from typing import Callable

from mewcode.mcp.client import MCPClient
from mewcode.mcp.protocol import MCPProtocolError, MCPTimeoutError
from mewcode.tools.base import DangerLevel, Tool, ToolResult


class MCPToolAdapter(Tool):
    """适配单个 MCP 工具到 MewCode Tool 接口。

    name 加前缀 mcp__<server>__<tool> 避免冲突（spec F9 / D5）。
    danger_level 默认 SAFE，readonly=False（spec D13）。
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

    async def execute(self, params: dict, sandbox, render_event: Callable) -> ToolResult:
        try:
            result = await self._client.call_tool(
                self._original_name, params, timeout=self._timeout
            )
            return ToolResult(
                success=not result.is_error,
                text=result.text,
            )
        except (asyncio.TimeoutError, MCPTimeoutError) as e:
            return ToolResult(
                success=False,
                text=f"MCP 工具调用超时（{self._timeout}s）：{e}",
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
```

### 2.6 mewcode/mcp/manager.py

```python
"""生命周期管理：并发启动 + 注册 + 退出清理（spec F11 / F12 / F14）。"""

import asyncio

from mewcode.mcp.adapter import MCPToolAdapter
from mewcode.mcp.client import MCPClient
from mewcode.mcp.config import ServerConfig
from mewcode.mcp.transport import HttpTransport, StdioTransport
from mewcode.tools import ToolRegistry


def _build_transport(cfg: ServerConfig):
    if cfg.type == "stdio":
        return StdioTransport(
            command=cfg.command, args=cfg.args, env=cfg.env, cwd=cfg.cwd,
        )
    return HttpTransport(url=cfg.url, headers=cfg.headers)


async def _start_one(cfg: ServerConfig) -> tuple[MCPClient, list]:
    """启动一个 Server：连接 + initialize + tools/list。"""
    transport = _build_transport(cfg)
    client = MCPClient(name=cfg.name, transport=transport, timeout=cfg.timeout)
    await client.initialize()
    tools = await client.list_tools()
    return client, tools


async def start_all(
    configs: dict[str, ServerConfig]
) -> dict[str, tuple[MCPClient, list]]:
    """并发启动所有 Server（spec F11）。

    单 Server 失败 → warning + 跳过（不抛异常）。

    Returns:
        {server_name: (client, [ToolInfo, ...])}
    """
    if not configs:
        return {}

    items = list(configs.items())
    tasks = [asyncio.create_task(_start_one(cfg)) for _, cfg in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, tuple[MCPClient, list]] = {}
    for (name, cfg), result in zip(items, results):
        if isinstance(result, BaseException):
            print(f"⚠️ MCP Server {name!r} 启动失败：{result}（已跳过）")
            continue
        client, tools = result
        out[name] = (client, tools)
    return out


def register_to(
    registry: ToolRegistry,
    started: dict[str, tuple[MCPClient, list]],
) -> int:
    """把 MCP 工具注册到 ToolRegistry。

    Returns:
        注册的工具总数。
    """
    count = 0
    for name, (client, tools) in started.items():
        for tool_info in tools:
            adapter = MCPToolAdapter(
                client=client,
                original_name=tool_info.name,
                description=tool_info.description,
                input_schema=tool_info.input_schema,
                timeout=client._timeout,  # 使用 client 的默认 timeout
            )
            registry.register(adapter)
            count += 1
    return count


async def shutdown_all(started: dict[str, tuple[MCPClient, list]]) -> None:
    """退出时关闭所有 Server。"""
    if not started:
        return
    tasks = [
        asyncio.create_task(client.shutdown())
        for client, _ in started.values()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
```

### 2.7 main.py 集成

```python
async def _amain(...) -> int:
    ...
    # 第六阶段：加载 MCP 配置
    from mewcode.mcp import load_all as load_mcp_configs
    from mewcode.mcp.manager import (
        start_all as mcp_start_all,
        register_to as mcp_register_to,
        shutdown_all as mcp_shutdown_all,
    )

    mcp_configs = load_mcp_configs(sandbox.cwd)
    mcp_started = await mcp_start_all(mcp_configs)
    mcp_count = mcp_register_to(registry, mcp_started)
    if mcp_count > 0:
        loaded = ", ".join(
            f"{name} ({len(tools)} 工具)"
            for name, (_, tools) in mcp_started.items()
        )
        renderer.print_info(f"🔌 已加载 MCP Server: {loaded}")

    try:
        return asyncio.run(run_repl(...))
    finally:
        # 退出清理
        try:
            asyncio.run(mcp_shutdown_all(mcp_started))
        except Exception:
            pass
```

注：实际上 `_amain` 已经是 async 函数，可以把所有 await 平铺，避免
asyncio.run 嵌套。具体实现按 main.py 现状决定。

## 3. 技术决策

### D1. 为什么自己写 JSON-RPC 而非用 mcp SDK

**决策**：用 stdlib + httpx 自己实现，不引入 `mcp` Python SDK。

**理由**：
- spec N2：本阶段不引入新依赖
- MewCode 此前所有底层（HTTP / SSE / Anthropic 协议解析）都手写，保持
  风格一致
- JSON-RPC 2.0 协议很简单（请求/响应/通知 三种消息）
- 实测 ~600 行 Python 完全够用
- 官方 SDK 还在迭代，未来可能 breaking
- 学习价值：让用户理解 MCP 协议本质

**代价**：
- 不能享受官方 SDK 的协议升级
- resources / prompts / sampling 后续要自己实现（但本阶段 spec F15 已排除）

### D2. 为什么两层而非三层

**决策**：用户级 + 项目级（不像权限系统那样有"本地级"）。

**理由**：
- MCP Server 配置较"基础设施"性质——不像权限规则那样需要"会话临时
  覆盖"或"本地不入 git"
- 敏感信息（如 API token）通过 `${VAR}` 走环境变量，不会写到 yaml
- 简化 = 用户认知成本低

如果未来需要"本地级"：直接补一层即可（loader 已经按列表合并）。

### D3. 为什么 HTTP 不维护持久 SSE 连接

**决策**：HTTP 模式下每次 tools/call 是独立 POST，不维持长连接。

**理由**：
- spec F15：本阶段不做 server-initiated 通知 / 流式工具响应
- 持久 SSE 连接意味着客户端要管理重连、心跳、消息路由——复杂度急升
- POST 单响应覆盖 tools/call 90% 场景
- 如果某 Server 只支持流式响应：tool_result 取首帧也能拿到主要内容

**代价**：
- 不支持 progress 通知
- 不支持模型 cancellation 回退到 server

### D4. 为什么 ${VAR} 缺失就跳过整个 Server

**决策**：env / headers 任一字段含未定义 ${VAR} → 整个 Server 跳过。

**理由**：
- Silent fail（替换为空字符串）会让 Server 拿到错误的凭证：
  `Authorization: Bearer ` → 401 → Server 启动失败 → 用户看不出原因
- 显式 warning 让用户立刻发现："忘了 export GITHUB_TOKEN"
- 一致性：spec D6（单 Server 失败跳过）的同源思想

### D5. 为什么工具名加 `mcp__<server>__<tool>` 双下划线前缀

**决策**：双下划线分隔的三段式 name。

**理由**：
- 单下划线（`mcp_filesystem_read_file`）会和 Server 名 / 工具名内部
  的下划线混淆
- Claude Code 的事实标准：`mcp__server__tool`
- 模型在大量 demonstration 中见过这个格式，识别准
- 双下划线正则容易匹配（permission rules 里写
  `mcp__filesystem__*` 可放行整个 Server）

### D6. 为什么 transport.call 直接同步 await

**决策**：transport.call 是 `async def`，发请求 + 等响应一起完成。

**理由**：
- 简化客户端 API：调用方不用管 future
- stdio 内部仍异步配对（reader_loop + PendingRegistry）；call 内部
  alloc id + register + write + await
- http 模式下 call 就是一次 POST + 解析（没有"配对"概念）
- 上层（client.py）调起来一致：`await transport.call(req, timeout)`

### D7. 为什么 MCP 工具默认 SAFE + readonly=False

**决策**：所有 MCP 工具默认 SAFE（不触发 confirmer）+ readonly=False
（Plan Mode 不可用）。

**理由**：
- SAFE：MCP 工具的安全交给第五阶段权限系统——用户主动写规则放行
  （`Mcp(mcp__filesystem__*)` 类，本阶段简化为字符串前缀匹配）
- readonly=False：不能假设 MCP 工具是只读（filesystem server 有 write
  也有 read）；Plan Mode 下统一禁用更安全
- 不在 yaml 暴露 readonly 配置：MVP 简化；高级用户后续阶段补充

### D8. 为什么 HTTP 模式 notify 用 POST 不等响应

**决策**：HttpTransport.notify 仍发 POST，但忽略响应（捕获所有异常）。

**理由**：
- MCP 协议规定通知是 fire-and-forget；Server 不应回响应
- 但 HTTP 是请求-响应模式，不发 POST 没法传递；发了也不在乎结果
- timeout 设短（10s），避免 notify 阻塞主流程

### D9. 为什么 client.py 不集成超时重试

**决策**：超时直接抛 TimeoutError，不重试。

**理由**：
- spec F13：明确不重试
- 重试可能让副作用工具（write/run）执行多次
- 模型层面会自动调整：失败的 ToolResult 反馈让模型决定下一步
- 简化错误路径

### D10. 为什么不暴露 /mcp 命令

**决策**：本阶段不实现 `/mcp show / reload` 命令。

**理由**：
- spec F15：明确推迟
- 启动时已打 `🔌 已加载 MCP Server: ...` 横幅，用户能看到
- /mcp reload 涉及优雅重启，复杂度高（需要 cancel pending tools/call）
- 用户重启 mewcode 即可拿到最新配置

后续阶段加 /mcp 命令是低成本扩展。

### D11. 为什么 MCPClient 不在 transport 内部 alloc id

**决策**：id 分配由 client.py 的 PendingRegistry 做；transport 不分配。

**理由**：
- id 是协议层概念，transport 是传输层
- 分层清晰：client 知道 id，transport 只管送字节
- 测试时 stub transport 不需要分配 id（client 来分配）

注：实际代码中，stdio transport 内部也维护 _pending（用于路由响应），
但 id 是 client 传进来的。"分配 id" 只在 client 这边发生。

### D12. 为什么并发启动而非串行

**决策**：用 asyncio.gather 并发 + return_exceptions=True。

**理由**：
- spec N7：性能。5 个 Server 各 200ms 握手 → 串行 1s vs 并发 200ms
- gather 的 return_exceptions 让单 Server 失败不影响其他
- 启动时已经在 async 上下文里，gather 是天然选择

### D13. 为什么 MCP 工具不进入 PermissionPolicy 的 TOOL_NAME_MAP

**决策**：rules.py 的 TOOL_NAME_MAP 不为 MCP 工具新增条目。

**理由**：
- 第五阶段 TOOL_NAME_MAP 是固定 6 个内置工具的"友好名"映射
- MCP 工具数量是动态的（取决于 Server），不能预定义
- 用户写 MCP 规则需要用全名前缀：
  `Bash(mcp__filesystem__*)`——但这里 Bash 是工具类型，不是真正的
  bash command。本阶段简化处理：

  rules.py 的 parse_rule 在解析未知工具名时返回 None；MCP 工具的
  权限通过 `extract_match_target` 返回工具的全名作为 target，但现有
  逻辑只支持 6 个内置工具。

  → 简化方案：MCP 工具默认走 ask 路径（默认 mode=default + 无规则）；
  用户用 `/permissions allow "<规则>"` 时，本阶段我们扩展 rules.py
  的 TOOL_NAME_MAP 加一个 "mcp" 桶，让用户可以写
  `Mcp(mcp__filesystem__*)` 来匹配所有 MCP 工具。

  详细修改在 plan 5（文件清单）的 rules.py 修改项体现。

### D14. 为什么 timeout 在 ServerConfig 而非全局

**决策**：每个 Server 单独配 timeout，不全局。

**理由**：
- 不同 Server 性能差异大：本地 stdio 10ms 响应，远程 HTTP 工具可能
  几十秒（如 LLM 工具）
- 单 Server 配置的 timeout 默认 60s，覆盖大部分场景
- 不引入"工具级 timeout"（更细粒度）：MVP 简化

## 4. 时序图

### 4.1 启动流程

```
main.py    config       manager       client       transport      Server进程
   │         │            │             │             │              │
   │ load_all(cwd)        │             │             │              │
   ├────────►│            │             │             │              │
   │◄────────┤  {name:cfg}                                            │
   │                                                                  │
   │ start_all(configs)   │             │             │              │
   ├──────────────────────►│            │             │              │
   │                      │ asyncio.gather                            │
   │                      ├────────────►│             │              │
   │                      │             │ initialize()                │
   │                      │             ├────────────►│ start()       │
   │                      │             │             ├─create_subprocess─►
   │                      │             │             ├─reader_loop ─►│
   │                      │             │             │               │
   │                      │             │ initialize JSON-RPC          │
   │                      │             ├────────────►│ ──────────────►│
   │                      │             │             │ ←──────────────┤ {result:...}
   │                      │             │ ←───────────┤               │
   │                      │             │ notif/initialized           │
   │                      │             ├────────────►│ ──────────────►│
   │                      │             │ tools/list                  │
   │                      │             ├────────────►│ ──────────────►│
   │                      │             │ ←───────────┤ ←──────────────┤ {tools:[...]}
   │                      │ ←───────────┤             │               │
   │                      │  (client, [ToolInfo,...])                  │
   │ ◄────────────────────┤  {name: (client, tools)}                   │
   │                                                                  │
   │ register_to(registry, started)                                   │
   ├──────────────────────►│            │             │              │
   │                      │ for each tool: registry.register(MCPToolAdapter)
   │ ◄────────────────────┤   返回总数                                  │
```

### 4.2 模型调用 MCP 工具

```
chat.engine    policy      MCPToolAdapter   MCPClient   StdioTransport   Server
   │             │              │              │             │              │
   │ tu.name="mcp__fs__read_file"             │             │              │
   │ policy.check(...)         │              │             │              │
   ├────────────►│              │              │             │              │
   │             │ 走规则 / 询问 / allow                                      │
   │ ◄───────────┤  Decision("allow")          │             │              │
   │                                                                          │
   │ adapter.execute(params, sandbox, ...)                                    │
   ├──────────────────────────►│              │             │              │
   │                           │ client.call_tool("read_file", {path:"a"})  │
   │                           ├─────────────►│             │              │
   │                           │              │ encode_request + transport.call
   │                           │              ├────────────►│              │
   │                           │              │             │ stdin.write  │
   │                           │              │             ├─────────────►│
   │                           │              │             │              │ 工具执行
   │                           │              │             │ ←────────────┤ {result:{content:[...]}}
   │                           │              │             │ reader 路由   │
   │                           │              │ ←───────────┤              │
   │                           │ ←────────────┤  {result}                   │
   │                           │ _parse_call_result → CallResult            │
   │                           │              │                             │
   │ ◄─────────────────────────┤  ToolResult(success, text)                 │
```

### 4.3 单 Server 启动失败

```
main      manager        Server A        Server B (broken)        Server C
 │           │              │                  │                       │
 │ start_all                │                  │                       │
 ├──────────►│              │                  │                       │
 │           │ gather(...)  │                  │                       │
 │           ├─_start_one A ►│ initialize ✓    │                       │
 │           ├─_start_one B ──────────────────►│ FileNotFoundError     │
 │           ├─_start_one C ──────────────────────────────────────────►│ initialize ✓
 │           │              │                  │                       │
 │           │ ←results: [client_A, FileNotFoundError, client_C]        │
 │           │ for B: print warning, skip                               │
 │           │ ←──── {A: (client_A, tools), C: (client_C, tools)}       │
 │ ◄─────────┤                                                          │
```

### 4.4 退出清理

```
main         manager         clients
 │              │              │
 │ shutdown_all │              │
 ├─────────────►│              │
 │              │ gather([c.shutdown() for c in clients])
 │              ├─────────────►│
 │              │              │ stdio: close stdin → terminate → kill
 │              │              │ http:  client.aclose()
 │ ◄────────────┤  done         │
```

## 5. 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/mcp/__init__.py` |
| 新建 | `mewcode/mcp/config.py` |
| 新建 | `mewcode/mcp/protocol.py` |
| 新建 | `mewcode/mcp/transport.py` |
| 新建 | `mewcode/mcp/client.py` |
| 新建 | `mewcode/mcp/adapter.py` |
| 新建 | `mewcode/mcp/manager.py` |
| 修改 | `mewcode/permissions/rules.py` (+ "mcp" 工具桶) |
| 修改 | `mewcode/main.py` (启动 / 退出集成 MCP) |
| 修改 | `.gitignore` (+ .mewcode/mcp_servers.local.yaml 预留) |
| 新建 | `tests/test_mcp_config.py` |
| 新建 | `tests/test_mcp_protocol.py` |
| 新建 | `tests/test_mcp_transport_stdio.py` |
| 新建 | `tests/test_mcp_transport_http.py` |
| 新建 | `tests/test_mcp_client.py` |
| 新建 | `tests/test_mcp_adapter.py` |
| 新建 | `tests/test_mcp_manager.py` |
| 新建 | `scripts/verify_mcp.py` |

共 18 个文件（13 新建 + 4 修改 + 1 .gitignore 预留）。

## 6. 与第五阶段的兼容矩阵

| 第五阶段行为 | 第六阶段是否保留 | 说明 |
|-------------|-----------------|------|
| run_turn 签名 | ✅ 不变 | MCP 工具透过 ToolRegistry 注入 |
| ToolRegistry 接口 | ✅ 不变 | MCPToolAdapter 实现 Tool 接口注册 |
| Sandbox.resolve / safe_open | ✅ 不变 | MCP 工具不走 sandbox（远端执行） |
| PermissionPolicy.check | ✅ 不变 | MCP 工具走相同 policy 流程 |
| Confirmer | ✅ 不变 | MCP 工具默认 SAFE 不触发 |
| Plan Mode 物理隔离 | ✅ 保留 | MCP 工具默认 readonly=False，Plan 不可用 |
| Agent Loop 五种停止 | ✅ 保留 | |
| AgentEvent 7 种 | ✅ 不变 | MCP 工具调用走相同事件 |
| system prompt 7 模块 | ✅ 不变 | |
| prompt cache | ✅ 不变 | tools 数组含 MCP 工具，仍命中 cache |
| /clear /provider /think /plan /do /permissions | ✅ 不变 |
| 第五阶段 238 单测 | ✅ 全过 | 新模块独立 |

### 不需要适配的已有测试

无——MCP 是纯新增功能：
- 不传 mcp_servers.yaml 时（spec AC25），mewcode 启动行为完全等同第五阶段
- ToolRegistry 注册 MCP 工具是叠加，不影响内置 6 个工具
- PermissionPolicy 对 MCP 工具的处理是默认 ask（与未匹配普通工具行为一致）

唯一可能影响：rules.py 增加 "mcp" 工具桶（D13）后，原有 TOOL_NAME_MAP
单测可能要补一个 case 验证 mcp 桶——非阻塞改动。
