"""Agent 层事件类型。

在第二阶段 StreamEvent 之上新增 AgentEvent 层，彻底解耦 Agent Loop
编排逻辑与终端渲染：

    Provider 层：StreamEvent（TextDelta / ThinkingDelta / ToolUse* / Usage / Done）
                             ↓ chat.engine._consume_round 累积
    Agent 层：  AgentEvent（本模块）
                             ↓ Renderer.on_agent_event 订阅
    终端：      进度行 / 调用提示 / 简略反馈 / 停止原因 / 累计用量

两层事件的生命周期不同：一个 AgentEvent（如 IterationStart）对应多个
StreamEvent（一整轮的 TextDelta + ToolUse* + Done）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IterationStart:
    """一轮 Agent Loop 迭代开始。

    Renderer 输出一行进度：`── 迭代 N/M ──`

    Attributes:
        iteration:      1-based 迭代号。
        max_iterations: 迭代上限（本阶段固定 50）。
    """

    iteration: int
    max_iterations: int


@dataclass(frozen=True)
class IterationEnd:
    """一轮 Agent Loop 迭代结束（LLM 流结束 + blocks 入历史）。

    Renderer 不输出（静默），仅用于事件流完整性。
    """

    iteration: int


@dataclass(frozen=True)
class ToolBatchStart:
    """一批 tool_use 即将执行。

    Attributes:
        count:            本批 tool_use 总数。
        safe_count:       并发批数量（SAFE 只读工具）。
        dangerous_count:  串行批数量（DANGEROUS 写类工具）。
    """

    count: int
    safe_count: int
    dangerous_count: int


@dataclass(frozen=True)
class ToolCall:
    """单个工具调用前提示。

    Renderer 输出：`▸ <name>(<summary>)`

    Attributes:
        name:    工具名。
        summary: 参数概要（由 Tool.render_call_summary 生成）。
    """

    name: str
    summary: str


@dataclass(frozen=True)
class ToolResultEvent:
    """单个工具执行结果简略反馈。

    Renderer 输出：`  ✓ <name>: <summary>` 或 `  ✗ <name>: <summary>`

    Attributes:
        tool_use_id: 关联到 ToolUseBlock.id。
        name:        工具名。
        summary:     结果概要（由 Tool.render_result_summary 生成）。
        success:     执行是否成功。
    """

    tool_use_id: str
    name: str
    summary: str
    success: bool


@dataclass(frozen=True)
class Stopped:
    """Agent Loop 停止。

    Attributes:
        reason:    停止原因，取值：
            - "natural"         模型不再请求工具（自然完成）
            - "max_iterations"  达到迭代上限（软停止）
            - "user_cancel"     用户 Ctrl+C 取消
            - "unknown_tools"   连续调用未知工具
            - "error"           LLM 流出错
        iteration: 实际跑了几轮（1-based；0 表示第 1 轮就失败）。
    """

    reason: str
    iteration: int


@dataclass(frozen=True)
class UsageTotal:
    """Loop 结束的累计 token 用量。

    Renderer 输出：`↑ X tokens · ↓ Y tokens[· 思考 Z tokens] · N 轮`

    Attributes:
        input_tokens:    所有迭代的 input_tokens 累加。
        output_tokens:   所有迭代的 output_tokens 累加。
        thinking_tokens: 所有迭代的 thinking_tokens 累加；无则 None。
        iterations:      实际迭代次数。
    """

    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None
    iterations: int


# Agent 层事件的联合类型
AgentEvent = (
    IterationStart
    | IterationEnd
    | ToolBatchStart
    | ToolCall
    | ToolResultEvent
    | Stopped
    | UsageTotal
)
