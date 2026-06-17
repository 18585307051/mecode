"""Provider 流式事件类型。

Provider 不直接返回原始 SSE 帧，而是返回一组协议无关的统一事件，
让 chat 层完全不感知具体的 wire protocol。

事件流约定（按出现顺序）：
    [ThinkingDelta × N (可选)] → [TextDelta × N] → Usage(可选) → Done

具体规则：
- ThinkingDelta 仅在 Anthropic 协议 + thinking 开启时出现，
  且全部出现在 TextDelta 之前。
- Usage 在 Done 之前最多出现一次；后端未返回 usage 数据时省略此事件。
- Done 是流正常结束的唯一标志；异常情况通过抛出异常表达，不发 Done。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    """正文增量。每收到一个 chunk 发一次。"""

    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """思考增量。仅 Anthropic 协议 + thinking 开启时出现。"""

    text: str


@dataclass(frozen=True)
class Usage:
    """本次调用的 token 用量。

    Attributes:
        input_tokens:    输入 token 数。
        output_tokens:   输出 token 数。
        thinking_tokens: 思考 token 数；后端未返回或不适用时为 None，
                         此时 Renderer 应跳过该项不显示。
    """

    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None = None


@dataclass(frozen=True)
class Done:
    """流正常结束的标记，位于事件流末尾。"""

    pass


# 流式事件的联合类型
StreamEvent = TextDelta | ThinkingDelta | Usage | Done
