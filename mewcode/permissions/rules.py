"""规则解析与匹配（spec F4 / Q4 / Q5 / D3）。

规则格式：'工具名(glob 模式)' 例如 'Bash(git *)'。

匹配语义（spec D5）：
- 整条命令 / 路径的前缀匹配（不是包含、不是任意位置）
- glob 字符 `*` 匹配任意字符（含 `/`，因为我们用 fnmatch.fnmatchcase）
- `**` 在 fnmatch 里语义等价 `*`（Python stdlib 的限制），实用够用
- `Bash(git *)` 匹配 `git status` ✅、不匹配 `cd /tmp && git status` ❌

工具名映射（spec D3）：
- 用户写 'Bash' 与内部 run 工具映射
- 大小写不敏感（Bash / bash / BASH 都识别）
"""

import fnmatch
import re
from dataclasses import dataclass


# 工具名规范化映射：YAML 中首字母大写 ↔ 内部小写 name
# 第五阶段允许 Bash 与 Run 都映射到 run 工具
# 第六阶段新增 "mcp" 虚拟工具桶：匹配所有 mcp__ 开头的 MCP 工具
TOOL_NAME_MAP: dict[str, str] = {
    "bash": "run",
    "run": "run",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "glob": "glob",
    "search": "search",
    "mcp": "mcp",  # 第六阶段：MCP 工具虚拟桶
}


@dataclass(frozen=True)
class Rule:
    """单条规则。

    Attributes:
        tool:    内部工具名（小写：run/read/write/edit/glob/search）
        pattern: 原始 glob 模式（不含工具名包装）
        raw:     原始字符串（如 'Bash(git *)'），保留供显示与去重
    """

    tool: str
    pattern: str
    raw: str

    def matches(self, tool_name: str, target: str) -> bool:
        """判断规则是否匹配指定工具调用的 target（命令字符串或路径）。

        Args:
            tool_name: 内部小写工具名（run/read/...）。
            target:    待匹配字符串：
                - run 工具：command 参数
                - read/write/edit：path 参数
                - glob/search：pattern 参数
                - mcp__* 工具：工具的全名（如 mcp__fs__read_file）

        Returns:
            True：tool 与 pattern 都匹配；False：任一不匹配。
        """
        # MCP 虚拟桶：rule.tool=="mcp" 时匹配所有 mcp__ 开头的工具
        if self.tool == "mcp":
            if not tool_name.startswith("mcp__"):
                return False
            return fnmatch.fnmatchcase(tool_name, self.pattern)
        if self.tool != tool_name:
            return False
        # fnmatchcase 完整匹配；pattern 含 * 通配
        return fnmatch.fnmatchcase(target, self.pattern)


# 规则字符串解析正则：'ToolName(<pattern>)'
_RULE_RE = re.compile(r"^\s*([A-Za-z]+)\s*\((.*)\)\s*$", re.DOTALL)


def parse_rule(raw: str) -> Rule | None:
    """解析单条规则字符串。

    Args:
        raw: 规则原文，例如 'Bash(git *)'。

    Returns:
        合法 → Rule 实例；
        非法（格式错误 / 工具名未知 / 模式空）→ None（调用方应当 warning）。
    """
    if not isinstance(raw, str):
        return None
    m = _RULE_RE.match(raw)
    if not m:
        return None
    tool_raw = m.group(1).lower()
    pattern = m.group(2).strip()
    if not pattern:
        return None
    tool = TOOL_NAME_MAP.get(tool_raw)
    if tool is None:
        return None
    return Rule(tool=tool, pattern=pattern, raw=raw.strip())


def extract_match_target(tool_name: str, params: dict) -> str | None:
    """从工具调用参数中提取规则匹配的 target 字符串。

    Args:
        tool_name: 内部小写工具名。
        params:    工具的 input 字典。

    Returns:
        目标字符串；不支持的工具返回 None（这种工具 policy 视为 ask）。
        MCP 工具（mcp__ 开头）返回工具的全名作为 target。
    """
    if tool_name.startswith("mcp__"):
        # MCP 工具：用全名作为匹配 target（rule.tool=="mcp" 时匹配全名）
        return tool_name
    if tool_name == "run":
        return params.get("command", "")
    if tool_name in ("read", "write", "edit"):
        return params.get("path", "")
    if tool_name in ("glob", "search"):
        return params.get("pattern", "")
    return None


def format_rule_for_display(tool_name: str, target: str) -> str:
    """构造规则字符串供写入 YAML 或显示给用户（spec F7 a 选项）。

    例如 ('run', 'git status') → 'Bash(git status)'。
    MCP 工具：('mcp__fs__read', 'mcp__fs__read') → 'Mcp(mcp__fs__read)'。
    """
    if tool_name.startswith("mcp__"):
        return f"Mcp({tool_name})"
    verb_map = {
        "run": "Bash",
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "glob": "Glob",
        "search": "Search",
    }
    verb = verb_map.get(tool_name, tool_name.capitalize())
    return f"{verb}({target})"
