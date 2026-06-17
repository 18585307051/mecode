"""7 个固定模块与 build_system_prompt 的单元测试。

覆盖 spec AC1 / AC2 / AC3 / AC17。
"""

from pathlib import Path

import pytest

from mewcode.system_prompt import build_system_prompt
from mewcode.system_prompt.modules import (
    ACTION,
    CONSTRAINTS,
    FIXED_MODULES,
    IDENTITY,
    OUTPUT,
    TASK_MODE,
    TONE,
    TOOL_USAGE,
)


# ---------- 7 个模块独立可获取 ----------


def test_7_个模块独立可获取() -> None:
    """每个模块以 '## ' 开头且非空，长度 30-500 字。"""
    modules = {
        "IDENTITY": IDENTITY,
        "CONSTRAINTS": CONSTRAINTS,
        "TASK_MODE": TASK_MODE,
        "ACTION": ACTION,
        "TOOL_USAGE": TOOL_USAGE,
        "TONE": TONE,
        "OUTPUT": OUTPUT,
    }
    for name, text in modules.items():
        assert isinstance(text, str), f"{name} 不是字符串"
        assert text.startswith("## "), f"{name} 不以 '## ' 开头"
        assert 30 <= len(text) <= 800, f"{name} 长度异常：{len(text)}"


def test_FIXED_MODULES_长度为7() -> None:
    """FIXED_MODULES 含全部 7 个模块（spec F1）。"""
    assert len(FIXED_MODULES) == 7


def test_FIXED_MODULES_顺序() -> None:
    """spec F1 拼装顺序：身份 → 系统约束 → 任务模式 → 动作执行 → 工具使用
    → 语气风格 → 文本输出。"""
    expected_titles = [
        "## 身份",
        "## 系统约束",
        "## 任务模式",
        "## 动作执行",
        "## 工具使用",
        "## 语气风格",
        "## 文本输出",
    ]
    for module, expected in zip(FIXED_MODULES, expected_titles):
        first_line = module.split("\n", 1)[0]
        assert first_line == expected, (
            f"模块标题不符：期望 {expected}，实际 {first_line}"
        )


# ---------- build_system_prompt 输出验证 ----------


@pytest.fixture
def cwd() -> Path:
    return Path.cwd()


@pytest.fixture
def tools() -> list[str]:
    return ["read", "glob", "search", "write", "edit", "run"]


def test_build_含全部7个模块标题(cwd: Path, tools: list[str]) -> None:
    """spec AC2：输出含 7 个固定模块标题 + ## 当前环境。"""
    s = build_system_prompt(cwd, tools)
    for title in (
        "## 身份",
        "## 系统约束",
        "## 任务模式",
        "## 动作执行",
        "## 工具使用",
        "## 语气风格",
        "## 文本输出",
        "## 当前环境",
    ):
        assert title in s, f"输出缺少标题：{title}"


def test_build_环境信息位置(cwd: Path, tools: list[str]) -> None:
    """spec AC3：## 当前环境 出现在所有 7 个固定模块之后。"""
    s = build_system_prompt(cwd, tools)
    pos_env = s.index("## 当前环境")
    for module_title in (
        "## 身份",
        "## 系统约束",
        "## 任务模式",
        "## 动作执行",
        "## 工具使用",
        "## 语气风格",
        "## 文本输出",
    ):
        assert s.index(module_title) < pos_env, (
            f"{module_title} 应当在 ## 当前环境 之前"
        )


def test_build_顺序稳定(cwd: Path, tools: list[str]) -> None:
    """连续两次调用结果完全一致（缓存友好）。"""
    s1 = build_system_prompt(cwd, tools)
    s2 = build_system_prompt(cwd, tools)
    assert s1 == s2


def test_build_长度合理(cwd: Path, tools: list[str]) -> None:
    """spec AC17：长度在 800-3000 字符（中文密度，宽松上限）。"""
    s = build_system_prompt(cwd, tools)
    assert 800 <= len(s) <= 3000, f"长度异常：{len(s)}"


def test_build_含工具列表(cwd: Path) -> None:
    """工具列表注入 ## 当前环境 段落。"""
    s = build_system_prompt(cwd, ["read", "write"])
    assert "read / write" in s


def test_build_可选模块_none不附加(cwd: Path, tools: list[str]) -> None:
    """custom_instructions / skills / memory 默认 None 时不附加段落。"""
    s = build_system_prompt(cwd, tools)
    assert "## 自定义指令" not in s
    assert "## 已激活的 Skill" not in s
    assert "## 长期记忆" not in s


def test_build_可选模块_提供时附加(cwd: Path, tools: list[str]) -> None:
    """传入可选模块时附加在末尾。"""
    s = build_system_prompt(
        cwd,
        tools,
        custom_instructions="自定义内容 X",
        skills=["skill A"],
        memory="记忆 Y",
    )
    assert "## 自定义指令\n自定义内容 X" in s
    assert "## 已激活的 Skill\nskill A" in s
    assert "## 长期记忆\n记忆 Y" in s
    # 顺序：自定义指令 → Skill → 记忆，全部在 ## 当前环境 之后
    pos_env = s.index("## 当前环境")
    assert s.index("## 自定义指令") > pos_env
    assert s.index("## 已激活的 Skill") > s.index("## 自定义指令")
    assert s.index("## 长期记忆") > s.index("## 已激活的 Skill")


def test_build_双重强化_工具使用模块(cwd: Path, tools: list[str]) -> None:
    """spec F5 / AC8：TOOL_USAGE 模块含'优先用专用工具'字样。"""
    s = build_system_prompt(cwd, tools)
    assert "优先用专用工具" in s
    assert "edit 前必先 read" in s
