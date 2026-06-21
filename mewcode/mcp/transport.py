"""传输层：stdio + http（spec F6 / F7 / D11）。

共同接口 Transport：
  start()                 建立连接
  call(msg, timeout)      发请求 + 等响应，返回 result dict
  notify(msg)             发通知（不等响应）
  shutdown()              关闭

StdioTransport：
  - asyncio.create_subprocess_exec 启动子进程
  - reader_loop 持续 readline 并路由到 PendingRegistry
  - stderr_loop 捕获 stderr 到 buffer
  - shutdown：close stdin → 2s → terminate → 2s → kill

HttpTransport：
  - httpx.AsyncClient POST 单响应模式
  - 支持 application/json 与 text/event-stream（首帧）
  - notify 用 POST 不等响应
"""

import asyncio
import json
import os
import sys
from abc import ABC, abstractmethod

import httpx

from mewcode.mcp.protocol import MCPProtocolError, PendingRegistry


class Transport(ABC):
    """传输层抽象。"""

    @abstractmethod
    async def start(self) -> None:
        """建立连接（启动子进程 / 创建 HTTP client）。"""

    @abstractmethod
    async def call(self, msg: dict, timeout: float) -> dict:
        """发请求 + 等响应。

        Returns:
            JSON-RPC 响应的 result 字段（dict）。

        Raises:
            MCPProtocolError: JSON-RPC error 响应 / HTTP 错误。
            asyncio.TimeoutError: 超时。
        """

    @abstractmethod
    async def notify(self, msg: dict) -> None:
        """发通知（无响应）。"""

    @abstractmethod
    async def shutdown(self) -> None:
        """关闭连接。"""


class StdioTransport(Transport):
    """子进程 stdio 传输（spec F6 / D11）。"""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None,
    ) -> None:
        self._command = command
        self._args = args
        self._env = {**os.environ, **env}
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_buf: list[bytes] = []
        self._pending = PendingRegistry()
        self._closed = False

    async def start(self) -> None:
        """启动子进程 + 后台读循环。"""
        kwargs: dict = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": self._env,
        }
        if self._cwd:
            kwargs["cwd"] = self._cwd
        if sys.platform == "win32":
            import subprocess

            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        self._proc = await asyncio.create_subprocess_exec(
            self._command, *self._args, **kwargs
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _reader_loop(self) -> None:
        """持续读 stdout 行并路由到 pending future。"""
        assert self._proc and self._proc.stdout
        try:
            while not self._closed:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if "id" in msg:
                    self._pending.resolve(msg)
                # 通知（无 id）本阶段忽略
        except (asyncio.CancelledError, OSError):
            pass
        finally:
            self._pending.fail_all(
                ConnectionError("MCP stdio transport closed")
            )

    async def _stderr_loop(self) -> None:
        """捕获 stderr 到 buffer（debug 用）。"""
        assert self._proc and self._proc.stderr
        try:
            while not self._closed:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                self._stderr_buf.append(line)
                # 只保留最后 4KB 避免内存泄漏
                total = sum(len(b) for b in self._stderr_buf)
                if total > 4096:
                    self._stderr_buf = self._stderr_buf[-4:]
        except (asyncio.CancelledError, OSError):
            pass

    async def call(self, msg: dict, timeout: float) -> dict:
        """发请求 + 等响应（spec F4）。"""
        assert self._proc and self._proc.stdin
        req_id = msg["id"]
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.register(req_id, msg["method"], future)
        try:
            self._proc.stdin.write(
                (json.dumps(msg) + "\n").encode("utf-8")
            )
            await self._proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.cancel(req_id)
            raise
        except (BrokenPipeError, ConnectionResetError) as e:
            self._pending.cancel(req_id)
            raise MCPProtocolError(-32000, f"stdio 传输断开：{e}") from e

    async def notify(self, msg: dict) -> None:
        """发通知（不等响应）。"""
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def shutdown(self) -> None:
        """关闭子进程（spec F14）。"""
        if self._closed:
            return
        self._closed = True

        # 取消后台协程
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()

        if self._proc:
            # 关闭 stdin
            try:
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            except Exception:
                pass
            # 等待退出，宽限 2s
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    self._proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    try:
                        self._proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await self._proc.wait()
                    except Exception:
                        pass

        # 等后台协程退出
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                try:
                    await task
                except (asyncio.CancelledError, BaseException):
                    pass

    @property
    def stderr_text(self) -> str:
        """累积的 stderr 文本（debug 用）。"""
        return b"".join(self._stderr_buf).decode("utf-8", errors="replace")


class HttpTransport(Transport):
    """Streamable HTTP 传输（spec F7 / D12）。

    POST 单响应模式：每次 call 是一次独立 POST。
    """

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
                resp.status_code,
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            data = resp.json()
        elif "text/event-stream" in ct:
            data = self._parse_first_sse_data(resp.text)
        else:
            raise MCPProtocolError(
                -32000, f"未知 Content-Type: {ct!r}"
            )

        if "error" in data:
            err = data["error"]
            raise MCPProtocolError(
                err.get("code", -1),
                err.get("message", ""),
                err.get("data"),
            )
        return data.get("result", {})

    @staticmethod
    def _parse_first_sse_data(text: str) -> dict:
        """解析 SSE 流的第一个 data 帧（spec D12）。"""
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    return json.loads(payload)
        raise MCPProtocolError(-32000, "SSE 响应无 data 帧")

    async def notify(self, msg: dict) -> None:
        """HTTP 模式下通知用 POST 不等响应（spec D8）。"""
        assert self._client
        try:
            await self._client.post(
                self._url,
                content=json.dumps(msg).encode("utf-8"),
                headers=self._headers,
                timeout=10.0,
            )
        except Exception:
            pass

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
