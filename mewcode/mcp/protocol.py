"""JSON-RPC 2.0 编解码 + 异步配对（spec F4 / D9 / D10）。

三种消息类型：
  Request:      {"jsonrpc":"2.0", "id":N, "method":"...", "params":{...}}
  Response OK:  {"jsonrpc":"2.0", "id":N, "result":{...}}
  Response Err: {"jsonrpc":"2.0", "id":N, "error":{"code":N, "message":"..."}}
  Notification: {"jsonrpc":"2.0", "method":"...", "params":{...}}
"""

import asyncio
import itertools
from dataclasses import dataclass


class MCPProtocolError(Exception):
    """MCP 协议错误（JSON-RPC error 响应或传输层错误）。"""

    def __init__(self, code: int, message: str, data=None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPTimeoutError(Exception):
    """MCP 调用超时（spec F13 / D9）。"""


def encode_request(req_id: int, method: str, params: dict | None = None) -> dict:
    """构造 JSON-RPC 请求 dict。"""
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def encode_notification(method: str, params: dict | None = None) -> dict:
    """构造 JSON-RPC 通知 dict（无 id，不等响应）。"""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


@dataclass
class _Pending:
    """一条 pending 请求。"""

    future: asyncio.Future
    method: str


class PendingRegistry:
    """id ↔ Future 异步配对（spec F4 / D10）。

    线程模型：仅在单个 asyncio 事件循环内使用，无锁。
    """

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._pending: dict[int, _Pending] = {}

    def alloc_id(self) -> int:
        """分配下一个自增 id。"""
        return next(self._counter)

    def register(self, req_id: int, method: str, future: asyncio.Future) -> None:
        """注册一条 pending 请求。"""
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

    def cancel(self, req_id: int) -> None:
        """取消单条 pending（超时清理用）。"""
        self._pending.pop(req_id, None)

    def fail_all(self, exc: Exception) -> None:
        """传输关闭时把所有 pending 设为异常（避免泄漏）。"""
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._pending.clear()
