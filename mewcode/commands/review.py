"""PROMPT 类命令实现：/review 自检（spec 第十阶段 F12）。

PROMPT 类命令的执行规约：
- handler 不向 LLM 发请求；
- handler 把构造好的"伪用户输入"放在 CommandResult.prompt_text；
- REPL 主循环捕获 prompt_text 后调 run_turn，与正常对话完全同路径。
"""

from __future__ import annotations

from mewcode.commands.registry import CommandContext, CommandResult


# 预设自检 prompt（spec F12）
_REVIEW_PROMPT = """请回顾本轮对话里你做的所有改动和操作，逐项检查：
1. 修改是否完成了用户要求的目标？有没有偏题？
2. 改动是否引入了潜在 bug、边界情况遗漏、错误处理缺失？
3. 是否有应该写但没写的测试？
4. 代码风格、命名、注释是否与项目既有风格一致？
5. 是否破坏了现有功能、接口契约或测试？

按上面五点逐条给出结论；如果某点没问题，明确说"无"。最后用一句话总结整体风险等级（低 / 中 / 高）和建议的下一步。"""


async def _handle_review(ctx: CommandContext) -> CommandResult:
    """/review [侧重点]

    - 当前 session.messages 为空时，**不**调 LLM，仅打印提示并返回空结果。
    - 否则把预设 prompt 放进 CommandResult.prompt_text，交给 REPL 注入对话。
    - 有用户参数时，附加到末尾作为额外重点。
    """
    if not getattr(ctx.session, "messages", None):
        ctx.renderer.print_info(
            "当前会话尚无内容可回顾。先发起一些对话再用 /review。"
        )
        return CommandResult()

    extra = " ".join(ctx.args).strip()
    text = _REVIEW_PROMPT
    if extra:
        text += f"\n\n本次额外重点关注：{extra}"
    return CommandResult(prompt_text=text)
