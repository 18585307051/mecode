# MewCode 第十阶段 Plan：斜杠命令系统

## 架构概览

第十阶段在已有 `mewcode/commands/` 三件套（`registry.py` / `builtin.py` / `__init__.py`）之上做"加厚"——不重写架构，扩展元数据、加严注册校验、补齐三类命令的执行边界、引入 Tab 补全、把命令实现按职责拆开避免 `builtin.py` 继续膨胀。

```
                  +-------------------+
   用户输入  →    |  REPL main_loop   |
                  +---------+---------+
                            │
                  以 / 开头? │ 否 → run_turn (chat 对话分支)
                            │ 是
                            ▼
                  +-------------------+
                  | commands.dispatch |
                  +---------+---------+
                            │ 查注册表
                            ▼
        +-----------+ +-----------+ +-----------+
        |   LOCAL   | | STATEFUL  | |  PROMPT   |
        +-----+-----+ +-----+-----+ +-----+-----+
              │             │             │
              │             │             ▼
              │             │       prompt_text 注入下一轮
              │             ▼             │
              │      修改 session/        │
              │      policy/...           │
              ▼             ▼             ▼
            renderer 输出 / CommandResult
```

新增/修改的物理边界：

- `mewcode/commands/registry.py` —— 元数据扩展、严格校验、CommandRegistrationError、CommandType。
- `mewcode/commands/builtin.py` —— 新增/修改 11 条命令注册（保持原有 12 条 + 新增 4 条，部分隐藏）。
- `mewcode/commands/views.py`（新增）—— `/status` `/session` `/memory` `/help` 等只读视图实现的聚合处，避免 `builtin.py` 突破 800 行。
- `mewcode/commands/review.py`（新增）—— `/review` PROMPT 类命令独立成文件，凸显类型差异。
- `mewcode/repl/completer.py`（新增）—— prompt_toolkit `Completer` 实现。
- `mewcode/repl/main_loop.py` —— 接 completer、处理 `prompt_text` 注入、`ctx` 补字段。
- `mewcode/render/renderer.py` —— 新增分组 help / status / session list / memory list / notes 等渲染方法。
- `mewcode/main.py` —— 捕获 `CommandRegistrationError` 单行红字退出。
- `mewcode/sessions/archive.py` —— 已有 `scan_summaries`，复用；新增 `load_by_id(session_id)` 暴露给 `/session resume`。
- `mewcode/memory/manager.py` —— 已有 `refresh / get_combined_index_text`，复用；新增 `list_notes(scope=None)`。

## 核心数据结构

### CommandType（字符串常量类）

```python
class CommandType:
    LOCAL = "local"
    STATEFUL = "stateful"
    PROMPT = "prompt"
```

不引 `enum.StrEnum`（保持 Python 3.10 兼容）；用类常量足以，比较时直接 `cmd.type == CommandType.LOCAL`。

### Command（扩展后的 dataclass）

```python
@dataclass(frozen=True)
class Command:
    name: str
    aliases: tuple[str, ...]
    description: str
    handler: Callable[["CommandContext"], Awaitable["CommandResult"]]
    type: str                       # CommandType.* 之一，必填
    usage: str = ""                 # 一行用法示例
    arg_hint: str = ""              # 参数形式提示
    hidden: bool = False             # /help 是否隐藏
```

### CommandResult（扩展）

```python
@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    prompt_text: str | None = None  # PROMPT 类命令注入的"伪用户输入"
```

### CommandContext（扩展）

在第七、八、九阶段累积的字段基础上，新增两项：

```python
memory_manager: object | None = None   # 第九阶段已加但本阶段命令大量使用，正式落字段
archive: object | None = None          # /session resume / /session new 用
```

### CommandRegistrationError

```python
class CommandRegistrationError(RuntimeError):
    """命令注册期撞名/自反引用。由 main 捕获后单行红字退出。"""
```

### CommandSummary / NoteSummary（views 模块的 dataclass）

`/session list` 和 `/memory list` 输出表格行，渲染层用：

```python
@dataclass(frozen=True)
class _SessionRow:
    session_id: str
    message_count: int
    updated_at: str       # ISO，渲染层负责格式化为相对时间
    title: str
    is_current: bool

@dataclass(frozen=True)
class _NoteRow:
    note_id: str
    scope: str            # "user" | "project"
    category: str
    updated_at: str
    title: str
```

这两个不入公共 API，仅 views 内部用。

## 模块设计

### mewcode.commands.registry

**职责**：管理全局命令表、严格校验、按类型分组导出、解析与分发。

**对外接口**：

- `Command`、`CommandType`、`CommandContext`、`CommandResult`、`CommandRegistrationError` 类
- `COMMANDS: dict[str, Command]` 全局表（按小写 name/alias 索引）
- `register(cmd: Command) -> None` —— 注册一条；任何冲突立即 `raise CommandRegistrationError`
- `unregister_all() -> None` —— 测试辅助；清空全局表
- `commands_by_type() -> dict[str, list[Command]]` —— 返回 `{LOCAL: [...], STATEFUL: [...], PROMPT: [...]}`，仅含 `hidden=False`，每段按 name 升序
- `visible_command_names() -> list[str]` —— Tab 补全和未知命令引导用
- `dispatch(line: str, ctx: CommandContext) -> CommandResult | None`

**校验细节**（`register` 内部）：

1. `cmd.type` 不在三类常量中 → 报错。
2. `cmd.name` 含空格或为空 → 报错。
3. `cmd.name in cmd.aliases` → 报错（自反）。
4. `cmd.name.lower()` 已存在于 `COMMANDS` 且对应 Command 不是同一对象 → 报错。
5. `cmd.aliases` 中任一项已存在于 `COMMANDS` 且对应 Command 不是同一对象 → 报错。

**dispatch 改造**：

- 不以 `/` 开头 → `None`。
- 解析后 `name = parts[0].lower()`。
- 空字符串或纯 `/` → 走未知命令分支，传 `name=""`、`available=visible_command_names()`。
- 命令存在 → 调 handler；返回的 `CommandResult` 透传给 REPL。

### mewcode.commands.views

**职责**：把 LOCAL 类查询命令的 handler 集中到这里，避免 `builtin.py` 继续臃肿。

**包含 handler**：

- `_handle_help` —— 按 `commands_by_type()` 分组打印；调 `renderer.print_command_groups(grouped)`。
- `_handle_status` —— 拼装 6 节字典，调 `renderer.print_status(snapshot)`。
- `_handle_session` —— 子命令分发；`list/current` 在本文件，`new/resume` 在 `builtin.py`（STATEFUL）。

  - 注意：dispatch 不区分 LOCAL/STATEFUL，只看注册表的 type 字段；`/session` 整体注册一次（type 取 `STATEFUL`，因为子命令里有 `new/resume`），但 handler 内部按子命令路由到 view 或 stateful 实现。
- `_handle_memory` —— 同上，子命令分发；`show/list` 在本文件，`refresh` 在 `builtin.py`。

**辅助函数**：

- `_collect_status(ctx) -> dict[str, list[str]]` —— 6 节数据收集
- `_format_relative_time(ts: str) -> str` —— "3 小时前"格式化（Python stdlib + datetime 即可）

### mewcode.commands.review

**职责**：`/review` PROMPT 类命令独立成文件，封装预设 prompt 与拼接逻辑。

**导出**：`_handle_review(ctx) -> CommandResult`

**实现**：

```python
_REVIEW_PROMPT = """请回顾本轮对话里你做的所有改动和操作，逐项检查：
1. 修改是否完成了用户要求的目标？有没有偏题？
...
最后用一句话总结整体风险等级（低 / 中 / 高）和建议的下一步。"""

async def _handle_review(ctx):
    if not ctx.session.messages:
        ctx.renderer.print_info("当前会话尚无内容可回顾。先发起一些对话再用 /review。")
        return CommandResult()
    extra = " ".join(ctx.args).strip()
    text = _REVIEW_PROMPT
    if extra:
        text += f"\n\n本次额外重点关注：{extra}"
    return CommandResult(prompt_text=text)
```

### mewcode.commands.builtin

**职责**：注册全部命令；STATEFUL 类 handler 实现保留在此。

**保留的现有 handler**（行为不变）：

- `_handle_exit` `_handle_clear` `_handle_think` `_handle_provider` `_handle_providers`
- `_handle_plan` `_handle_do`
- `_handle_permissions`（重命名为 `_handle_permission`，子命令不变）
- `_handle_instructions` `_handle_compact`

**新增 STATEFUL handler**：

- `_handle_session_new(ctx)` —— `archive.rotate(session)` + 清空 messages
- `_handle_session_resume(ctx, session_id)` —— `archive.load_by_id` + 替换 session.messages + 调 `rebuild_system_prompt`（如有 memory）
- `_handle_memory_refresh(ctx)` —— `memory_manager.refresh()` 异步等待

**注册顺序**：先 LOCAL（views 引用），再 STATEFUL（builtin 自有），最后 PROMPT（review 引用）。

### mewcode.repl.completer

**职责**：实现 prompt_toolkit `Completer`。

```python
class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            return  # 已进入参数区，不补
        prefix = text[1:].lower()  # 去掉斜杠
        for name in visible_command_names():
            if name.startswith(prefix):
                yield Completion(
                    name,
                    start_position=-len(prefix),
                    display=f"/{name}",
                )
```

**行为**：

- 单匹配：prompt_toolkit 默认在按 Tab 时直接补全。
- 多匹配：默认弹下拉菜单。
- 空 prefix（仅有 `/` 一个字符）：与 spec F7 一致，**不返回候选**（用户按 Tab 没有反应；想看全命令请用 `/help`）。

### mewcode.repl.main_loop

**改动**：

1. 引入 `_make_prompt(session) -> str` 函数（模块顶部或函数内 helper 均可），实现 spec F14 的 PLAN 前缀：
   ```python
   def _make_prompt(session) -> str:
       if getattr(session, "mode", "do") == "plan":
           return "[PLAN] > "
       return "> "
   ```
   原 `PROMPT = "> "` 常量保留为 fallback 或直接删除；调用处改为 `await pt_session.prompt_async(_make_prompt(session))`。
2. `PromptSession(completer=SlashCommandCompleter())`。
3. `CommandContext` 构造时传 `memory_manager`、`archive`。
4. dispatch 返回 `CommandResult` 后：
   - `should_exit` → `return 0`
   - `prompt_text is not None` → 调 `run_turn(session, prompt_text, ...)`，与普通对话同路径。
   - 否则 → `continue`。

### mewcode.render.renderer

**新增方法**：

```python
def print_command_groups(self, grouped: dict[str, list[Command]]) -> None:
    """三段分组的 /help 输出。"""

def print_status(self, snapshot: dict[str, list[str]]) -> None:
    """六节状态仪表盘；snapshot 形如 {"供应商": ["openai/...", ...], ...}。"""

def print_session_list(self, rows: list, current_id: str) -> None:
    """/session list 的表格输出。"""

def print_session_current(self, info: dict) -> None:
    """/session current 的多行输出。"""

def print_note_list(self, rows: list) -> None:
    """/memory list 的表格输出。"""

def print_memory_index(self, text: str) -> None:
    """/memory show 的内容输出（直接打印 + 简单包装）。"""
```

实现都用现有 `self._console.print` + ANSI；不引 rich Table（保持 Windows 兼容）。

### mewcode.main

**改动**：在 `try: register_builtins()` 周围加一层捕获：

```python
try:
    register_builtins()
except CommandRegistrationError as e:
    renderer.print_error("CommandRegistration", str(e))
    return 1
```

注意：现有 `register_builtins()` 调用在 `repl.main_loop.run_repl()` 内（幂等防御），需要把首次注册前移到 `main()`，让 panic 在最早期发生。`run_repl` 中的 `register_builtins()` 调用保持，只是届时 dict 已填好不会再次注册（注册表内部要做"对象相等则跳过"的容忍：见 registry 校验第 4/5 条"对应 Command 不是同一对象"）。

### mewcode.sessions.archive

**新增方法**：`load_by_id(session_id: str) -> RestoredSession | None`，复用现有 `_load_jsonl_file` 逻辑，按精确 id 命中文件。已有 `_select_latest_session` 是按 mtime，本方法按 id；命中后走相同的"坏行跳过 + 孤儿截断 + 长间隔提醒"管线。

如果当前实现里没这个方法，则在第 9 阶段的 `archive.py` 里加一个薄壳调用 `_load_jsonl_file(path)`。

### mewcode.memory.manager

**新增方法**：

```python
def list_notes(self, scope: str | None = None) -> list[NoteSummary]:
    """枚举两层 notes/*.md，按 scope 过滤；返回精简元数据。"""
```

实现：扫两个 `notes_dir`，读每个 `.md` 的 frontmatter，组合成 `NoteSummary(note_id, scope, category, updated_at, title)`。`title` 取笔记内容（去掉 frontmatter 后）的第一行非空文本，截断 60 字符。

## 模块交互

```
main.py
  └── register_builtins()             ← 启动 panic 关
        ├── views.py                  ← LOCAL handlers
        ├── review.py                 ← PROMPT handler
        └── builtin.py                ← STATEFUL handlers + 注册逻辑

main.py → run_repl(...)
  └── PromptSession(completer=SlashCommandCompleter)
        └── completer.py → registry.visible_command_names()

run_repl loop:
  line ──> dispatch ──> handler ──> CommandResult
                                       │
                          should_exit? ─┼─→ return 0
                          prompt_text? ─┼─→ run_turn(session, prompt_text, ...)
                                  否 ──┴─→ continue
```

`/session resume <id>`、`/session new`、`/memory refresh`、`/permission ...` 这些 STATEFUL handler 内部直接操作 `ctx.session` / `ctx.archive` / `ctx.memory_manager` / `ctx.policy`，不需要回 REPL。

## 文件组织

```
mewcode/commands/
├── __init__.py            ← 增 export CommandType / CommandRegistrationError
├── registry.py            ← 改：扩 dataclass + 严格校验 + 类型分组导出
├── builtin.py             ← 改：注册全部命令 + STATEFUL handler
├── views.py               ← 新：LOCAL handler（/help /status /session list/current /memory show/list）
└── review.py              ← 新：PROMPT handler（/review）

mewcode/repl/
├── __init__.py
├── main_loop.py           ← 改：接 completer + prompt_text 分支 + _make_prompt 函数化 PLAN 前缀
└── completer.py           ← 新：SlashCommandCompleter

mewcode/render/
└── renderer.py            ← 改：新增 6 个语义化方法

mewcode/sessions/archive.py ← 改：补 load_by_id
mewcode/memory/manager.py   ← 改：补 list_notes

mewcode/main.py            ← 改：捕获 CommandRegistrationError

tests/
├── test_command_dispatch.py             ← 改：适配新 dataclass 字段
├── test_command_registry.py             ← 新：撞名/自反/类型分组
├── test_command_views.py                ← 新：/status /session list /memory list
├── test_command_review.py               ← 新：/review 三种情形
├── test_completer.py                    ← 新：补全候选

scripts/
└── verify_commands.py                   ← 新：端到端 8 节验证
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 命令类型枚举 | 三常量（LOCAL/STATEFUL/PROMPT） | 与 spec F1 对齐；不引 enum.StrEnum 维持 3.10 兼容 |
| `/exit` `/quit` 归属 | STATEFUL，可见 | 用户必须知道怎么退出；归 STATEFUL 表示"切到退出态" |
| `/permissions` 重命名 | name=`permission`, alias=`permissions` | 单数更短、与"权限"语义对齐；旧名做别名零回归 |
| Tab 补全 | 仅主命令名，仅可见命令 | 子命令补全需要每个子命令族登记 schema；本阶段先做最有用的 70% |
| `/help` 渲染 | 三段分组 + 缩进列表 | 用户一眼分清"查询/操作/对话"；不引 rich Table 避免 Windows 乱码 |
| `/status` 数据来源 | 全部从 ctx 注入对象读 | 零 IO，确保仪表盘秒回 |
| `/review` 拼接 | 预设 prompt + 用户额外要点 | 既保留自检骨架，又允许定制重点 |
| panic 体验 | renderer.print_error + return 1，不打 traceback | 配置错误，traceback 反而干扰 |
| `register_builtins` 幂等 | 同对象再次注册视为 noop | repl 启动期已注册过；run_repl 兜底调用要不报错 |
| views.py 拆分 | 仅拆 LOCAL；STATEFUL 留 builtin | 把"无副作用"集中维护，更易回归 |
| Completer 边界 | 第一个空格之前才补 | 子命令补全留待后续；本阶段防止 `/session resume <id>` 时按 Tab 干扰 |
| `CommandResult.prompt_text` | 字符串字段，REPL 注入 | 让 PROMPT 类命令复用 run_turn 而不在 dispatch 里发请求；保持分层 |
| PLAN 模式前缀 | 仅 PLAN 显示 `[PLAN] > `，DEFAULT 不变 | 解决"忘了在 PLAN 模式直接写改文件请求"的唯一高风险场景；其它状态走 `/status` 不增加视觉噪音 |
| Completer 空 prefix | 不返回候选 | 与 spec F7 自洽；用户想看全表用 `/help` |

## 与 spec F 需求映射

| F | 命中模块 |
|---|---|
| F1 元数据扩展 | registry.Command/CommandType |
| F2 严格冲突检测 | registry.register + CommandRegistrationError |
| F3 大小写不敏感 | registry.dispatch + register（lower） |
| F4 空与未知 | registry.dispatch + renderer.print_unknown_command |
| F5 三类边界 | registry.CommandResult + main_loop.prompt_text 分支 |
| F6 /help 分组 | views._handle_help + renderer.print_command_groups |
| F7 Tab 补全 | repl.completer.SlashCommandCompleter |
| F8 /status | views._handle_status + renderer.print_status |
| F9 /session 族 | views._handle_session + builtin._handle_session_new/resume |
| F10 /memory 族 | views._handle_memory + builtin._handle_memory_refresh + manager.list_notes |
| F11 /permission 别名 | builtin 注册 |
| F12 /review | review._handle_review |
| F13 老命令 hidden | builtin 注册 |
| F14 PLAN prompt 前缀 | repl.main_loop._make_prompt |
| F15 不做项 | 文档约束 |
