# MewCode 第五阶段 Spec

## 背景

MewCode 第四阶段已交付结构化 system prompt 与 prompt cache（docs/05/）。
四阶段之后，MewCode 拥有完整的工具系统（read/write/edit/run/glob/search）、
Agent Loop 多轮自主、Plan/Do 模式、缓存优化等能力——能干活、干得好。

但目前的"安全"只有两层：
1. **工作目录沙盒**（spec 第二阶段 F10）：路径越界拒绝
2. **DangerLevel.DANGEROUS 工具确认**（仅 edit）：执行前 y/N 询问

这两层在简单场景够用，但有明显短板：
- **没有命令层面的拒绝**——`run rm -rf /` / `run :(){:|:&};:` 类致命操作
  会直接执行
- **没有细粒度规则**——要么全允许要么全拒绝，无法表达"放行 git 但拦截
  rm"这类常见诉求
- **没有信任分级**——用户不能在严格审查模式与放手模式之间切换
- **没有人在回路**——规则没覆盖到的边缘场景，工具直接执行而非询问
- **没有规则文件**——所有信任决策只在内存里，无法跨会话复用

本阶段（第五阶段）为 MewCode 装上**五层防御权限系统**：从硬黑名单到
人在回路逐层把关，权限拒绝时不终止 Agent Loop，让模型有机会调整策略。

## 目标

- 五层防御：黑名单硬拦截 → 路径沙箱（含 TOCTOU 防御）→ 可配置规则
  → 权限模式 → 人在回路
- 黑名单层：用正则在 run 工具执行前拦掉已知致命命令；不可被配置或
  yolo 模式放开
- 沙箱层：继承第二阶段 Sandbox 的路径前缀校验，新增 `safe_open` 方法
  防 TOCTOU 竞态
- 规则层：用户级 + 项目级 + 本地级三层 YAML，越靠近项目优先级越高；
  规则格式 `工具名(glob 模式)`，按 allow / deny 两列分类
- 模式层：strict / default / yolo 三档覆盖在规则之上
- 人在回路：规则未明确命中时按 y/s/a/N 四选询问用户，支持本次 / 本会话 /
  永久（写入本地级 YAML）三种放行
- 拒绝不终止 Loop：返回结构化 ToolResult，含详细错误与引导文字，让模型
  在 R2 中向用户解释或请求权限
- 命令支持：新增 /permissions 子命令族（show / allow / deny / mode /
  reload / init）
- 第一/二/三/四阶段功能不退化

## 功能需求

### F1. 五层防御链

工具调用经过下列检查链，任一层拒绝即停（返回 ToolResult，不抛异常）：

```
工具调用请求
    │
    ▼
┌─────────────────────────────┐
│ Layer 1: 黑名单（仅 run 工具）│ — 不可配置
│  - rm -rf / / ~ / $HOME      │
│  - mkfs.* / dd of=/dev/sd*   │
│  - fork 炸弹 :(){:|:&};:     │
│  - curl|sh / wget|sh         │
└─────────────┬───────────────┘
              │ pass
              ▼
┌─────────────────────────────┐
│ Layer 2: 沙箱 + TOCTOU       │ — 路径相关工具必经
│  - resolve 后前缀校验        │
│  - safe_open 时 fstat 比对   │
└─────────────┬───────────────┘
              │ pass
              ▼
┌─────────────────────────────┐
│ Layer 3: 可配置规则匹配       │ — YAML 三层合并
│  - 命中 deny → 拒绝          │
│  - 命中 allow → 通过         │
│  - 都未命中 → 进入 Layer 4   │
└─────────────┬───────────────┘
              │ unmatched
              ▼
┌─────────────────────────────┐
│ Layer 4: 权限模式            │
│  - yolo → 直接通过           │
│  - default → 进入 Layer 5    │
│  - strict → 进入 Layer 5     │
│    （strict 模式下询问 UI    │
│    更显眼，但行为同 default）│
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Layer 5: 人在回路            │
│  y - 仅本次允许              │
│  s - 本会话允许              │
│  a - 永久（写入本地级 YAML） │
│  N - 拒绝                    │
└─────────────────────────────┘
```

