# MewCode 第五阶段 Plan

> 基于已批准的 `docs/06/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第四阶段的兼容矩阵。

## 1. 架构概览

```
┌────────────────────────────────────────────────────────────────────┐
│  main.py 启动                                                      │
│   load permissions YAML (3 layers) → PermissionPolicy 实例          │
└────────────────┬───────────────────────────────────────────────────┘
                 │ 注入
                 ▼
        ┌─────────────────────────────┐
        │ chat.engine                  │
        │   _execute_tool_batch        │
        │     ↓ 工具执行前调           │
        │   policy.check(tool, params) │
        │     → Decision(allow/deny/ask)│
        └─────────┬───────────────────┘
                  │
                  ▼
        ┌─────────────────────────────────────────────────────┐
        │ permissions/policy.py                                │
        │   PermissionPolicy.check(tool_name, params)          │
        │     ↓                                                 │
        │     1. blocklist.match(...)  ← 不可绕过              │
        │     2. sandbox 校验（路径相关）  ← 不可绕过           │
        │     3. session_deny → deny                           │
        │     4. session_allow → allow                          │
        │     5. local YAML deny/allow                          │
        │     6. project YAML deny/allow                        │
        │     7. user YAML deny/allow                           │
        │     8. mode (yolo/default/strict)                    │
        │     9. interactive (ask user)                         │
        │     → Decision                                        │
        └─────────┬───────────────────────────────────────────┘
                  │ ask 时调
                  ▼
        ┌─────────────────────────────┐
        │ permissions/interactive.py   │
        │   prompt_user(...) → choice  │
        │     y / s / a / N            │
        │     a → 写入 local YAML      │
        └─────────────────────────────┘

┌──────────────────┐   ┌─────────────────────┐   ┌─────────────────┐
│ permissions/     │   │ tools/sandbox.py     │   │ commands/       │
│  blocklist.py    │   │   + safe_open()      │   │   + permissions │
│  rules.py        │   │     防 TOCTOU         │   │     子命令族     │
│  loader.py       │   └─────────────────────┘   └─────────────────┘
│  policy.py       │
│  interactive.py  │
└──────────────────┘
```

### 层次关系

| 层 | 变化 |
|----|------|
| permissions/ (新模块) | 新增：blocklist/rules/loader/policy/interactive 5 文件 |
| chat.engine | _execute_tool_batch 加 policy.check 调用 |
| chat.session | 新增 permission_session_allow/deny + mode_override 字段 |
| tools.sandbox | 新增 safe_open 方法 |
| tools.read/write/edit | 改用 sandbox.safe_open |
| commands.builtin | 新增 _handle_permissions* 子命令 handler |
| main.py | 启动加载 PermissionPolicy + 注入 chat 层 |
| repl.main_loop | 透传 policy 给 run_turn |
| providers / renderer | 不变 |

## 2. 模块设计

### 2.1 permissions/blocklist.py

```python
"""黑名单：硬拦截高危命令（spec F2 / D1）。

不可被配置或 yolo 模式绕过。仅作用于 run 工具的 command 参数。
"""

import re

DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"rm\s+(?:-[a-zA-Z]*r[a-zA-Z]*|--recursive)\s+(?:-[a-zA-Z]*f[a-zA-Z]*|--force)?\s*(?:/|~|\$HOME|/\*)\s*$", re.IGNORECASE),
    re.compile(r"rm\s+(?:-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*)\s*(?:/|~|\$HOME)\s*$", re.IGNORECASE),
    re.compile(r"\bmkfs(?:\.\w+)?\s+", re.IGNORECASE),
    re.compile(r"\bdd\s+.*\bof=/dev/(?:sd|nvme|hd|xvd)", re.IGNORECASE),
    re.compile(r">\s*/dev/(?:sd|nvme|hd|xvd)"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # fork bomb
    re.compile(r"\b(?:curl|wget)\s+\S+.*\|\s*(?:sh|bash|zsh)\b", re.IGNORECASE),
    re.compile(r"\bformat\s+[A-Za-z]:", re.IGNORECASE),
    re.compile(r"\brmdir\s+/[sS]\s+/[qQ]\s+[A-Za-z]:", re.IGNORECASE),
]


