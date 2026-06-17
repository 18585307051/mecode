"""OpenAI 协议 Provider 实现。

端点：     POST {base_url}/v1/chat/completions
请求头：   Authorization: Bearer <api_key>、content-type
请求体：   {model, stream, stream_options, messages, tools?}
SSE：      OpenAI 兼容协议的 SSE 没有 event 字段，所有信息在 data
           的 JSON 里；以 `data: [DONE]` 哨兵标记流结束。

历史序列化（spec F16/F17）：
- assistant 消息：拆分 text 部分与 tool_use 部分
  → {"role":"assistant", "content": <文本拼接 or null>,
     "tool_calls": [{"id": ..., "type":"function",
       "function": {"name":..., "arguments": json.dumps(input)}}]}
  thinking 块在 OpenAI 协议下不存在，忽略
- user 消息含 ToolResultBlock：每个 ToolResultBlock 单独一条
  {"role":"tool", "tool_call_id": ..., "content": ...} 消息
- user 纯文本：{"role":"user", "content": ...}

SSE 工具调用解析：
- delta.tool_calls 按 index 维度归并
  - 首次见到 index → 取 id/name → ToolUseStart
  - arguments 增量 → 累加 args_buf + ToolUseInputDelta
  - finish_reason == "tool_calls" → 遍历 state 发 ToolUseEnd

本协议不支持 extended thinking，构造请求体时忽略 thinking 参数；
spec F9 已规定命令层在用户尝试 /think on 时给出"不支持"提示。
"""

import json
from collections.abc import AsyncIterator

from mewcode.providers.base import Message, Provider
from mewcode.providers.blocks import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.providers.errors import StreamParseError
from mewcode.providers.events import (
    Done,
    StreamEvent,
    TextDelta,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
)
from mewcode.transport import iter_sse_frames, stream_post