### F2. 黑名单（spec Q1 / D1）

不可配置的硬拦截，仅作用于 run 工具的 command 参数：

```python
DANGEROUS_PATTERNS = [
    # rm -rf 致命路径
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*|--recursive)\s+(-[a-zA-Z]*f[a-zA-Z]*|--force)?\s*(/|~|\$HOME|/\*)",
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*)\s+(-[a-zA-Z]*r[a-zA-Z]*)\s*(/|~|\$HOME|/\*)",
    # 文件系统破坏
    r"\bmkfs(\.\w+)?\s",
    r"\bdd\s+.*\bof=/dev/(sd|nvme|hd|xvd)",
    r">\s*/dev/(sd|nvme|hd|xvd)",
    # fork 炸弹
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # 网络下载直接执行
    r"\bcurl\s+.*\|\s*(sh|bash|zsh)\b",
    r"\bwget\s+.*\|\s*(sh|bash|zsh)\b",
    # Windows 等价的破坏命令
    r"\bformat\s+[A-Za-z]:",
    r"\brmdir\s+/[sS]\s+/[qQ]\s+[A-Za-z]:",
]
```

匹配任意一条 → 立即返回 ToolResult 拒绝；error_category="黑名单拦截"。

### F3. 沙箱 + TOCTOU 防御（spec Q3 / Q9）

继承第二阶段 Sandbox.resolve 的"resolve 后前缀校验"。新增方法
`safe_open(path, mode)`：

```python
def safe_open(self, raw_path: str, mode: str = "r", encoding="utf-8"):
    """以原子方式安全打开文件，防 TOCTOU。

    步骤：
    1. resolve raw_path → 校验在 cwd 内
    2. open 文件
    3. 对 fd 调 os.fstat → 取 inode/dev
    4. 对 resolve 后的路径调 os.lstat → 取 inode/dev
    5. 比对：不一致说明 open 后路径被换成了 symlink，抛 PathRaceConditionError

    Returns:
        contextmanager（支持 with 语句）
    """
```

read / write / edit 三个文件工具改用 `sandbox.safe_open` 替代直接 open。

注：Windows 下 inode 概念是 `st_ino + st_dev` 组合（NTFS 也支持），跨平台。

### F4. 可配置规则——格式

规则格式：`工具名(glob 模式)`，分 allow / deny 两类。

工具名是首字母大写：`Bash` / `Read` / `Write` / `Edit` / `Glob` / `Search`。
（注：内部工具 name 是小写 `run/read/...`，规则层用首字母大写以与 Claude
Code 兼容；模式匹配时大小写不敏感。）

模式语义（spec Q5 / D5）：
- 整条命令/路径的**前缀匹配**（不是包含、不是任意位置）
- glob 字符 `*` 匹配任意非分隔符字符；`**` 匹配任意（含分隔符）
- `Bash(git *)` 匹配 `git status` ✅、`git push origin main` ✅；
  不匹配 `cd /tmp && git status`（不以 git 开头）

YAML 文件示例：

```yaml
# .mewcode/permissions.yaml
mode: default

allow:
  - "Bash(git *)"
  - "Bash(npm install*)"
  - "Bash(pytest*)"
  - "Read(**/*.py)"
  - "Write(tmp/**)"

deny:
  - "Bash(rm *)"          # 比黑名单更严的 rm 限制
  - "Edit(mewcode.yaml)"  # 不允许改 mewcode.yaml
  - "Edit(.git/**)"
```

规则解析：
- 非法格式（不含括号、空规则）→ 启动时给 warning 但不阻塞
- 同一工具的多条规则：按 YAML 列表顺序匹配，先命中先生效

### F5. 三层 YAML 文件（spec Q6 / Q11）

- **用户级**：`~/.mewcode/permissions.yaml`（跨项目，全局默认）
- **项目级**：`<cwd>/.mewcode/permissions.yaml`（与项目共享，建议加入 git）
- **本地级**：`<cwd>/.mewcode/permissions.local.yaml`（本机不共享，建议
  加入 .gitignore；人在回路的"a 永久"会写到这里）