def match_blocklist(command: str) -> str | None:
    """检查命令是否触发黑名单。命中返回匹配的危险模式描述，未命中返回 None。"""
    for pat in DANGEROUS_PATTERNS:
        if pat.search(command):
            return pat.pattern
    return None
```

### 2.2 permissions/rules.py

```python
"""规则解析与匹配（spec F4 / Q4 / Q5）。

规则格式：'工具名(glob 模式)' 例如 'Bash(git *)'。
匹配语义：整条命令 / 路径的前缀匹配，glob `*` 匹配任意非分隔符，
`**` 匹配任意（含分隔符）。
"""

import fnmatch
import re
from dataclasses import dataclass


# 工具名规范化映射：YAML 中首字母大写 ↔ 内部小写 name
TOOL_NAME_MAP = {
    "bash": "run",
    "run": "run",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "glob": "glob",
    "search": "search",
}


@dataclass(frozen=True)
class Rule:
    """单条规则。"""
    tool: str       # 内部工具名（小写：run/read/...）
    pattern: str    # 原始 glob 模式
    raw: str        # 原始字符串（"Bash(git *)"），保留用于显示

    def matches(self, tool_name: str, target: str) -> bool:
        """判断规则是否匹配指定工具调用的 target（命令字符串或路径）。"""
        if self.tool != tool_name:
            return False
        # glob 前缀匹配：把 pattern 当作 fnmatch 模式
        # fnmatch 是完整匹配，所以 target 必须能被 pattern 完整覆盖
        # 使用方式：把 target 看成 path-like，pattern 含通配
        return fnmatch.fnmatchcase(target, self.pattern)


_RULE_RE = re.compile(r"^([A-Za-z]+)\((.*)\)$", re.DOTALL)


def parse_rule(raw: str) -> Rule | None:
    """解析单条规则字符串，非法格式返回 None（调用方 warning）。"""
    m = _RULE_RE.match(raw.strip())
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

    - run: command 参数
    - read/write/edit: path 参数
    - glob: pattern 参数
    - search: pattern 参数
    """
    if tool_name == "run":
        return params.get("command", "")
    if tool_name in ("read", "write", "edit"):
        return params.get("path", "")
    if tool_name in ("glob", "search"):
        return params.get("pattern", "")
    return None
```

### 2.3 permissions/loader.py

```python
"""三层 YAML 文件加载与合并（spec F5）。

层级：本地 → 项目 → 用户（越靠近项目优先级越高）。
缺失文件视为空规则；YAML 解析失败给 warning 但不阻塞。
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
    """加载单个 YAML 文件，返回 (mode, allow_rules, deny_rules)。

    文件不存在 → 返回 (None, [], [])
    解析失败 → 打印 warning，返回 (None, [], [])
    """
    if not path.exists():
        return None, [], []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"⚠️ 权限规则文件 {path} 解析失败：{e}")
        return None, [], []

    mode = data.get("mode")
    allow_raw = data.get("allow", []) or []
    deny_raw = data.get("deny", []) or []

    allow = []
    for raw in allow_raw:
        rule = parse_rule(str(raw))
        if rule is not None:
            allow.append(rule)
        else:
            print(f"⚠️ 跳过非法规则：{raw}（来自 {path}）")

    deny = []
    for raw in deny_raw:
        rule = parse_rule(str(raw))
        if rule is not None:
            deny.append(rule)
        else:
            print(f"⚠️ 跳过非法规则：{raw}（来自 {path}）")

    return mode, allow, deny


def load_all(cwd: Path) -> PermissionConfig:
    """加载三层文件并合并（spec F5 / Q11）。"""
    user_path = Path.home() / ".mewcode" / "permissions.yaml"
    project_path = cwd / ".mewcode" / "permissions.yaml"
    local_path = cwd / ".mewcode" / "permissions.local.yaml"

    user_mode, user_allow, user_deny = load_layer(user_path)
    project_mode, project_allow, project_deny = load_layer(project_path)
    local_mode, local_allow, local_deny = load_layer(local_path)

    # mode：本地 > 项目 > 用户 > "default"
    mode = local_mode or project_mode or user_mode or "default"

    # allow / deny：本地在前，依次拼接（高优先级在前，先匹配先生效）
    allow = local_allow + project_allow + user_allow
    deny = local_deny + project_deny + user_deny

    return PermissionConfig(mode=mode, allow=allow, deny=deny)
