"""把 7 固定模块 + 环境信息 + 可选模块按顺序拼成完整 system 字符串。

spec F1 + F2 + F3 拼装顺序：
    7 固定模块（FIXED_MODULES）
    → 环境信息（build_env_section）
    → 自定义指令 (可选，本阶段不传)
    → Skill (可选)
    → 长期记忆 (可选)

模块之间用双换行 `\\n\\n` 分隔。可选模块为 None 时跳过。
"""

from pathlib import Path

from mewcode.system_prompt.env import build_env_section
from mewcode.system_prompt.modules import FIXED_MODULES


def build_system_prompt(
    cwd: Path,
    tools: list[str],
    custom_instructions: str | None = None,
    skills: list[str] | None = None,
    memory: str | None = None,
) -> str:
    """构造完整 system 字符串。

    Args:
        cwd:   工作目录绝对路径。
        tools: 已注册工具名列表。
        custom_instructions: 用户的项目级自定义指令。后续章节加载，
            本阶段调用方不传。
        skills: 已激活的 Skill 文本列表。后续章节使用。
        memory: 跨会话长期记忆文本。后续章节使用。

    Returns:
        多段拼接的 system 字符串，可直接作为 stream_chat 的 system 参数。
    """
    parts: list[str] = list(FIXED_MODULES)
    parts.append(build_env_section(cwd, tools))

    # 可选模块（本阶段三个全部为 None）
    if custom_instructions:
        parts.append(f"## 自定义指令\n{custom_instructions}")
    if skills:
        parts.append("## 已激活的 Skill\n" + "\n\n".join(skills))
    if memory:
        parts.append(f"## 长期记忆\n{memory}")

    return "\n\n".join(parts)