启动时按"本地级 → 项目级 → 用户级"顺序加载，三层合并：
- **mode 字段**：取最高优先级层（本地级有 mode 则用之，否则项目级…）
- **allow / deny 列表**：按"本地→项目→用户"顺序拼接，更优先的在前；先匹配先生效

文件不存在时视为空（mode 缺失则继续向下层找），不报错。

### F6. 权限模式（spec Q8 / D8）

三档语义：

- **strict 严格**：所有未明确 allow 的工具调用都进入人在回路询问（即便
  Layer 3 没命中 deny 也要问）
- **default 默认**：Layer 3 未命中时进入 Layer 5 询问；命中 allow 直接通过
- **yolo 放行**：除黑名单 + 沙箱外全部允许，跳过 Layer 3 / Layer 5

启动时若 mode 是 yolo，REPL 横幅打印警告：
```
⚠️ 权限模式：YOLO（除致命黑名单外全部放行）
```

### F7. 人在回路 UI（spec Q13 / D13）

终端打印：
```
● Bash rm -rf node_modules
未匹配规则，是否允许？
  y - 仅本次
  s - 本会话
  a - 永久（写入 permissions.local.yaml）
  n - 拒绝
请选择 [y/s/a/N]:
```

输入处理：
- `y` 或 `yes` → 仅本次允许，下次重问
- `s` → 本会话允许，记入 session.session_allowlist（mewcode 退出后失效）
- `a` → 写入 `.mewcode/permissions.local.yaml` 的 allow 列表
- `n` 或回车（默认）或 EOF（Ctrl+D）→ 拒绝
- Ctrl+C → 抛 ConfirmCancelled，整个 turn 取消（继承第二阶段 N5）

询问时对应的工具调用是 `Tool name + 完整参数`——例如 Bash 工具的命令、
Read 工具的路径，让用户能看清要批准什么。

### F8. 拒绝不终止 Loop（spec Q14 / D14）

任何层的拒绝都返回结构化 ToolResult：

```python
ToolResult(
    success=False,
    text="权限拒绝：Bash(rm -rf node_modules) 未在允许规则内。"
         "如需允许此操作，请告诉用户运行 /permissions allow \"Bash(rm -rf*)\" "
         "或临时切换到 yolo 模式。",
    error_category="权限拒绝",  # 或 "黑名单拦截" / "路径越界" / "TOCTOU 竞态"
)
```

Agent Loop 收到失败的 ToolResult 后正常进入下一轮——模型看到详细错误
后可以：
- 调整命令尝试（如把 `rm -rf node_modules` 改为 `rm node_modules/.cache` 求精）
- 在文本答复中告诉用户"我需要 X 权限，请运行 /permissions allow 或切换 yolo"
- 放弃该路径，换思路

### F9. /permissions 命令族

新增斜杠命令：

```
/permissions show          列出当前生效的所有规则与 mode
/permissions allow <规则>   添加一条 allow 规则（会话级，临时）
/permissions deny <规则>    添加一条 deny 规则（会话级，临时）
/permissions mode <档位>    切换 strict / default / yolo
/permissions reload         重新加载三层 YAML（覆盖会话级）
/permissions init           生成默认 permissions.yaml 模板
```

### F10. Session 状态扩展

Session 增加：
```python
permission_session_allow: list[str]   # /permissions allow 添加 + s 选项
permission_session_deny: list[str]    # /permissions deny 添加
permission_mode_override: str | None  # /permissions mode 临时覆盖
```

session_allow / deny 比文件级规则**更优先**（最高优先级层）；mewcode 退出
后失效。

### F11. 黑名单层例外提示

黑名单触发时给用户与模型的反馈要明确"这条不可绕过"：

```
ToolResult(
    success=False,
    text="黑名单拦截：rm -rf / 是不可配置的高危操作，无法通过权限规则或 yolo 模式放行。"
         "请使用更精确的删除路径。",
    error_category="黑名单拦截",
)
```

### F12. 优先级最终顺序

工具调用最终走的判定优先级（从高到低）：