```

### 2.4 permissions/policy.py

```python
"""综合判定五层防御链（spec F1 / F12）。"""

from dataclasses import dataclass
from pathlib import Path

from mewcode.permissions.blocklist import match_blocklist
from mewcode.permissions.loader import PermissionConfig, load_all
from mewcode.permissions.rules import Rule, extract_match_target


@dataclass(frozen=True)
class Decision:
    """权限决策结果。"""
    action: str      # "allow" / "deny" / "ask"
    reason: str      # 中文原因（用于 ToolResult.text）
    error_category: str | None = None  # "黑名单拦截" / "权限拒绝" / None


class PermissionPolicy:
    """综合判定。

    五层优先级（从高到低）：
    1. 黑名单（不可绕过）
    2. 沙箱（路径越界 → deny；本类不直接处理沙箱，由调用方先调 sandbox.resolve）
    3. 会话级 deny → 4. 会话级 allow
    5. 本地级 deny → 6. 本地级 allow
    7. 项目级 deny → 8. 项目级 allow
    9. 用户级 deny → 10. 用户级 allow
    11. mode (yolo / default / strict)
    12. ask 用户
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._config = load_all(cwd)
        # 会话级（运行时通过 /permissions allow/deny 与 s 选项添加）
        self._session_allow: list[Rule] = []
        self._session_deny: list[Rule] = []
        self._mode_override: str | None = None

    @property
    def mode(self) -> str:
        return self._mode_override or self._config.mode

    def reload(self) -> None:
        """重新加载三层 YAML（清空 session 级）。"""
        self._config = load_all(self._cwd)
        self._session_allow.clear()
        self._session_deny.clear()
        self._mode_override = None

    def add_session_allow(self, rule: Rule) -> None:
        self._session_allow.append(rule)

    def add_session_deny(self, rule: Rule) -> None:
        self._session_deny.append(rule)

    def set_mode_override(self, mode: str) -> None:
        if mode not in ("strict", "default", "yolo"):
            raise ValueError(f"未知模式：{mode}")
        self._mode_override = mode

    def check(self, tool_name: str, params: dict) -> Decision:
        """五层防御主入口（spec F1）。沙箱由调用方先调 sandbox 校验。"""
        # Layer 1: 黑名单（仅 run 工具）
        if tool_name == "run":
            command = params.get("command", "")
            hit = match_blocklist(command)
            if hit:
                return Decision(
                    action="deny",
                    reason=(
                        f"黑名单拦截：命令 {command!r} 匹配高危模式 {hit!r}。"
                        "此层不可通过权限规则或 yolo 模式放行。请使用更精确的命令。"
                    ),
                    error_category="黑名单拦截",
                )

        # Layer 2: 沙箱由调用方处理（在 chat.engine 调 policy 之前先做 sandbox 校验）

        # Layer 3-10: 规则匹配
        target = extract_match_target(tool_name, params)
        if target is not None:
            # 按优先级遍历：会话 → 本地 → 项目 → 用户
            # session_deny 与 session_allow 都比文件级高
            if any(r.matches(tool_name, target) for r in self._session_deny):
                return self._build_deny(tool_name, target, "会话级 deny")
            if any(r.matches(tool_name, target) for r in self._session_allow):
                return Decision("allow", reason="会话级 allow 命中")

            # 文件级（已按本地→项目→用户拼接，按出现顺序匹配）
            if any(r.matches(tool_name, target) for r in self._config.deny):
                return self._build_deny(tool_name, target, "deny 规则命中")
            if any(r.matches(tool_name, target) for r in self._config.allow):
                return Decision("allow", reason="allow 规则命中")

        # Layer 11: 权限模式
        if self.mode == "yolo":
            return Decision("allow", reason="yolo 模式放行")

        # Layer 12: ask 用户（default 与 strict 都走此路径，UI 区别在交互层）
        return Decision("ask", reason="未匹配规则")

    def _build_deny(self, tool_name: str, target: str, reason: str) -> Decision:
        return Decision(
            action="deny",
            reason=(
                f"权限拒绝：{tool_name}({target!r}) {reason}。"
                f"如需允许此操作，请告诉用户运行 "
                f"/permissions allow \"...(...) \" 添加规则，"
                f"或临时切换到 yolo 模式。"
            ),
            error_category="权限拒绝",
        )
```

### 2.5 permissions/interactive.py

```python
"""人在回路 UI（spec F7）。"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from prompt_toolkit import PromptSession