def _serialize_messages_openai(messages: list[Message]) -> list[dict]:
    """把 list[Message] 翻译成 OpenAI API 接受的 messages 字段格式。

    spec F16 / F17 协议适配：
    - assistant 消息中的 ToolUseBlock → tool_calls 字段
    - user 消息中的 ToolResultBlock → 独立 role=tool 的消息
    - ThinkingBlock 在 OpenAI 协议下忽略
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant":
            out.append(_serialize_assistant_message(m.content))
        else:  # user
            # user 消息可能是纯文本也可能是 tool_results；后者要拆成多条 role=tool
            if all(isinstance(b, TextBlock) for b in m.content):
                text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                out.append({"role": "user", "content": text})
            else:
                # 含 ToolResultBlock：先把任何前置 TextBlock 作为 user 文本消息，
                # 再为每个 ToolResultBlock 单独生成一条 role=tool 消息
                pre_text_parts = []
                tool_results: list[ToolResultBlock] = []
                for b in m.content:
                    if isinstance(b, TextBlock):
                        pre_text_parts.append(b.text)
                    elif isinstance(b, ToolResultBlock):
                        tool_results.append(b)
                if pre_text_parts:
                    out.append({"role": "user", "content": "".join(pre_text_parts)})
                for r in tool_results:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": r.tool_use_id,
                            "content": r.content,
                        }
                    )
    return out


def _serialize_assistant_message(blocks) -> dict:
    """把 assistant 消息的块列表打包成 OpenAI 单条消息。"""
    text_parts = []
    tool_calls = []
    for b in blocks:
        if isinstance(b, TextBlock):
            text_parts.append(b.text)
        elif isinstance(b, ToolUseBlock):
            tool_calls.append(
                {
                    "id": b.id,
                    "type": "function",
                    "function": {
                        "name": b.name,
                        "arguments": json.dumps(b.input, ensure_ascii=False),
                    },
                }
            )
        # ThinkingBlock 在 OpenAI 协议下忽略

    msg: dict = {"role": "assistant"}
    # OpenAI 要求 content 是字符串或 null；纯工具调用时常见做法是 content=null
    msg["content"] = "".join(text_parts) if text_parts else None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


class OpenAIProvider(Provider):
    """走 OpenAI /v1/chat/completions 协议的 Provider 实现。"""

    async def stream_chat(  # type: ignore[override]
        self,
        messages: list[Message],
        thinking: bool,  # 本协议忽略此参数
        tools_format: list[dict] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        url = f"{self._config.base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "content-type": "application/json",
        }

        # OpenAI 协议：system 通过 messages[0] = {role:"system", content:...} 表达
        serialized = _serialize_messages_openai(messages)
        if system:
            serialized = [{"role": "system", "content": system}] + serialized

        body: dict = {
            "model": self._config.model,
            "stream": True,
            # include_usage=True 让兼容后端在最后一帧返回 usage 信息
            "stream_options": {"include_usage": True},
            "messages": serialized,
        }
        if tools_format:
            body["tools"] = tools_format

        input_tokens = 0
        output_tokens = 0
        finished = False

        # tool_calls 累积：index → {"id":..., "name":..., "args": <累计>}
        # OpenAI 流中按 tool_calls[i].index 区分多个并发工具调用
        tool_call_state: dict[int, dict] = {}

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
                    # 在 [DONE] 之前如果还有未发的 ToolUseEnd（极少见，正常应在
                    # finish_reason="tool_calls" 时已发），这里兜底再发一次
                    for state in tool_call_state.values():
                        yield self._make_tool_use_end(state)
                    tool_call_state.clear()

                    yield Done()
                    finished = True
                    continue

                try:
                    data_obj = json.loads(frame.data)
                except json.JSONDecodeError as e:
                    raise StreamParseError(
                        f"无法解析 OpenAI SSE 数据: {e}; 原始: {frame.data[:200]}"
                    ) from e

                # 处理 choices 中的 delta（文本与工具调用）
                choices = data_obj.get("choices") or []
                if choices:
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    # 文本增量
                    content = delta.get("content")
                    if content:
                        yield TextDelta(text=content)

                    # 工具调用增量
                    tool_calls_delta = delta.get("tool_calls")
                    if tool_calls_delta:
                        for tc in tool_calls_delta:
                            idx = tc.get("index")
                            if not isinstance(idx, int):
                                continue
                            state = tool_call_state.get(idx)
                            if state is None:
                                # 首次见到此 index：取 id 与 name
                                tu_id = tc.get("id", "")
                                fn = tc.get("function") or {}
                                tu_name = fn.get("name", "")
                                state = {
                                    "id": tu_id,
                                    "name": tu_name,
                                    "args": "",
                                }
                                tool_call_state[idx] = state
                                yield ToolUseStart(id=tu_id, name=tu_name)
                            else:
                                # 后续帧可能补全 id 或 name（极少见）
                                if not state["id"] and tc.get("id"):
                                    state["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if not state["name"] and fn.get("name"):
                                    state["name"] = fn["name"]

                            fn = tc.get("function") or {}
                            args_chunk = fn.get("arguments")
                            if args_chunk:
                                state["args"] += args_chunk
                                yield ToolUseInputDelta(
                                    id=state["id"], json_chunk=args_chunk
                                )

                    # finish_reason == "tool_calls"：发 ToolUseEnd
                    finish_reason = choice.get("finish_reason")
                    if finish_reason == "tool_calls":
                        # 按 index 排序后发出，保证顺序与模型给出的一致
                        for idx in sorted(tool_call_state.keys()):
                            state = tool_call_state[idx]
                            yield self._make_tool_use_end(state)
                        tool_call_state.clear()

                # usage 帧（最后一帧，含 stream_options=include_usage 时）
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
            # 显式关闭底层异步生成器，吞清理路径上的良性异常
            for s in (sse_stream, byte_stream):
                try:
                    await s.aclose()
                except BaseException:
                    pass

    @staticmethod
    def _make_tool_use_end(state: dict) -> ToolUseEnd:
        """把累积的 args 字符串 json.loads 后构造 ToolUseEnd。"""
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
        return ToolUseEnd(id=state["id"], name=state["name"], input=input_obj)