1. 黑名单（不可绕过）
2. 沙箱 + TOCTOU（不可绕过）
3. 会话级 deny（/permissions deny + 内存）
4. 会话级 allow（/permissions allow + s 选项）
5. 本地级 YAML deny
6. 本地级 YAML allow
7. 项目级 YAML deny
8. 项目级 YAML allow
9. 用户级 YAML deny
10. 用户级 YAML allow
11. 权限模式（yolo / strict / default）
12. 人在回路

### F13. /permissions init 模板

`permissions init` 在 `<cwd>/.mewcode/permissions.yaml` 生成：

```yaml
# MewCode 权限规则
# 详见 docs/06/spec.md 第五阶段

mode: default

# allow 列表：明确允许的工具调用
allow:
  - "Bash(git *)"
  - "Bash(npm install*)"
  - "Bash(npm test*)"
  - "Bash(pytest*)"
  - "Bash(python -m*)"
  - "Read(**/*)"
  - "Glob(**/*)"
  - "Search(**/*)"

# deny 列表：明确拒绝的工具调用
deny:
  - "Edit(mewcode.yaml)"
  - "Edit(.git/**)"
  - "Edit(.env*)"
  - "Bash(rm -rf*)"
```

如果文件已存在，提示"文件已存在，未覆盖"，给用户决定是否手动备份。

### F14. 不做的事

明确不做：
- 网络请求限制（防 curl/wget 总用量、域名黑白名单）—— 后续章节
- 资源配额（CPU/内存/磁盘 quota）—— 后续章节
- 审计日志（permission_log.jsonl 持久化所有决策）—— 后续章节
- 团队同步（中央化规则服务器）—— 不做
- 工具别名映射（用户自定义 Tool 名称）—— 不做
- 二次确认锁定（多次拒绝后 lockdown 整个 turn）—— 不做

## 非功能需求

### N1. 模块边界

- `mewcode/permissions/`（新模块）：
  - `blocklist.py`：黑名单常量与匹配
  - `rules.py`：YAML 解析 + 规则匹配
  - `loader.py`：三层文件加载与合并
  - `policy.py`：综合判定（五层链）
  - `interactive.py`：人在回路 UI
- `chat.engine` 通过 `policy.check(tool_call) → Decision` 做权限判定
- Sandbox 在 `tools.sandbox` 增加 safe_open 方法（继承第二阶段位置）
- 工具实现层（read/write/edit）改用 sandbox.safe_open
- 不引入新依赖（YAML 已有 PyYAML）

### N2. 不引入新依赖

dependencies 仍仅 `prompt_toolkit / rich / PyYAML / httpx`。
glob 匹配用 stdlib `fnmatch`；TOCTOU 用 `os.fstat / os.lstat`。

### N3. 中文优先

所有错误信息、确认提示、命令文档、模板注释均为中文。

### N4. 单测覆盖

按 spec Q15 / D15 的 10 类测试，预计新增 25-30 个：
- 黑名单匹配 / 不匹配 各场景
- TOCTOU 防御
- 规则解析（YAML 合法 / 非法）
- 规则匹配（前缀语义）
- 三层合并
- 三种权限模式行为差异
- 人在回路 4 种回答
- /permissions 6 个子命令
- 永久写盘
- 拒绝不终止 Loop（stub Provider 单测）

### N5. 第一/二/三/四阶段不退化

- 145 个已有单测全过
- 已有端到端脚本全部仍通过（注：可能需要在测试环境配置宽松规则文件
  让模型能正常调 read/glob/search/run）
- run_turn / Provider / ToolRegistry / Sandbox.resolve 接口不变
- Plan Mode / Agent Loop / system prompt 行为不变
- /clear / /provider / /think / /plan / /do 命令不变

### N6. Windows 兼容

- 黑名单匹配 Windows 等价命令（format / rmdir）
- TOCTOU 在 NTFS 上 inode 用 `st_ino` + `st_dev` 组合可靠
- 路径分隔符：YAML 中允许 `/`，内部统一转换
- shell 命令：Windows 下 cmd 的命令不会触发 Linux 黑名单（如 `del` 不在
  黑名单），用户需要靠 deny 规则补充

### N7. 默认安全策略

无任何 YAML 文件时（首次使用）：
- mode = default
- allow / deny 都为空
- 所有工具调用都进入人在回路询问

这保证"默认就是最严格"——用户主动 `permissions init` 后才会有放行规则。