from mewcode.permissions.rules import Rule, parse_rule


class PermissionAsker:
    """询问用户的封装。复用全局 PromptSession 以与主 REPL 一致。"""

    def __init__(self) -> None:
        self._pt: PromptSession | None = None

    def _session(self) -> PromptSession:
        if self._pt is None:
            self._pt = PromptSession()
        return self._pt

    async def ask(self, tool_name: str, target: str, cwd: Path) -> str:
        """问 y / s / a / n 四选。

        返回字面量字符串：
        - "once"     → y
        - "session"  → s
        - "forever"  → a（已写入 local YAML）
        - "deny"     → n / 回车 / EOF

        Raises:
            ConfirmCancelled: 用户按 Ctrl+C
        """
        # 显示工具调用与询问
        print(f"\n● {self._format_call(tool_name, target)}")
        print("未匹配规则，是否允许？")
        print("  y - 仅本次")
        print("  s - 本会话")
        print("  a - 永久（写入 permissions.local.yaml）")
        print("  n - 拒绝")

        try:
            answer = await self._session().prompt_async("请选择 [y/s/a/N]: ")
        except KeyboardInterrupt:
            from mewcode.tools.confirmer import ConfirmCancelled
            raise ConfirmCancelled() from None
        except EOFError:
            return "deny"

        ans = answer.strip().lower()
        if ans in ("y", "yes"):
            return "once"
        if ans == "s":
            return "session"
        if ans == "a":
            self._write_to_local_yaml(tool_name, target, cwd)
            return "forever"
        return "deny"

    def _format_call(self, tool_name: str, target: str) -> str:
        verb_map = {
            "run": f"Bash {target}",
            "read": f"Read {target}",
            "write": f"Wrote {target}",
            "edit": f"Edit {target}",
            "glob": f"Glob {target}",
            "search": f"Search {target}",
        }
        return verb_map.get(tool_name, f"{tool_name} {target}")

    def _write_to_local_yaml(self, tool_name: str, target: str, cwd: Path) -> None:
        """把规则追加到 .mewcode/permissions.local.yaml 的 allow 列表。"""
        path = cwd / ".mewcode" / "permissions.local.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        # 加载现有内容（不存在则创建空结构）
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except yaml.YAMLError:
                data = {}
        else:
            data = {}

        if "allow" not in data or not isinstance(data.get("allow"), list):
            data["allow"] = []

        # 构造规则字符串（首字母大写工具名）
        verb_map = {
            "run": "Bash", "read": "Read", "write": "Write",
            "edit": "Edit", "glob": "Glob", "search": "Search",
        }
        rule_str = f"{verb_map.get(tool_name, tool_name)}({target})"

        # 已存在则不重复添加
        if rule_str not in data["allow"]:
            data["allow"].append(rule_str)

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
```

### 2.6 chat.engine 集成

在 `_execute_tool_batch` 中工具调用前调 policy.check：

```python
# 伪代码（在每个工具的执行前插入）
async def _run_with_permission(tool, tu, sandbox, policy, asker, session):
    # 1. policy.check
    decision = policy.check(tool.name, tu.input)

    if decision.action == "deny":
        return ToolResultBlock(
            tool_use_id=tu.id,
            content=decision.reason,
            is_error=True,
        )

    if decision.action == "ask":
        choice = await asker.ask(tool.name, _extract_target(tool, tu), sandbox.cwd)
        if choice == "deny":
            return ToolResultBlock(
                tool_use_id=tu.id,
                content="用户拒绝执行此工具",
                is_error=True,
            )
        if choice == "session":
            # 添加会话级 allow
            target = _extract_target(tool, tu)
            policy.add_session_allow(_rule_for(tool.name, target))
        # once / forever / session 都通过 → 继续执行

    # 2. 实际执行（保留第三阶段的 DangerLevel.DANGEROUS confirmer 逻辑）
    return await tool.execute(tu.input, sandbox)
