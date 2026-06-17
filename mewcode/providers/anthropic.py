"""Anthropic 协议 Provider 实现。

端点：     POST {base_url}/v1/messages
请求头：   x-api-key、anthropic-version、content-type
请求体：   {model, max_tokens, stream, messages, thinking?}
SSE 事件 → StreamEvent 映射：

    Anthropic 事件                              → 内部事件
    -------------------------------------------------------
    message_start (含 input_tokens)             → 记录 input_tokens
    content_block_delta (text_delta)            → TextDelta
    content_block_delta (thinking_delta)        → ThinkingDelta（T10 启用）
    message_delta (含 output_tokens)            → 记录 output_tokens
    message_stop                                → 发 Usage + Done

其他事件（ping、content_block_start/stop 等）忽略。
"""

import json
from collections.abc import AsyncIterator

from mewcode.providers.base import Message, Provider
from mewcode.providers.errors import StreamParseError
from mewcode.providers.events import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    Usage,
)
from mewcode.transport import iter_sse_frames, stream_post

# 写死的请求参数。spec 规定本阶段不暴露给用户调节（YAGNI）。
_MAX_TOKENS = 8192
_THINKING_BUDGET = 4096
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    """走 Anthropic /v1/messages 协议的 Provider 实现。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,
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
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }
        if thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": _THINKING_BUDGET,
            }

        # 累积变量：input_tokens 在 message_start 时拿到，output_tokens
        # 来自 message_delta。
        # 关于 thinking_tokens：实测 DeepSeek 通过 Anthropic 协议返回的 usage
        # 中不单独包含思考 token 字段，思考 token 似乎已并入 output_tokens。
        # Anthropic 官方 API 当前版本也未在 message_delta.usage 中返回独立的
        # 思考 token 字段，所以这里保持 None；Renderer 判空决定是否显示该项
        # （spec F13：后端未返回时该行省略相应字段）。
        # 若未来后端开始单独返回（如 cache_creation_input_tokens 或专用
        # thinking_tokens 字段），在 message_delta 分支处补一行赋值即可。
        input_tokens = 0
        output_tokens = 0
        thinking_tokens: int | None = None

        # 收到 message_stop 后置 True，后续 SSE 帧（如 ping）继续消费但
        # 不再产生事件——为的是让底层 byte_stream 自然走到 EOF，避免
        # 提前 return 触发 httpx async with 的 GeneratorExit 清理路径。
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

                # message_start：抽取 input_tokens
                if event == "message_start":
                    msg = data_obj.get("message", {})
                    usage = msg.get("usage", {})
                    if isinstance(usage.get("input_tokens"), int):
                        input_tokens = usage["input_tokens"]
                    continue

                # content_block_delta：正文增量 / 思考增量
                if event == "content_block_delta":
                    delta = data_obj.get("delta", {})
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield TextDelta(text=text)
                    elif dtype == "thinking_delta":
                        # 关键：仅在客户端启用 thinking 时才转发思考增量。
                        # 兼容性保险——某些后端（如 DeepSeek 的 Anthropic 协议
                        # 端点）即便客户端未传 thinking 字段也会主动返回思考内容；
                        # 此时 Provider 层负责丢弃，让 spec AC12（默认关闭时
                        # 回复中无思考块）保持成立。
                        if thinking:
                            text = delta.get("thinking", "")
                            if text:
                                yield ThinkingDelta(text=text)
                    continue

                # message_delta：抽取 output_tokens
                if event == "message_delta":
                    usage = data_obj.get("usage", {})
                    if isinstance(usage.get("output_tokens"), int):
                        output_tokens = usage["output_tokens"]
                    continue

                # message_stop：发 Usage（如有数据）+ Done，标记结束但不退出循环
                if event == "message_stop":
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        thinking_tokens=thinking_tokens,
                    )
                    yield Done()
                    finished = True
                    continue

                # 其他事件（ping、content_block_start/stop 等）：忽略
        finally:
            # 显式关闭底层异步生成器，吞掉清理路径上的所有异常。
            # httpx 在 GC 路径中会从 socket 抛 ReadError、CancelledError 等
            # 良性 cleanup noise，如果不在这里 swallow，它们会通过 stderr
            # 渗漏到终端，污染输出（spec N4 控制字符泄漏）。
            for s in (sse_stream, byte_stream):
                try:
                    await s.aclose()
                except BaseException:
                    pass
