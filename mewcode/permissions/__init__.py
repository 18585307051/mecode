"""权限系统子模块出口（spec 第五阶段）。

五层防御：
1. 黑名单（blocklist）—— 不可绕过
2. 沙箱 + TOCTOU（在 mewcode.tools.sandbox 中扩展，本模块不直接处理）
3. 可配置规则（rules + loader + policy）
4. 权限模式（policy 内部）
5. 人在回路（interactive）
"""

from mewcode.permissions.blocklist import (
    DANGEROUS_PATTERNS,
    match_blocklist,
)
from mewcode.permissions.interactive import PermissionAsker
from mewcode.permissions.loader import PermissionConfig, load_all, load_layer
from mewcode.permissions.policy import Decision, PermissionPolicy
from mewcode.permissions.rules import (
    Rule,
    extract_match_target,
    format_rule_for_display,
    parse_rule,
)

__all__ = [
    "DANGEROUS_PATTERNS",
    "Decision",
    "PermissionAsker",
    "PermissionConfig",
    "PermissionPolicy",
    "Rule",
    "extract_match_target",
    "format_rule_for_display",
    "load_all",
    "load_layer",
    "match_blocklist",
    "parse_rule",
]