```

### 2.7 tools/sandbox.py 增加 safe_open

```python
import os
from contextlib import contextmanager

from mewcode.tools.errors import PathOutOfSandboxError, ToolError


class PathRaceConditionError(ToolError):
    """TOCTOU 竞态：文件 open 后路径被换成符号链接（spec F3 / Q9）。"""
    category = "TOCTOU 竞态"


@dataclass(frozen=True)
class Sandbox:
    cwd: Path

    def resolve(self, raw_path: str) -> Path:
        # 继承现有实现 ...

    @contextmanager
    def safe_open(self, raw_path: str, mode: str = "r", encoding: str = "utf-8"):
        """以原子方式安全打开文件，防 TOCTOU（spec F3）。

        步骤：
        1. resolve raw_path → 校验在 cwd 内
        2. open 文件
        3. 对 fd 调 os.fstat → 取 (inode, dev)
        4. 对 resolved 路径调 os.lstat → 取 (inode, dev)
        5. 比对：不一致说明 open 后路径被换成 symlink，抛 PathRaceConditionError

        Yields:
            file 对象（with 语句自动 close）
        """
        resolved = self.resolve(raw_path)

        # binary 模式不指定 encoding
        open_kwargs = {} if "b" in mode else {"encoding": encoding}

        f = open(resolved, mode, **open_kwargs)
        try:
            try:
                fd_stat = os.fstat(f.fileno())
                ln_stat = os.lstat(resolved)
                if (fd_stat.st_ino, fd_stat.st_dev) != (ln_stat.st_ino, ln_stat.st_dev):
                    raise PathRaceConditionError(
                        f"TOCTOU 竞态：文件 {raw_path} 在 open 后被换成符号链接"
                    )
            except OSError as e:
                # Windows 某些情况下 fstat/lstat 可能 raise，宽容处理
                # 但若是 PathRaceConditionError 要往上抛
                if isinstance(e, PathRaceConditionError):
                    raise
                # 其他 OSError 不当作竞态
            yield f
        finally:
            f.close()
```

### 2.8 chat.session 字段扩展

```python
@dataclass
class Session:
    ...
    # spec F10 第五阶段
    permission_session_allow: list[str] = field(default_factory=list)
    permission_session_deny: list[str] = field(default_factory=list)
    permission_mode_override: str | None = None
```

clear() 与 switch_provider() 时重置。

注：实际权限状态存在 `PermissionPolicy` 实例里（不是 Session），但
session 字段保留是为了 /clear 时让 policy 也清空。chat.engine 在
/clear 后调 policy.reload() 刷新。

### 2.9 commands/builtin.py 新增

```python
async def _handle_permissions(ctx: CommandContext) -> CommandResult:
    """/permissions <子命令> ..."""
    if not ctx.args:
        # 默认显示
        return await _permissions_show(ctx)

    sub = ctx.args[0]
    rest = ctx.args[1:]

    if sub == "show":
        return await _permissions_show(ctx)
    elif sub == "allow":
        return await _permissions_allow(ctx, rest)
    elif sub == "deny":
        return await _permissions_deny(ctx, rest)
    elif sub == "mode":
        return await _permissions_mode(ctx, rest)
    elif sub == "reload":
        return await _permissions_reload(ctx)
    elif sub == "init":
        return await _permissions_init(ctx)
    else:
        ctx.renderer.print_info(f"未知子命令: {sub}")
        ctx.renderer.print_info(
            "用法：/permissions [show|allow|deny|mode|reload|init] ..."
        )
        return CommandResult()