### N8. yolo 模式警告

启动时检测到 mode=yolo（不论来自哪一层 YAML），REPL 横幅必须打印：
```
⚠️ 权限模式：YOLO（除致命黑名单外全部放行）
```
红色或黄色高亮，让用户清楚自己关掉了大多数防御。

### N9. 规则匹配大小写

工具名匹配大小写不敏感（`Bash` / `bash` / `BASH` 都识别为 run 工具的规则）。
glob 模式按当前操作系统的文件系统大小写敏感性（Linux 敏感，Windows 不敏感）。

### N10. 文件加载错误处理

YAML 解析失败时：
- 启动时打印红字警告：`⚠️ 权限规则文件 X 解析失败：<原因>`
- 视该层为空，继续启动（不阻塞 REPL）
- /permissions reload 时同样行为

### N11. permissions.local.yaml 自动加入 .gitignore

`/permissions init` 同时检查 .gitignore，若不含 `.mewcode/permissions.local.yaml`
则追加，避免本地规则被误提交。

## 验收标准

### AC1. 黑名单硬拦截

通过单测：构造 RunTool 调用 `rm -rf /` / `:(){:|:&};:` 等 5 条黑名单
命令 → policy.check 返回 deny；error_category="黑名单拦截"。

### AC2. 黑名单不可被 yolo 绕过

通过单测：mode=yolo 时调用 `rm -rf /` 仍被拒绝。

### AC3. TOCTOU 防御

通过单测：mock Path.resolve 返回正常路径，但 open 后 fstat 与 lstat
不一致 → safe_open 抛 PathRaceConditionError。

### AC4. 沙箱仍生效

通过单测：read / write / edit 调用 cwd 外路径 → 拒绝（继承第二阶段）。

### AC5. 规则 YAML 解析

通过单测：构造合法 YAML → 规则列表正确；非法 YAML（不含括号的规则）→
启动 warning + 视为空。

### AC6. 规则前缀匹配语义

通过单测：
- `Bash(git *)` 匹配 `git status` ✅
- `Bash(git *)` 不匹配 `cd /tmp && git status` ❌
- `Read(src/**/*.py)` 匹配 `src/a/b.py` ✅

### AC7. 三层合并

通过单测：用户级 mode=strict / 项目级空 / 本地级 mode=yolo →
最终生效 mode=yolo。

### AC8. 权限模式 yolo

通过单测：mode=yolo + 任意非黑名单命令 → 通过；rm -rf / 仍拒绝。

### AC9. 权限模式 strict

通过单测：mode=strict + 命中 allow 的命令 → 仍走人在回路（最严格）。

### AC10. 人在回路四选行为

通过单测（mock 输入）：
- y → success；下次再问（不持久化）
- s → success；同 turn 内再次调用同样命令直接通过
- a → success；写入 permissions.local.yaml
- N（默认） → ToolResult(success=False, error_category="用户拒绝")

### AC11. 拒绝不终止 Loop

通过 stub Provider 单测：构造 turn 内多个工具调用，其中一个权限拒绝
→ 返回 ToolResult(success=False) 入历史 → R2 正常发起 →
模型答复中含权限错误信息。

### AC12. /permissions show

执行命令打印当前生效规则与 mode。

### AC13. /permissions allow / deny

执行后会话级规则即时生效；通过单测验证。

### AC14. /permissions mode

切换后立即覆盖文件级 mode；通过单测验证。

### AC15. /permissions reload

清空会话级覆盖，重新加载三层 YAML。

### AC16. /permissions init

生成模板文件；存在时不覆盖。

### AC17. 默认无文件最严格

无任何 YAML 时，每个工具调用都触发人在回路。

### AC18. yolo 警告

mode=yolo 启动时横幅打印警告。

### AC19. 不退化

- pytest 全过（145 + 新增约 25-30）
- 已有端到端脚本仍通过
- run_turn / Provider / Sandbox.resolve 等接口不变

### AC20. /permissions init 加 .gitignore

执行后 .gitignore 含 `.mewcode/permissions.local.yaml`。

## 依赖与约束

- 继承前四阶段全部接口契约
- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台兼容
