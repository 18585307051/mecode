# MewCode 第七阶段 Spec

## 背景

第四阶段 `system_prompt/builder.py` 在 `build_system_prompt(...)` 上预留
了三个可选参数：

```python
def build_system_prompt(
    cwd: Path,
    tools: list[str],
    custom_instructions: str | None = None,
    skills: list[str] | None = None,
    memory: str | None = None,
) -> str:
```

第四到第六阶段一直传 `None`——hook 留着但没人用。

业界事实标准是：项目根放一份 Markdown 指令文件（`AGENTS.md` / `CLAUDE.md`
/ `.cursorrules`），AI 工具启动时自动加载。这是用户表达"项目级 AI 工作
规则"最高频的入口——比修改 system prompt 简单、比注释代码省力、比交互
里反复说省心。

第七阶段把 `custom_instructions` 这个 hook 接通：用户在项目根放一份
Markdown，mewcode 启动时自动加载并注入 system prompt。

## 目标

- 启动时按多名兼容查找指令文件（AGENTS.md → CLAUDE.md → .mewcoderc）
- 三层文件支持：用户级（~/.mewcode/）、项目级（cwd 根）、本地级
  （cwd/.mewcode/AGENTS.local.md）
- 三层内容拼接为单段字符串，按 H2 标题包装来源，注入 system prompt
  的 `custom_instructions` 参数
- 单文件 8KB 上限（超限截断 + warning），错误容错（不阻塞启动）
- 启动时横幅显示加载层（仅当至少一层有内容）
- 新增 `/instructions show` 与 `/instructions reload` 命令
- reload 内容未变时不替换 system prompt（保护 prompt cache）
- 第一/二/三/四/五/六阶段功能不退化

## 功能需求

### F1. 文件名候选与查找顺序

每一层独立查找，按以下顺序找到第一个就停（spec Q1 / Q11）：

```
AGENTS.md  → CLAUDE.md → .mewcoderc
```

理由：
- `AGENTS.md` 是 OpenAI Codex / GitHub 等正在统一的标准
- `CLAUDE.md` 是 Anthropic Claude Code 风格
- `.mewcoderc` 兜底（项目自有命名）

### F2. 三层文件位置

| 层级 | 路径 |
|------|------|
| 用户级 | `~/.mewcode/AGENTS.md` (或 CLAUDE.md / .mewcoderc) |
| 项目级 | `<cwd>/AGENTS.md` (或 CLAUDE.md / .mewcoderc) |
| 本地级 | `<cwd>/.mewcode/AGENTS.local.md` (或 CLAUDE.local.md) |

层间合并规则（spec Q2 / D2）：
- **拼接（不是覆盖）**——三层内容都生效，因为指令是叠加的（不像权限规
  则那样需要"项目级覆盖用户级"）
- 顺序：用户 → 项目 → 本地

### F3. 拼接格式

最终注入 `custom_instructions` 的字符串结构（spec Q5 / Q12 / D5）：

```markdown
以下是用户在项目中明确写出的工作规则，应当严格遵守：

### 用户全局规则（来自 ~/.mewcode/AGENTS.md）
<用户级文件原文>

### 项目规则（来自 ./AGENTS.md）
<项目级文件原文>

### 本地规则（来自 ./.mewcode/AGENTS.local.md）
<本地级文件原文>
```

规则：
- 每段前必加 H3 标题 + 来源路径
- 某层缺失则跳过该段（不打空标题）
- 三层全部缺失 → 整体返回 None，`custom_instructions` 不传值（不出现
  `## 自定义指令` 段落）
- 多名兼容：标题里写实际找到的文件名（如 `CLAUDE.md` 就显示 CLAUDE.md）

### F4. 注入 system prompt

调 `build_system_prompt(...)` 时把拼接结果传给 `custom_instructions`
参数（第四阶段已留好）。结果：

```
7 固定模块 → 当前环境 → 自定义指令（本阶段填充）→ Skill → 长期记忆
                                ↑↑↑
```

仍然进入 prompt cache 的 system 段，只要内容不变就持续命中缓存。

### F5. 单文件大小限制

每个文件最多 8KB（spec Q6）：
- 读取时按字节截断（保留前 8192 字节）
- 截断后追加 `\n\n[... 内容已截断（超过 8KB 上限）...]\n`
- 打印 warning：`⚠️ 项目指令文件 <路径> 超过 8KB，已截断`

### F6. 错误容错（spec Q7 / D7）

启动加载阶段任何错误都不阻塞 REPL：
- 文件不存在 → 视为该层无内容
- 文件读不了（PermissionError / OSError）→ warning + 视为空
- 非 UTF-8 编码 → warning + 跳过（视为空）
- 超过 8KB → 截断 + warning（不视为空）
- 三层全部为空 → 不打横幅、不传 `custom_instructions`、行为等同前阶段

### F7. 启动横幅

当至少一层加载到内容时，在所有其他启动横幅（Banner / Provider / 权限
模式 / MCP）之后打印（spec Q10）：

```
📋 项目指令: 用户级 (0.8KB) + 项目级 (1.2KB)
```

显示规则：
- 只显示有内容的层
- 大小用截断后字节数，向上取整到 0.1KB
- 三层全无 → 不打印（避免噪音）

### F8. /instructions 命令族

新增斜杠命令（spec Q8）：

```
/instructions show     显示当前生效的指令文本（含来源标题）
/instructions reload   重新加载三层文件
/instructions          缺省 = show
```

不实现 `init` / `edit`（spec Q8 / D8）。

### F9. /instructions show

打印当前 session 中已注入的 `custom_instructions` 字符串：
- 如果三层全空 → 提示 `当前未加载任何项目指令。建议在项目根创建
  AGENTS.md 写明工作规则。`
- 否则 → 打印完整字符串（含 H3 标题与来源）

### F10. /instructions reload

重新执行加载逻辑（spec Q9 / F8）：
1. 重新读三层文件
2. 重新构造 `custom_instructions` 字符串
3. **内容比对**（spec Q9 / D9）：
   - 用 SHA-256 hash 比对新旧内容
   - 相同 → 打印 `指令未变化，未重新构造 system prompt（cache 仍生效）`
   - 不同 → 重新调 `build_system_prompt(...)` → 替换 `session.system_prompt`
     → 打印 `已重新加载（X.XKB）。下次请求会重新建立 prompt cache。`

### F11. 加载阶段对 prompt cache 的影响

启动时构造的 `session.system_prompt` 一旦写入即不变（除非 `/instructions
reload` 触发）。这保证：
- 同一进程内的所有请求共享同一个 cache breakpoint
- prompt cache 命中率不退化（与第四阶段一致）

### F12. CommandContext 新增字段

`commands/registry.py` 的 CommandContext 新增字段：

```python
@dataclass
class CommandContext:
    ...
    instructions: object = None  # 第七阶段：InstructionsLoader 实例
```

reload 命令需要从 ctx.instructions 拿到 loader 重新加载。

### F13. 不做的事

明确不做：
- frontmatter / YAML 元数据（spec Q4 / D4）
- 章节级选择性加载（spec Q4）
- 自动追加 .gitignore（spec Q13 / D13）
- /instructions init / edit 命令（spec Q8 / D8）
- 加载多个候选文件并拼接（同层只取第一个，spec Q11 / D11）
- 监听文件变化自动 reload（仍要手动 /instructions reload）
- 跨 session 持久化（每次启动都重新读文件）
- 三层之外的目录搜索（如 monorepo 子目录）
- 模型可写指令文件（用户手动维护）

## 非功能需求

### N1. 模块边界

- 新模块 `mewcode/instructions/`：
  - `__init__.py`：暴露 InstructionsLoader 与 load_all
  - `loader.py`：三层文件查找、加载、拼接、reload
- main.py 在加载阶段调用 loader.load_all → 传给 build_system_prompt
- commands/builtin.py 加 `/instructions` 命令 handler
- commands/registry.py CommandContext 加 `instructions` 字段
- 不动的模块：
  - chat / providers / render / permissions / mcp 全部零修改
  - system_prompt/builder.py 不变（已留 custom_instructions 参数）

### N2. 不引入新依赖

dependencies 仍仅 4 项（prompt_toolkit / rich / PyYAML / httpx）。
SHA-256 用 stdlib `hashlib`。

### N3. 中文优先

错误提示、warning、命令文档全中文。指令文件内容本身由用户决定语言，
mewcode 不做翻译。

### N4. 单测覆盖（spec Q14）

新增约 10-12 个测试：
1. 加载逻辑（4-5 个）：
   - 找到第一个匹配文件名
   - 文件不存在视为空
   - 超限截断 + warning
   - 三层全部缺失 → None
2. 拼接逻辑（3-4 个）：
   - 单层时只显示该层
   - 多层时按用户→项目→本地顺序
   - 全部为空时返回 None
3. /instructions 命令（2-3 个）：
   - show 输出
   - reload 内容相同 / 不同时的行为

### N5. 不退化

- 297 个已有单测全过
- 已有 8 个端到端脚本仍通过
- 不传 instructions 文件时（无任何 AGENTS.md），mewcode 启动行为完全
  等同第六阶段
- run_turn / Provider / ToolRegistry / Sandbox / PermissionPolicy /
  MCP 接口全部不变
- /clear / /provider / /think / /plan / /do / /permissions 命令不变

### N6. Windows 兼容

- 文件读取统一用 `Path.read_text(encoding="utf-8")`
- ~ 展开用 `Path.home()`
- 文件编码错误（GBK 残留等）→ warning 而非崩溃

### N7. 性能

启动加载是同步 I/O，三层最多读 6 个文件（每层 3 个候选）：
- 实际开销 < 10ms
- 不放进 asyncio.gather（没必要）

### N8. 模块依赖单向

```
mewcode/instructions/
  ↓ 依赖 stdlib only
不依赖：chat / providers / render / permissions / mcp / tools / system_prompt
```

main.py 是 instructions 与 build_system_prompt 的唯一连接点。

## 验收标准

### AC1. 文件查找：找到第一个匹配
通过单测：项目级目录下同时有 AGENTS.md 和 CLAUDE.md → 加载 AGENTS.md
（按候选顺序）。

### AC2. 文件查找：候选都不存在
通过单测：项目级目录下三个候选都不存在 → 该层视为空，不报错。

### AC3. 三层全空 → 整体 None
通过单测：用户级 / 项目级 / 本地级三个目录都没有候选文件 →
load_all 返回 None。

### AC4. 三层拼接：用户→项目→本地顺序
通过单测：三层都有内容 → 输出按"用户全局规则 → 项目规则 → 本地规则"
顺序，每段前有 H3 标题。

### AC5. 三层拼接：缺一层不出空标题
通过单测：仅项目级有内容 → 输出只有项目规则段，没有"用户全局规则" /
"本地规则" 标题。

### AC6. 单文件 8KB 限制
通过单测：写入一个 9KB 文件 → 加载后字节数 ≤ 8KB + 截断标记 +
warning 包含 "已截断"。

### AC7. 文件读取失败容错
通过单测：mock 文件读取抛 PermissionError → 该层视为空，warning 包含
"读不了"。

### AC8. 非 UTF-8 文件容错
通过单测：写入一个 GBK 编码的文件 → warning 包含"非 UTF-8" + 该层视为
空。

### AC9. /instructions show
通过单测：reload 后调 show → renderer.print_info 收到完整指令文本。

### AC10. /instructions show 全空
通过单测：三层全空时 show → renderer.print_info 收到"未加载任何项目
指令"提示。

### AC11. /instructions reload 内容未变
通过单测：reload 两次（中间不改文件）→ 第二次提示 "指令未变化"，
session.system_prompt 不变。

### AC12. /instructions reload 内容变化
通过单测：reload → 改文件 → 再 reload → session.system_prompt 已更新，
打印"已重新加载"。

### AC13. 横幅显示加载层
启动时项目级有 1.2KB 内容 → 横幅打印 "📋 项目指令: 项目级 (1.2KB)"。

### AC14. 横幅：全空不打印
三层全空 → 不打印 📋 横幅。

### AC15. 注入 system_prompt
通过集成测：项目级有内容 → session.system_prompt 含 "## 自定义指令"
段 + 含"项目规则"H3 标题。

### AC16. 不退化
- pytest tests/ -q 全过（297 + 12 ≈ 309）
- verify_t9 / verify_t18 / verify_t19 / verify_round_loop /
  verify_agent_loop / verify_plan_mode / verify_cache_hit /
  verify_permissions / verify_mcp 全部通过
- 无任何 AGENTS.md 时启动行为等同第六阶段

### AC17. 多名兼容
通过单测：项目级仅有 CLAUDE.md → 加载 CLAUDE.md，标题为 "项目规则
（来自 ./CLAUDE.md）"。

## 依赖与约束

- 继承前六阶段全部接口契约
- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台
