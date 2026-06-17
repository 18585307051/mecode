"""system_prompt 子模块出口。

第四阶段把单文件 system_prompt.py 升级为子模块目录。为兼容前几阶段的
import 路径（`from mewcode.system_prompt import build_system_prompt`），
这里继续暴露同名函数。

新增导出：
- build_plan_reminder：根据 plan_turn_count 选择完整或精简 reminder
- inject_into_user_text：把 reminder 拼接到 user 消息开头
"""

from mewcode.system_prompt.builder import build_system_prompt
from mewcode.system_prompt.reminders import (
    PLAN_REMINDER_FULL,
    PLAN_REMINDER_SHORT,
    build_plan_reminder,
    inject_into_user_text,
)

__all__ = [
    "PLAN_REMINDER_FULL",
    "PLAN_REMINDER_SHORT",
    "build_plan_reminder",
    "build_system_prompt",
    "inject_into_user_text",
]