```

每个子命令简单调 `ctx.policy` 的对应方法。注：`ctx.policy` 需要从 main.py
透传到 CommandContext——CommandContext 增加 policy 字段。

## 3. 技术决策

### D1. 为什么黑名单只针对 run 工具

**决策**：黑名单只匹配 run（Bash）工具的 command 参数。

**理由**：
- 黑名单本质是"shell 命令安全"问题
- read / glob / search 是只读，没有"危险"概念
- write / edit 的危险通过沙箱（路径越界）+ 规则（敏感文件）控制
- 简化实现 + 减少误判

### D2. 为什么 TOCTOU 用 fstat + lstat 比对

**决策**：safe_open 内部 open 后立即 fstat（已打开的 fd）+ lstat（路径
本身），比对 inode + dev。

**理由**：
- fstat 拿到的是真正打开的那个文件的元数据
- lstat 拿到的是路径解析的最终对象（不跟随 symlink）
- 两者不一致 = 路径被换了——典型 TOCTOU 攻击模式
- inode + dev 组合在 Windows NTFS 与 POSIX 都可靠
- 不引入新依赖

**局限**：
- 只在 open 那一瞬间检查；open 后又被替换无法检测（但此时 fd 已绑定原文件，读写都安全）
- 真正完美的 TOCTOU 防御需要 openat + AT_NOFOLLOW，但 Python 跨平台支持差

### D3. 为什么规则用 fnmatch 而非 glob.glob

**决策**：规则匹配用 stdlib `fnmatch.fnmatchcase`。

**理由**：
- fnmatch 是纯字符串匹配，不会真去文件系统找
- glob.glob 会遍历文件系统，慢且语义不对
- fnmatchcase 大小写敏感（Linux 友好）；Windows 用户写规则会自然小写
- `*` 匹配任意字符（含 `/`，与 shell 略不同），符合"前缀匹配"语义
- `**` 在 fnmatch 里等价于 `*`（不区分），实际生效是"任意字符串"——简化
  但够用

### D4. 为什么三层文件位置选 .mewcode 子目录

**决策**：用户级 `~/.mewcode/permissions.yaml`，项目级 `<cwd>/.mewcode/...`，
本地级 `<cwd>/.mewcode/permissions.local.yaml`。

**理由**：
- `.mewcode` 子目录更"成体系"，未来还能放别的（settings、history 等）
- 与 mewcode.yaml 同级（项目根放 mewcode.yaml，子目录 .mewcode/ 放规则）
- 跨平台路径友好（Windows / Linux / macOS 都支持）
- 与 Claude Code 的 .claude 目录习惯对齐

### D5. 为什么人在回路在 chat.engine 而非 confirmer

**决策**：新增 `PermissionAsker` 类（permissions/interactive.py），不
复用第二阶段的 Confirmer。

**理由**：
- Confirmer 是"DANGEROUS 工具确认"，y/N 二选
- PermissionAsker 是"权限询问"，y/s/a/N 四选 + 涉及写文件
- 职责分开：Confirmer 关注"危险操作再确认"，Asker 关注"未知规则查问"
- 实际场景：edit 工具仍是 DANGEROUS，会触发两次询问（先 confirmer 后 asker）——
  这是设计取舍（D7 详细讨论）

### D6. 为什么 session_allow 比文件级 deny 更优先

**决策**：session_allow > local deny > project deny > user deny > local allow > ...

**理由**：
- 用户在 REPL 里临时 `/permissions allow "Bash(rm tmp/*)"` 是最近期的意图
  ——即便项目级 YAML 有 `Bash(rm *)` 拒绝，也应当尊重用户当下决定
- 这是"会话级覆盖一切"的简化模型；如果用户错了，session 退出即失效
- 严格性：黑名单不可绕过；其他都可被会话级覆盖

### D7. 为什么仍保留第三阶段的 Confirmer + DangerLevel

**决策**：edit 工具仍是 DANGEROUS，先过 Confirmer，再过 PermissionAsker。

**理由**：
- DangerLevel 是工具自身的属性（"我修改文件容易出错"），与权限规则无关
- 一个 edit 调用如果未匹配规则 → Asker 询问"是否允许 edit"
- 通过后 → Confirmer 询问"确认要 edit 吗（看 diff）"
- 用户在两阶段做不同决策：是否信任工具（权限）+ 是否信任本次执行（具体内容）
- 两次询问体感稍重，但安全性更高；后续可加 "skip confirmer if rule allowed"
  优化（不在本阶段）

### D8. 为什么 policy.check 不接管沙箱校验

**决策**：policy.check 假设调用方已做沙箱校验（chat.engine 在调 policy
之前先调 sandbox.resolve）。

**理由**：
- 沙箱是"路径越界"，权限是"业务规则"，两个层次不同
- 沙箱依赖工具运行时的具体参数（哪个 path 字段），policy 不知道
- 让工具自己（如 ReadTool.execute）调 sandbox.resolve 是第二阶段的契约——
  改了会破坏所有工具
- chat.engine 在调 policy 之前可以选择性预检（如果想提前拒绝）；本阶段
  不预检，让工具自己拦截路径越界（行为与第四阶段一致）

### D9. 为什么 yolo 模式不绕过黑名单

**决策**：黑名单 > yolo。即使 mode=yolo，rm -rf / 仍被拒绝。

**理由**：
- 黑名单是"系统安全底线"，不是"权限策略"
- yolo 是"我相信模型不会乱来"，但 rm -rf / 不是"乱来"是"灾难"
- 用户如果真的想 rm -rf /（极少数场景），可以 yolo + 在终端手动跑——
  不该让 mewcode 帮他

### D10. 为什么会话级 deny 不持久化

**决策**：`/permissions deny` 添加的规则只在当前 session 内有效。

**理由**：
- 持久 deny 规则建议用户手动写入 YAML（更明确的意图）
- /permissions deny 设计场景是"这次别让它做"，不是"永久封杀"
- 永久封杀走 YAML 文件，是 git-tracked 行为；临时 deny 是会话即时

### D11. 为什么 init 命令同时改 .gitignore

**决策**：`/permissions init` 同时检查 .gitignore，缺失时追加
`.mewcode/permissions.local.yaml`。

**理由**：
- 防止用户不小心提交本地规则到 git
- 本地规则可能含敏感命令（如 `Bash(*)` allow 全开 yolo 风格），不该团队共享
- 一次性配置，免得后续踩坑
- 用户感知：init 命令告知"已加 .gitignore 规则"

### D12. 为什么用 fnmatch 而非 pathspec/wildmatch

**决策**：仅用 stdlib fnmatch，不引入 pathspec / wildmatch / wcmatch 等。

**理由**：
- spec N2：本阶段不引入新依赖
- fnmatch 的 `*` 含 `/`（与 shell 略不同），但本场景"前缀匹配"够用
- pathspec 实现 .gitignore 风格更精确，但也更复杂——后续如果用户反馈
  匹配不够准再升级

### D13. 为什么 PermissionAsker 用 print 而非 Renderer

**决策**：PermissionAsker.ask 内部用 print，不调 renderer.print_info。

**理由**：
- Asker 是 permissions/ 模块独立组件，不依赖 chat / render 层
- 模块边界清晰：permissions 不引入 mewcode.render
- 终端输出格式简单（4 行选项 + prompt），不需要 rich 高级特性
- 已设置 sys.stdout.reconfigure(utf-8)（main 启动时），中文与 emoji 正常

## 4. 时序图

### 4.1 default 模式 + 命中 allow 规则

```
chat.engine        policy           tool
   │                │                │
   │ check("run", {"command":"git status"})
   ├───────────────►│
   │                │ blocklist.match("git status") → None
   │                │ extract_match_target → "git status"
   │                │ session_deny.matches → False
   │                │ session_allow.matches → False
   │                │ config.deny.matches → False
   │                │ config.allow.matches("Bash(git *)", "git status")
   │                │   → True
   │                │ Decision("allow", reason="allow 规则命中")
   │◄───────────────┤
   │ tool.execute(...)
   ├──────────────────────────────►│
   │◄──────────────────────────────┤ ToolResult
