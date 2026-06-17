"""Provider 流式事件类型。

Provider 不直接返回原始 SSE 帧，而是返回一组协议无关的统一事件，
让 chat 层完全不感知具体的 wire protocol。

第二阶段事件流约定（按出现顺序）：
    [ThinkingDelta × N (可选)]
    → [TextDelta × N 与 ToolUse* 事件交替出现，按模型生成顺序]
    → Usage(可选)
    → Done

工具调用事件子序列（按工具调用 ID 维度同步）：
    ToolUseStart(id, name)
    → ToolUseInputDelta(id, json_chunk) × N
    → ToolUseEnd(id, name, input)
ToolUseStart 与 ToolUseEnd 之间可以交错其他工具调用的事件（多个并发
工具调用块）；id 相同的事件按时间顺序发出。

具体规则：
- ThinkingDelta 仅在 Anthropic 协议 + thinking 开启时出现，全部出现在
  TextDelta 之前。
- ToolUseEnd 的 input 字段已由 Provider 内部把累计的 JSON 字符串碎片
  json.loads 解析为字典；JSON 解析失败时 Provider 抛 StreamParseError。
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
class ToolUseStart:
    """流中开始一个工具调用块。

    Attributes:
        id:   协议生成的工具调用 ID（用于后续 InputDelta 的归并 + chat 层
            构造 ToolUseBlock 与回填 ToolResultBlock 时关联）。
        name: 工具名（与 ToolRegistry 中的 name 对应）。
    """

    id: str
    name: str


@dataclass(frozen=True)
class ToolUseInputDelta:
    """工具调用参数 JSON 字符串增量。

    本阶段 UI 不消费此事件（D5 决策——保留接口以支持未来"参数实时
    展示"）。chat 层在 ToolUseEnd 时直接用已 json.loads 后的 input。

    Attributes:
        id:         所属工具调用的 ID（同一 ToolUseStart.id）。
        json_chunk: 本次到达的 JSON 字符串片段。
    """

    id: str
    json_chunk: str


@dataclass(frozen=True)
class ToolUseEnd:
    """工具调用块结束。

    由 Provider 在收到协议层的"块结束"信号时（Anthropic 的
    content_block_stop / OpenAI 的 finish_reason="tool_calls"）发出，
    此时 Provider 已把累计的 JSON 字符串 json.loads 为字典。

    Attributes:
        id:    所属工具调用的 ID。
        name:  工具名（冗余传入，便于 chat 层不查 buf 直接构造 ToolUseBlock）。
        input: 已解析的参数字典。
    """

    id: str
    name: str
    input: dict


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
StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ToolUseStart
    | ToolUseInputDelta
    | ToolUseEnd
    | Usage
    | Done
)
