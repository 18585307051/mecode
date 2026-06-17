"""<system-reminder> 系统级补充消息（spec F6 / F7）。

定义一种特殊形式的"伪 user 消息"：内容用 <system-reminder> 标签包裹。
模型行业惯例：识别此标签为系统补充信息，不当作用户输入回复。

注入位置（spec D4）：
    拼接到当前 turn 的 user 消息开头，与原 user 文本合并为同一个
    TextBlock，存在同一条 user message 中——不破坏 messages 数组的角色
    交替结构，不增加 token 开销。

注入节奏（spec F7）：
    Plan Mode 下：
        - 第 1 / 6 / 11 / 16 / ... 轮 → 完整 reminder（详细规则）
        - 其他轮 → 精简 reminder（仅状态提示）
    Do Mode：
        不注入。
"""


PLAN_REMINDER_FULL = (
    "<system-reminder>\n"
    "[Plan Mode] 当前处于 Plan Mode（计划模式）。仅可使用只读工具"
    "（read / glob / search）。不要尝试修改文件、执行命令；如需写操作，"
    "请告诉用户切换到 /do 模式后再试。Plan Mode 的输出应当是一份可执行"
    "的方案文本，明确说明每一步要做什么、要改哪些文件。\n"
    "</system-reminder>"
)


PLAN_REMINDER_SHORT = (
    "<system-reminder>[Plan Mode 仍然激活]</system-reminder>"
)


def build_plan_reminder(plan_turn_count: int) -> str:
    """根据 plan_turn_count 选择完整或精简 reminder。

    Args:
        plan_turn_count: 自切换到 plan 后的第几轮（1-based）。
            <= 0 表示当前不在 Plan Mode。

    Returns:
        含 <system-reminder> 标签的字符串；不需要注入时返回 ""。
    """
    if plan_turn_count <= 0:
        return ""
    # 第 1 轮，以及之后每隔 5 轮（即 6 / 11 / 16 / ...）
    if plan_turn_count == 1 or (plan_turn_count - 1) % 5 == 0:
        return PLAN_REMINDER_FULL
    return PLAN_REMINDER_SHORT


def inject_into_user_text(reminder: str, user_text: str) -> str:
    """把 reminder 拼接到 user 消息开头（spec D4）。

    Args:
        reminder:  build_plan_reminder 的结果，可能为空。
        user_text: 用户原始输入。

    Returns:
        若 reminder 非空：reminder + "\\n\\n" + user_text
        若 reminder 为空：user_text 原样返回
    """
    if not reminder:
        return user_text
    return f"{reminder}\n\n{user_text}"