```

### 4.2 default 模式 + 未匹配 → 询问 → s 选项

```
chat.engine    policy        asker          tool
   │            │              │              │
   │ check(...) │              │              │
   ├───────────►│              │              │
   │            │ Decision("ask")             │
   │◄───────────┤              │              │
   │ asker.ask("run", "make build", cwd)      │
   ├──────────────────────────►│              │
   │            │              │ print 选项   │
   │            │              │ prompt y/s/a/N
   │            │              │ 用户输入 s   │
   │◄──────────────────────────┤ "session"   │
   │ policy.add_session_allow(rule_for("run", "make build"))
   ├───────────►│              │              │
   │ tool.execute(...)         │              │
   ├─────────────────────────────────────────►│
   │            │              │              │ ToolResult
   │◄─────────────────────────────────────────┤
```

### 4.3 黑名单拦截

```
chat.engine     policy
   │              │
   │ check("run", {"command":"rm -rf /"})
   ├─────────────►│
   │              │ blocklist.match → matched (rm pattern)
   │              │ Decision("deny", error_category="黑名单拦截")
   │◄─────────────┤
   │ ToolResultBlock(success=False, content=reason, is_error=True)
   │ → 入历史，Loop 继续 R2
```

### 4.4 a 选项写入 local YAML

```
asker            local YAML
  │                  │
  │ 用户输入 a       │
  │                  │
  │ load existing yaml (or create)
  ├─────────────────►│
  │◄─────────────────┤ {mode:..., allow:[...]}
  │                  │
  │ data["allow"].append("Bash(make build)")
  │                  │
  │ yaml.safe_dump(...)
  ├─────────────────►│
  │                  │ 文件已写入
  │ return "forever"
