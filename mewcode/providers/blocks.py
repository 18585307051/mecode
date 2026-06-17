"""消息内容块体系。

本模块定义 spec F16 历史结构升级的核心：
Message.content 从 str 升级为 list[ContentBlock]，每个块对应模型协议中
的一个 content block，覆盖普通文本、思考、工具调用与工具结果四种形态。

四种块的触发场景与所属角色：

| 块类型           | 出现位置                       | 协议   |
|------------------|--------------------------------|--------|
| TextBlock        | assistant 或 user 消息         | 通用    |
| ThinkingBlock    | assistant 消息（thinking 开启）| 仅 Anthropic |
| ToolUseBlock     | assistant 消息                 | 通用    |
| ToolResultBlock  | user 消息（工具回填）          | 通用    |

设计规则：
- 全部使用 frozen dataclass，保证消息历史中的块不可变；任何"修改历史"
  的操作都应通过新建 Message 实现。
- ContentBlock 是上述四个类型的联合（PEP 604），便于使用 isinstance 与
  match-case 派发。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextBlock:
    """assistant 或 user 消息中的纯文本块。

    Attributes:
        text: 文本内容；空字符串合法（占位用）。
    """

    text: str


@dataclass(frozen=True)
class ThinkingBlock:
    """assistant 消息中的思考块。

    仅在 Anthropic 协议 + thinking 开启时产生。Anthropic API 在多轮请求
    中要求把已生成的 thinking 块原样回传给后端以维持上下文一致性，
    signature 字段由后端附带（部分 API 版本可能为空）。

    Attributes:
        text:      思考内容文本。
        signature: 后端附带的签名字段，回传时如实带上。
    """

    text: str
    signature: str = ""


@dataclass(frozen=True)
class ToolUseBlock:
    """assistant 发起的工具调用。

    Attributes:
        id:    协议生成的工具调用 ID，用于关联回填的 tool_result。
        name:  工具名（与 ToolRegistry 中的 name 对应）。
        input: 已 json.loads 的参数字典；构造前由 Provider 把 SSE 中的
            JSON 字符串碎片拼接并解析。
    """

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResultBlock:
    """user 消息中的工具结果块（回填给模型）。

    Attributes:
        tool_use_id: 关联到上一条 assistant 消息的某个 ToolUseBlock.id。
        content:     面向模型的文本（成功输出或错误描述均放在此处）。
        is_error:    True 表示工具执行失败；模型可据此调整下一步行为。
    """

    tool_use_id: str
    content: str
    is_error: bool = False


# 内容块的联合类型——整个消息历史的最小可组合单元。
ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock
