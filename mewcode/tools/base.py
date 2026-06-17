"""Tool 抽象基类、ToolResult、DangerLevel。

spec F1 / F3：每个具体工具继承 Tool，对外暴露统一契约：
- name:               唯一名称（与协议层 tool_use.name 对齐）
- description:        面向模型的简要描述
- parameters_schema:  参数 JSON Schema 定义（dict 形式手写）
- danger_level:       SAFE 自动执行；DANGEROUS 执行前需用户确认
- execute:            异步执行入口，返回结构化 ToolResult

Tool 抽象与具体 wire protocol 完全解耦——同一组工具同时供 Anthropic
与 OpenAI 协议使用，由 ToolRegistry 在序列化时按目标协议格式输出元
信息。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅类型检查时引入，避免循环依赖（sandbox.py 不依赖 base.py）
    from mewcode.tools.sandbox import Sandbox


@dataclass(frozen=True)
class ToolResult:
    """工具执行返回的结构化结果。

    Attributes:
        success:        执行是否成功（语义上"工具达成预期效果"）。
            run 工具下 exit_code != 0 时也属于 success=False，因为
            模型的"命令是否成功完成"语义包含返回码。
        text:           面向模型的文本输出（成功输出或错误描述均放此处）。
        error_category: 失败时的错误类别（用于 UI 简略反馈展示）；
            成功时为 None。
    """

    success: bool
    text: str
    error_category: str | None = None


class DangerLevel:
    """工具危险等级常量。

    SAFE 工具自动执行，DANGEROUS 工具执行前由 chat 层调用 Confirmer.ask
    征求用户 y/N 确认。spec Q3 决定本阶段不提供"全部允许"开关，每次
    DANGEROUS 调用都要确认。
    """

    SAFE = "safe"
    DANGEROUS = "dangerous"


# 调用前 UI 提示中"参数概要"的最大显示长度（spec F19）
_CALL_SUMMARY_MAX = 80


class Tool(ABC):
    """所有具体工具的抽象基类。

    类属性占位由子类覆盖；execute 抽象方法必须实现；三个 render_*
    方法提供合理默认实现，子类按需覆盖以提供更友好的 UI。
    """

    # 子类覆盖：唯一名称
    name: str = ""
    # 子类覆盖：面向模型的简要描述
    description: str = ""
    # 子类覆盖：JSON Schema dict（手写，不引入 pydantic / jsonschema）
    parameters_schema: dict = {}
    # 子类覆盖：危险等级
    danger_level: str = DangerLevel.SAFE

    @abstractmethod
    async def execute(self, params: dict, sandbox: "Sandbox") -> ToolResult:
        """执行工具。

        子类实现要求：
        - 任何异常（含 ToolError 子类、I/O 异常、超时等）必须在内部
          捕获并转换为 `ToolResult(success=False, ...)`，绝不向上抛
          导致进程崩溃（spec F3）。
        - 路径相关参数应通过 sandbox.resolve(path) 做边界校验。
        - 若需要超时，由子类自行用 asyncio.wait_for 包裹核心逻辑
          （read/write/edit/glob/search 30s；run 60s）。

        Args:
            params:  已经协议层 json.loads 的参数字典；工具内部按需
                做类型与必填校验。
            sandbox: 工作目录沙盒，提供路径校验与命令工作目录。

        Returns:
            结构化 ToolResult。
        """
        ...

    # ---------- 渲染辅助方法（子类按需覆盖） ----------

    def render_call_summary(self, params: dict) -> str:
        """生成 "▸ <name>(<concise>)" 中括号内的参数概要字符串。

        默认实现：取参数字典的 key=value 拼接，超长时尾部加省略号。
        子类可覆盖以突出关键参数（如 read 突出 path、search 突出 pattern）。
        """
        parts = [f"{k}={v!r}" for k, v in params.items()]
        s = ", ".join(parts)
        if len(s) > _CALL_SUMMARY_MAX:
            s = s[: _CALL_SUMMARY_MAX - 3] + "..."
        return s

    def render_confirm_detail(self, params: dict) -> str:
        """生成 DANGEROUS 工具确认提示中展示给用户判断 y/N 的详细信息。

        默认实现返回 params 的可读 repr。子类应当覆盖：
        - WriteTool：路径 + 内容前若干行
        - EditTool：路径 + 基于 difflib.unified_diff 的 diff 摘要
        - RunTool：完整命令字符串
        SAFE 工具不会触发此方法的调用。
        """
        return repr(params)

    def render_result_summary(self, result: ToolResult) -> str:
        """生成调用后一行简略反馈字符串。

        默认实现：成功显示 "成功"，失败显示 "失败：<error_category>"。
        子类按工具语义定制：
        - read       → "读取 N 行"
        - glob/search → "匹配 N 项"
        - run        → "退出码 N"
        - write/edit → "成功" 或 错误类别
        """
        if result.success:
            return "成功"
        cat = result.error_category or "未知错误"
        return f"失败：{cat}"