```

## 5. 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/permissions/__init__.py` |
| 新建 | `mewcode/permissions/blocklist.py` |
| 新建 | `mewcode/permissions/rules.py` |
| 新建 | `mewcode/permissions/loader.py` |
| 新建 | `mewcode/permissions/policy.py` |
| 新建 | `mewcode/permissions/interactive.py` |
| 修改 | `mewcode/tools/sandbox.py` （+ safe_open + PathRaceConditionError）|
| 修改 | `mewcode/tools/errors.py` （+ PathRaceConditionError）|
| 修改 | `mewcode/tools/read.py` / `write.py` / `edit.py` （改用 safe_open）|
| 修改 | `mewcode/chat/session.py` （+ permission_* 字段）|
| 修改 | `mewcode/chat/engine.py` （集成 policy.check）|
| 修改 | `mewcode/commands/registry.py` （CommandContext 加 policy 字段）|
| 修改 | `mewcode/commands/builtin.py` （+ /permissions 子命令）|
| 修改 | `mewcode/main.py` （加载 PermissionPolicy + 注入）|
| 修改 | `mewcode/repl/main_loop.py` （透传 policy）|
| 新建 | `tests/test_blocklist.py` |
| 新建 | `tests/test_permissions_rules.py` |
| 新建 | `tests/test_permissions_loader.py` |
| 新建 | `tests/test_permissions_policy.py` |
| 新建 | `tests/test_permissions_interactive.py` |
| 新建 | `tests/test_sandbox_toctou.py` |
| 新建 | `tests/test_permissions_command.py` |
| 新建 | `scripts/verify_permissions.py` |

共 22 个文件（13 新建 + 9 修改）。

## 6. 与第四阶段的兼容矩阵

| 第四阶段行为 | 第五阶段是否保留 | 说明 |
|-------------|-----------------|------|
| run_turn 签名 | ✅ 不变 | 内部加 policy 处理 |
| Provider stream_chat | ✅ 不变 | |
| Tool.execute 签名 | ✅ 不变 | |
| ToolRegistry 接口 | ✅ 不变 | |
| Sandbox.resolve | ✅ 保留 | + 新增 safe_open |
| Confirmer 行为 | ✅ 保留 | edit 仍 DANGEROUS 走 confirmer |
| Plan Mode 物理隔离 | ✅ 保留 | tools_format 不含写类工具 |
| Agent Loop 五种停止 | ✅ 保留 | 权限拒绝是工具失败，不是停止条件 |
| AgentEvent 7 种 | ✅ 不变 | 权限决策内部完成，不发新事件 |
| system prompt 7 模块 | ✅ 不变 | |
| prompt cache | ✅ 不变 | 权限规则不进 LLM 请求 |
| /clear /provider /think /plan /do /permissions | ✅ + 新 |
| 145 个已有单测 | ✅ 全过 | 新测试不影响旧测试 |

### 不需要适配的已有测试

无——本阶段所有改动都是**新增层**或**新增方法**：
- Sandbox.safe_open 是新方法，原有 resolve 不变
- chat.engine 在工具执行前插入 policy.check，不影响后续逻辑
- /permissions 是新命令
- 工具的 read/write/edit 改用 safe_open，但单测用的 stub Sandbox 不感知
  （除非要测 TOCTOU 才需要 mock）

唯一可能影响：第三阶段的 `test_chat_round_loop.py` 中如果 chat.engine
默认拿到一个空的 PermissionPolicy（mode=default），且没匹配规则 → 进入
ask 路径会卡住。**解决方案**：测试中传入一个"自动 allow 一切"的 stub policy。
