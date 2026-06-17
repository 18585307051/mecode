"""Anthropic 协议 Provider 实现。

端点：     POST {base_url}/v1/messages
请求头：   x-api-key、anthropic-version、content-type
请求体：   {model, max_tokens, stream, messages, tools?, thinking?}

历史序列化（spec F16/F17）：
- assistant 消息 content 块翻译：
  - TextBlock     → {"type":"text", "text": ...}
  - ThinkingBlock → {"type":"thinking", "thinking": ..., "signature": ...}
  - ToolUseBlock  → {"type":"tool_use", "id": ..., "name": ..., "input": ...}
- user 消息：
  - 全是 TextBlock 单块 → {"role":"user", "content": "<text>"}（兼容写法）
  - 含 ToolResultBlock → content 为块列表，每个 ToolResultBlock 翻译为
                          {"type":"tool_result", "tool_use_id":..., "content":..., "is_error":bool}

SSE 事件 → StreamEvent 映射（含工具调用）：

    Anthropic 事件                                                  → 内部事件
    --------------------------------------------------------------------------
    message_start (含 input_tokens)                                  → 记录 input_tokens
    content_block_start (type=tool_use, id=X, name=Y)                → ToolUseStart(X, Y)
    content_block_delta (text_delta)                                 → TextDelta
    content_block_delta (thinking_delta)                             → ThinkingDelta（thinking 关闭时丢弃）
    content_block_delta (input_json_delta, partial_json=P)           → ToolUseInputDelta + 累计
    content_block_stop（在 tool_use 块上）                            → ToolUseEnd(json.loads(args))
    message_delta (含 output_tokens)                                 → 记录 output_tokens
    message_stop                                                      → 发 Usage + Done

其他事件（ping、text/thinking 块的 content_block_start/stop 等）忽略。
"""

import json
from collections.abc import AsyncIterator

from mewcode.providers.base import Message, Provider
from mewcode.providers.blocks import (
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.providers.errors import StreamParseError
from mewcode.providers.events import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
)
from mewcode.transport import iter_sse_frames, stream_post

# 写死的请求参数。spec 规定本阶段不暴露给用户调节（YAGNI）。
_MAX_TOKENS = 8192
_THINKING_BUDGET = 4096
_ANTHROPIC_VERSION = "2023-06-01"


def _serialize_messages_anthropic(messages: list[Message]) -> list[dict]:
    """把 list[Message] 翻译成 Anthropic API 接受的 messages 字段格式。

    spec F16 / F17 协议适配：
    - assistant 消息：content 是块列表，每个块按类型翻译
    - user 消息：纯文本时简化为 content=str；含 ToolResultBlock 时块列表
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant":
            blocks = [_serialize_assistant_block(b) for b in m.content]
            blocks = [b for b in blocks if b is not None]
            out.append({"role": "assistant", "content": blocks})
        else:  # user
            # 全是 TextBlock → 简化为字符串内容
            if all(isinstance(b, TextBlock) for b in m.content):
                text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                out.append({"role": "user", "content": text})
            else:
                blocks = [_serialize_user_block(b) for b in m.content]
                blocks = [b for b in blocks if b is not None]
                out.append({"role": "user", "content": blocks})
    return out


def _serialize_assistant_block(b) -> dict | None:
    """翻译 assistant 消息中的单个内容块。"""
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": b.text,
            "signature": b.signature,
        }
    if isinstance(b, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": b.id,
            "name": b.name,
            "input": b.input,
        }
    # ToolResultBlock 不应出现在 assistant 消息中
    return None


def _serialize_user_block(b) -> dict | None:
    """翻译 user 消息中的单个内容块（含 tool_result 块）。"""
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ToolResultBlock):
        result: dict = {
            "type": "tool_result",
            "tool_use_id": b.tool_use_id,
            "content": b.content,
        }
        if b.is_error:
            result["is_error"] = True
        return result
    return None


class AnthropicProvider(Provider):
    """走 Anthropic /v1/messages 协议的 Provider 实现。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,
        tools_format: list[dict] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        url = f"{self._config.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        body: dict = {
            "model": self._config.model,
            "max_tokens": _MAX_TOKENS,
            "stream": True,
            "messages": _serialize_messages_anthropic(messages),
        }
        if system:
            # Anthropic 协议：system 走请求体顶层字段。
            # 第四阶段（spec F4 / D1）：升级为列表形式，最后一项加
            # cache_control={"type":"ephemeral"} 把整段 system 纳入
            # prompt cache，避免每次重复计费。
            body["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": _THINKING_BUDGET,
            }
        if tools_format:
            # tools_format 由 chat 层用 ToolRegistry.to_anthropic_format_with_cache()
            # 生成，最后一项已经含 cache_control（spec F4 / D2）。
            body["tools"] = tools_format

        # ----------------- 累积状态 -----------------
        # input_tokens 在 message_start 时拿到，output_tokens 来自 message_delta。
        # thinking_tokens 多数后端不单独返回，保持 None；详见前一版的注释。
        # cache_creation/read：第四阶段新增（spec F8），从 message_start 与
        # message_delta 的 usage 中提取，None 表示后端未返回。
        input_tokens = 0
        output_tokens = 0
        thinking_tokens: int | None = None
        cache_creation_input_tokens: int | None = None
        cache_read_input_tokens: int | None = None

        # tool_use 累积：index → {"id":..., "name":..., "args": <累计 JSON 字符串>}
        # 同一时刻可能有多个 tool_use 块并发存在（虽然实测 Anthropic 是按 index 串行），
        # 用 dict 按 index 维度归并 input_json_delta。
        tool_use_buf: dict[int, dict] = {}

        # 收到 message_stop 后置 True，后续 SSE 帧继续消费但不再产生事件。
        finished = False

        byte_stream = stream_post(url, headers, body)
        sse_stream = iter_sse_frames(byte_stream)

        try:
            async for frame in sse_stream:
                if finished:
                    continue
                if not frame.data:
                    continue

                try:
                    data_obj = json.loads(frame.data)
                except json.JSONDecodeError as e:
                    raise StreamParseError(
                        f"无法解析 Anthropic SSE 数据: {e}; 原始: {frame.data[:200]}"
                    ) from e

                event = frame.event

                # message_start：抽取 input_tokens 与 cache 字段
                if event == "message_start":
                    msg = data_obj.get("message", {})
                    usage = msg.get("usage", {})
                    if isinstance(usage.get("input_tokens"), int):
                        input_tokens = usage["input_tokens"]
                    # spec F8：缓存命中字段（None 表示后端未返回）
                    cc = usage.get("cache_creation_input_tokens")
                    if isinstance(cc, int):
                        cache_creation_input_tokens = cc
                    cr = usage.get("cache_read_input_tokens")
                    if isinstance(cr, int):
                        cache_read_input_tokens = cr
                    continue

                # content_block_start：可能是 text / thinking / tool_use 块开始
                if event == "content_block_start":
                    idx = data_obj.get("index")
                    block = data_obj.get("content_block", {})
                    if block.get("type") == "tool_use" and isinstance(idx, int):
                        tu_id = block.get("id", "")
                        tu_name = block.get("name", "")
                        tool_use_buf[idx] = {
                            "id": tu_id,
                            "name": tu_name,
                            "args": "",
                        }
                        yield ToolUseStart(id=tu_id, name=tu_name)
                    # text / thinking 块的 start 不需要内部事件
                    continue

                # content_block_delta：text/thinking/工具参数 三种增量
                if event == "content_block_delta":
                    delta = data_obj.get("delta", {})
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield TextDelta(text=text)
                    elif dtype == "thinking_delta":
                        # 关键：仅在客户端启用 thinking 时才转发思考增量。
                        # DeepSeek 等后端即便客户端未传 thinking 字段也会主动
                        # 返回思考内容；Provider 层负责丢弃以满足 spec AC12。
                        if thinking:
                            text = delta.get("thinking", "")
                            if text:
                                yield ThinkingDelta(text=text)
                    elif dtype == "input_json_delta":
                        idx = data_obj.get("index")
                        partial = delta.get("partial_json", "")
                        if isinstance(idx, int) and idx in tool_use_buf:
                            tool_use_buf[idx]["args"] += partial
                            yield ToolUseInputDelta(
                                id=tool_use_buf[idx]["id"], json_chunk=partial
                            )
                    continue

                # content_block_stop：tool_use 块结束时发 ToolUseEnd
                if event == "content_block_stop":
                    idx = data_obj.get("index")
                    if isinstance(idx, int) and idx in tool_use_buf:
                        state = tool_use_buf.pop(idx)
                        args_str = state["args"] or "{}"
                        try:
                            input_obj = json.loads(args_str)
                        except json.JSONDecodeError as e:
                            raise StreamParseError(
                                f"工具调用参数 JSON 解析失败 "
                                f"(name={state['name']}, id={state['id']}): {e}; "
                                f"原始: {args_str[:200]}"
                            ) from e
                        if not isinstance(input_obj, dict):
                            raise StreamParseError(
                                f"工具调用参数必须是对象 "
                                f"(name={state['name']}, id={state['id']}, "
                                f"got {type(input_obj).__name__})"
                            )
                        yield ToolUseEnd(
                            id=state["id"], name=state["name"], input=input_obj
                        )
                    # text / thinking 块的 stop 直接忽略
                    continue

                # message_delta：抽取 output_tokens 与 cache 字段
                if event == "message_delta":
                    usage = data_obj.get("usage", {})
                    if isinstance(usage.get("output_tokens"), int):
                        output_tokens = usage["output_tokens"]
                    # 部分后端在 message_delta 才返回 cache 字段
                    cc = usage.get("cache_creation_input_tokens")
                    if isinstance(cc, int):
                        cache_creation_input_tokens = cc
                    cr = usage.get("cache_read_input_tokens")
                    if isinstance(cr, int):
                        cache_read_input_tokens = cr
                    continue

                # message_stop：发 Usage（如有数据）+ Done，标记结束但不退出循环
                if event == "message_stop":
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        thinking_tokens=thinking_tokens,
                        cache_creation_input_tokens=cache_creation_input_tokens,
                        cache_read_input_tokens=cache_read_input_tokens,
                    )
                    yield Done()
                    finished = True
                    continue

                # 其他事件（ping 等）：忽略
        finally:
            # 显式关闭底层异步生成器，吞清理路径上的良性异常
            for s in (sse_stream, byte_stream):
                try:
                    await s.aclose()
                except BaseException:
                    pass
