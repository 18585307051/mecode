"""OpenAI 协议 Provider 实现。

端点：     POST {base_url}/v1/chat/completions
请求头：   Authorization: Bearer <api_key>、content-type
请求体：   {model, stream, stream_options, messages}
SSE：      OpenAI 兼容协议的 SSE 没有 event 字段，所有信息在 data
           的 JSON 里；以 `data: [DONE]` 哨兵标记流结束。

事件流约定保持不变：
- 收到 `choices[0].delta.content` → TextDelta
- 收到含 `usage` 的最后一帧 → Usage
- 收到 `data: [DONE]` → Done

本协议不支持 extended thinking，构造请求体时忽略 thinking 参数；
spec F9 已规定命令层在用户尝试 /think on 时给出"不支持"提示。
"""

import json
from collections.abc import AsyncIterator

from mewcode.providers.base import Message, Provider
from mewcode.providers.errors import StreamParseError
from mewcode.providers.events import (
    Done,
    StreamEvent,
    TextDelta,
    Usage,
)
from mewcode.transport import iter_sse_frames, stream_post


class OpenAIProvider(Provider):
    """走 OpenAI /v1/chat/completions 协议的 Provider 实现。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,  # 本协议忽略此参数
    ) -> AsyncIterator[StreamEvent]:
        url = f"{self._config.base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "content-type": "application/json",
        }

        body: dict = {
            "model": self._config.model,
            "stream": True,
            # include_usage=True 让兼容后端在最后一帧返回 usage 信息
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }

        input_tokens = 0
        output_tokens = 0
        finished = False

        byte_stream = stream_post(url, headers, body)
        sse_stream = iter_sse_frames(byte_stream)

        try:
            async for frame in sse_stream:
                if finished:
                    continue
                if not frame.data:
                    continue

                # OpenAI 协议结束哨兵
                if frame.data.strip() == "[DONE]":
                    yield Done()
                    finished = True
                    continue

                try:
                    data_obj = json.loads(frame.data)
                except json.JSONDecodeError as e:
                    raise StreamParseError(
                        f"无法解析 OpenAI SSE 数据: {e}; 原始: {frame.data[:200]}"
                    ) from e

                # 文本增量：choices[0].delta.content
                choices = data_obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield TextDelta(text=content)
                    # finish_reason、role 等其他字段在本阶段不需要处理

                # usage 帧（带 stream_options=include_usage 时最后一帧带）
                usage = data_obj.get("usage")
                if isinstance(usage, dict):
                    if isinstance(usage.get("prompt_tokens"), int):
                        input_tokens = usage["prompt_tokens"]
                    if isinstance(usage.get("completion_tokens"), int):
                        output_tokens = usage["completion_tokens"]
                    yield Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        thinking_tokens=None,
                    )
        finally:
            # 显式关闭底层异步生成器，吞掉清理路径上的良性异常
            for s in (sse_stream, byte_stream):
                try:
                    await s.aclose()
                except BaseException:
                    pass
