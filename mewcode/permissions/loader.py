"""三层 YAML 文件加载与合并（spec F5 / Q11）。

层级：本地级 → 项目级 → 用户级（越靠近项目优先级越高）。

合并规则：
- mode：本地 > 项目 > 用户 > "default"（取最高优先级有值的）
- allow / deny：本地 + 项目 + 用户 顺序拼接（本地在前先匹配生效）

错误处理：
- 缺失文件 → 视为空，不报错（spec N7：默认无文件最严格）
- YAML 解析失败 → 打印 warning，视该层为空，继续启动（spec N10）
- 非法规则字符串 → 跳过 + warning
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mewcode.permissions.rules import Rule, parse_rule


@dataclass
class PermissionConfig:
    """三层合并后的最终规则集。"""

    mode: str = "default"
    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)


def load_layer(path: Path) -> tuple[str | None, list[Rule], list[Rule]]:
    """加载单个 YAML 文件。

    Args:
        path: YAML 文件绝对路径。

    Returns:
        (mode, allow_rules, deny_rules)
        - 文件不存在 → (None, [], [])
        - 解析失败 → 打印 warning，返回 (None, [], [])
        - 非法规则 → 跳过 + warning，正常规则正常返回
    """
    if not path.exists():
        return None, [], []

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"⚠️ 权限规则文件 {path} 解析失败：{e}")
        return None, [], []

    if not isinstance(data, dict):
        print(f"⚠️ 权限规则文件 {path} 顶层不是 dict，已忽略")
        return None, [], []

    mode = data.get("mode")
    if mode is not None and mode not in ("strict", "default", "yolo"):
        print(f"⚠️ 权限规则文件 {path} 的 mode={mode!r} 非法，已忽略")
        mode = None

    allow_raw = data.get("allow", []) or []
    deny_raw = data.get("deny", []) or []

    if not isinstance(allow_raw, list):
        print(f"⚠️ 权限规则文件 {path} 的 allow 不是列表，已忽略")
        allow_raw = []
    if not isinstance(deny_raw, list):
        print(f"⚠️ 权限规则文件 {path} 的 deny 不是列表，已忽略")
        deny_raw = []

    allow: list[Rule] = []
    for raw in allow_raw:
        rule = parse_rule(str(raw))
        if rule is not None:
            allow.append(rule)
        else:
            print(f"⚠️ 跳过非法 allow 规则：{raw!r}（来自 {path}）")

    deny: list[Rule] = []
    for raw in deny_raw:
        rule = parse_rule(str(raw))
        if rule is not None:
            deny.append(rule)
        else:
            print(f"⚠️ 跳过非法 deny 规则：{raw!r}（来自 {path}）")

    return mode, allow, deny


def load_all(cwd: Path) -> PermissionConfig:
    """加载三层文件并合并（spec F5 / D6）。

    路径：
    - 用户级：~/.mewcode/permissions.yaml
    - 项目级：<cwd>/.mewcode/permissions.yaml
    - 本地级：<cwd>/.mewcode/permissions.local.yaml

    优先级：本地 > 项目 > 用户。

    Args:
        cwd: 项目根目录。

    Returns:
        PermissionConfig，含合并后的 mode 与 allow/deny 列表。
    """
    user_path = Path.home() / ".mewcode" / "permissions.yaml"
    project_path = cwd / ".mewcode" / "permissions.yaml"
    local_path = cwd / ".mewcode" / "permissions.local.yaml"

    user_mode, user_allow, user_deny = load_layer(user_path)
    project_mode, project_allow, project_deny = load_layer(project_path)
    local_mode, local_allow, local_deny = load_layer(local_path)

    # mode：本地 > 项目 > 用户 > "default"
    mode = local_mode or project_mode or user_mode or "default"

    # allow / deny：高优先级在前，先匹配先生效
    allow = local_allow + project_allow + user_allow
    deny = local_deny + project_deny + user_deny

    return PermissionConfig(mode=mode, allow=allow, deny=deny)
