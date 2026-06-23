# MewCode 第十阶段 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 改 | `mewcode/commands/registry.py` | 扩展 Command dataclass、CommandType、CommandRegistrationError、严格校验、commands_by_type、visible_command_names |
| 改 | `mewcode/commands/__init__.py` | 导出新增类型 |
| 改 | `mewcode/commands/builtin.py` | 命令注册迁移到 type 化、`/permissions`→`/permission`、注册新命令、注入 type/usage/hidden |
| 新 | `mewcode/commands/views.py` | LOCAL handler：`/help` `/status` `/session list/current` `/memory show/list` |
| 新 | `mewcode/commands/review.py` | PROMPT handler：`/review` |
| 改 | `mewcode/render/renderer.py` | 新增 print_command_groups / print_status / print_session_list / print_session_current / print_note_list / print_memory_index |
| 新 | `mewcode/repl/completer.py` | SlashCommandCompleter |
| 改 | `mewcode/repl/main_loop.py` | 接 completer + 处理 prompt_text 分支 + ctx 加 archive/memory_manager 字段 |
| 改 | `mewcode/main.py` | 提前调 register_builtins、捕获 CommandRegistrationError |
| 改 | `mewcode/sessions/archive.py` | 暴露 load_by_id |
| 改 | `mewcode/memory/manager.py` | 暴露 list_notes |
| 改 | `tests/test_command_dispatch.py` | 适配新 dataclass 字段、新 type 默认值 |
| 新 | `tests/test_command_registry.py` | 撞名/自反/类型分组/大小写 |
| 新 | `tests/test_command_views.py` | /help 分组、/status 六节、/session list/current、/memory list/show |
| 新 | `tests/test_command_review.py` | /review 三种情形（无消息/无参/有参） |
| 新 | `tests/test_completer.py` | 单匹配/多匹配/参数区不补/隐藏不补 |
| 新 | `tests/test_repl_prompt.py` | _make_prompt 函数：PLAN 前缀（spec F14 / AC16） |
| 新 | `scripts/verify_commands.py` | 端到端 8 节验证 |
| 新 | `docs/10/acceptance-report.md` | 验收报告（先建空壳） |

---

## T1: registry 扩展数据结构

**文件：** `mewcode/commands/registry.py`
**依赖：** 无

**步骤：**

1. 顶部新增类 `CommandType`，提供 `LOCAL = "local"` / `STATEFUL = "stateful"` / `PROMPT = "prompt"` 三个常量；放一个集合 `_VALID = frozenset({LOCAL, STATEFUL, PROMPT})` 用于校验。
2. 新增 `class CommandRegistrationError(RuntimeError): pass`。
3. 修改 `Command` dataclass：在 handler 之后追加四个字段
   - `type: str`（必填，无默认值）——故置于无默认值字段段；为兼容旧测试，先把 `type` 加在 `handler` 之后。
   - `usage: str = ""`、`arg_hint: str = ""`、`hidden: bool = False`。
4. 修改 `CommandResult`：新增 `prompt_text: str | None = None`。
5. 修改 `CommandContext`：新增 `archive: object | None = None`、`memory_manager: object | None = None`。

**验证：** `python -c "from mewcode.commands.registry import Command, CommandType, CommandResult, CommandRegistrationError; print(CommandType.LOCAL)"` 输出 `local` 不报错。

---

## T2: registry 严格冲突校验 + 类型分组导出

**文件：** `mewcode/commands/registry.py`
**依赖：** T1

**步骤：**

1. 把 `register(cmd)` 改写：
   - 先 `name = cmd.name.lower().strip()`，并断言不空、不含空格。
   - 先校验 `cmd.type in _VALID`，否则 `raise CommandRegistrationError(f"非法命令类型: {cmd.type!r}")`。
   - 自反检查：若 `cmd.name in cmd.aliases`，报错。
   - 收集所有 keys = `[cmd.name] + list(cmd.aliases)`，依次：若 `key.lower() in COMMANDS` 且 `COMMANDS[key.lower()] is not cmd`，报错。
   - 否则把 `name` 与所有 `aliases` 的小写形式都写入 `COMMANDS[key.lower()] = cmd`。
2. 新增 `unregister_all() -> None`：测试辅助，等同 `COMMANDS.clear()`。
3. 新增 `commands_by_type() -> dict[str, list[Command]]`：
   - 去重 set；遍历 `COMMANDS.values()`；按 type 分桶。
   - 每桶按 `cmd.name` 升序；过滤 `hidden=True`。
   - 返回 `{LOCAL: [...], STATEFUL: [...], PROMPT: [...]}`。
4. 新增 `visible_command_names() -> list[str]`：去重后取 `hidden=False` 的 name 集合，返回升序列表。

**验证：** 写一个临时脚本注册两条 name 相同的命令，第二次应抛 `CommandRegistrationError`。

---

## T3: dispatch 大小写不敏感 + 未知命令引导

**文件：** `mewcode/commands/registry.py`
**依赖：** T2

**步骤：**

1. `dispatch` 内部 `name = parts[0].lower()`。
2. 未知命令传给 `print_unknown_command` 的 `available` 改为 `visible_command_names()`。
3. 纯 `/` 分支（`parts == []`）改为 `print_unknown_command("", visible_command_names())`，提示中带 `/help`。
4. 同时把 handler 返回值原样透传（已是 `CommandResult`），不在 dispatch 内消费 `prompt_text`——交给 REPL。

**验证：** `await dispatch("/HELP", ctx)` 命中 `/help` handler。

---

## T4: Renderer 新增语义化方法

**文件：** `mewcode/render/renderer.py`
**依赖：** T1

**步骤：**

1. `print_command_groups(grouped)`：参数为 `{LOCAL: [Command], STATEFUL: [Command], PROMPT: [Command]}`；输出三段，标题分别为 `[bold]查询命令（不影响对话状态）：[/]` / `[bold]操作命令（修改状态）：[/]` / `[bold]对话命令（向 AI 发起请求）：[/]`；每条命令一行 `  /name (别名: ...)  description`，对齐宽度 16 字符。空段不输出。
2. `print_status(snapshot)`：参数 `dict[str, list[str]]`，键为节标题，值为该节的几行字符串；按字典插入顺序输出，节标题 `[bold]## {key}[/]`。
3. `print_session_list(rows, current_id)`：参数 `list[dict]`（dict 含 session_id/message_count/updated_at/title）；输出表头 `ID / 消息 / 最近更新 / 标题`；每行加 `*` 标记 current。
4. `print_session_current(info)`：参数 dict 含 session_id/message_count/estimated_tokens/created_at/updated_at；多行输出。
5. `print_note_list(rows)`：参数 `list[dict]` 含 note_id/scope/category/updated_at/title；输出表头 `ID / 范围 / 类别 / 最近更新 / 标题`。
6. `print_memory_index(text)`：直接 print；空文本时改打"暂无长期记忆。"。

**验证：** 单元测试或手动构造 stub Renderer，调用上述方法不报错。

---

## T5: Tab 补全 SlashCommandCompleter

**文件：** `mewcode/repl/completer.py`（新建）
**依赖：** T2

**步骤：**

1. `from prompt_toolkit.completion import Completer, Completion`。
2. `from mewcode.commands.registry import visible_command_names`。
3. 类 `SlashCommandCompleter(Completer)`，实现 `get_completions(self, document, complete_event)`：
   - `text = document.text_before_cursor`
   - 若 `not text.startswith("/")` → return。
   - 若 `" " in text` → return。
   - `prefix = text[1:].lower()`
   - 遍历 `visible_command_names()`：若 `name.lower().startswith(prefix)`，`yield Completion(name, start_position=-len(prefix), display=f"/{name}")`。

**验证：** `tests/test_completer.py` 用 mock document 验证候选数。

---

## T6: archive.load_by_id

**文件：** `mewcode/sessions/archive.py`
**依赖：** 无

**步骤：**

1. 检查现有 `archive.py` 是否已有 `load_by_id`；若没有，添加：

   ```python
   def load_by_id(self, session_id: str) -> RestoredSession | None:
       """按精确 session_id 加载会话；返回与启动恢复一致的 RestoredSession。"""
       path = self.sessions_dir / f"{session_id}.jsonl"
       if not path.exists():
           return None
       return self._load_jsonl_file(path)
   ```

2. 若 `_load_jsonl_file` 名称不同（实际可能叫 `_load_path` 等），改为对应函数名；若该路径不复用现有恢复管线，则按现有内部辅助拼出 `RestoredSession(messages, bad_lines, truncated_orphan, last_updated_at)`。

**验证：** 单元测试 `tests/test_command_views.py` 中的 `/session resume` 用例命中。

---

## T7: memory_manager.list_notes

**文件：** `mewcode/memory/manager.py`
**依赖：** 无

**步骤：**

1. 新增公开方法：

   ```python
   def list_notes(self, scope: str | None = None) -> list[dict]:
       """枚举两层 notes/*.md，按 scope 过滤。"""
   ```

2. 实现：分别扫 `self._user_notes_dir` 和 `self._project_notes_dir`（如字段名不同，按现有实现替换）；对每个 .md 文件用现有 `notes.read_note` 解析 frontmatter；title 取笔记主体第一行非空、截断 60 字符；返回 `[{"note_id":..., "scope":..., "category":..., "updated_at":..., "title":...}, ...]`，按 `updated_at` 倒序。
3. 若 `scope` 不为 None，过滤后返回。

**验证：** 单测构造两个 fake notes 文件，`list_notes()` 返回 2 条；`list_notes("user")` 返回 1 条。

---

## T8: builtin 全量重写注册（type/usage/hidden）

**文件：** `mewcode/commands/builtin.py`
**依赖：** T1, T2

**步骤：**

1. 把现有 12 条命令的 `register(Command(...))` 全部加上 `type=` 与 `hidden=` 字段：
   - `/exit` `/quit`：`type=STATEFUL`，hidden 默认 False。
   - `/help`：`type=LOCAL`。
   - `/clear`：`type=STATEFUL`。
   - `/think`：`type=STATEFUL`，`hidden=True`。
   - `/providers`：`type=LOCAL`，`hidden=True`。
   - `/provider`：`type=STATEFUL`，`hidden=True`。
   - `/plan`：`type=STATEFUL`。
   - `/do`：`type=STATEFUL`。
   - `/permissions`：name 改为 `"permission"`，aliases 改为 `("permissions",)`，`type=STATEFUL`。
   - `/instructions`：`type=STATEFUL`，`hidden=True`。
   - `/compact`：`type=STATEFUL`。
2. `/help` handler 重写：从 `views._handle_help` 引入；本文件不再实现 help 列表。

**验证：** `register_builtins()` 不抛错；`COMMANDS["permissions"] is COMMANDS["permission"]`。

---

## T9: views._handle_help

**文件：** `mewcode/commands/views.py`（新建）
**依赖：** T2, T4

**步骤：**

1. `from mewcode.commands.registry import commands_by_type, CommandResult`。
2. `async def _handle_help(ctx) -> CommandResult`：
   - `grouped = commands_by_type()`
   - `ctx.renderer.print_command_groups(grouped)`
   - return `CommandResult()`

**验证：** 单测 mock renderer，调用后 `grouped` 含三键。

---

## T10: views._handle_status

**文件：** `mewcode/commands/views.py`
**依赖：** T4, T7

**步骤：**

1. 实现 `_collect_status(ctx) -> dict[str, list[str]]`：
   - **供应商**：`["{name} / {protocol} / {model}"]`，从 `ctx.session.provider` + `ctx.session.current_provider_name` 取。
   - **模式**：`["[PLAN]" if mode=="plan" else "[DEFAULT]", "thinking: on/off"]`。
   - **会话**：从 `ctx.session.archive.session_id`、`len(ctx.session.messages)` 取；若 `ctx.compactor`，调 `ctx.compactor.estimate_messages(ctx.session.messages)` 拿 token；阈值字段名按现有 Compactor 实现取（如 `auto_compact_threshold`），打印剩余 token。无 archive 时打印"未启用"。
   - **权限**：`ctx.policy is None` → "未启用"；否则 `["mode={policy.mode}", f"规则: allow {len(policy.all_allow)} / deny {len(policy.all_deny)}"]`。
   - **记忆**：`ctx.memory_manager is None` → "未启用"；否则用 `manager.list_notes("user")` / `("project")` 计数 + `get_combined_index_text()` 取字节/行数。
   - **项目指令**：`ctx.instructions is None` → "未启用"；否则 `loader.loaded_layers()` 数量 + 合计字节。
2. `_handle_status(ctx)`：调 `_collect_status`，传给 `ctx.renderer.print_status(snapshot)`，return `CommandResult()`。

**验证：** 在 stub ctx 下 `print_status` 收到 6 节。

---

## T11: views._handle_session（list / current 子命令）

**文件：** `mewcode/commands/views.py`
**依赖：** T4, T6

**步骤：**

1. `async def _handle_session(ctx) -> CommandResult`：
   - 子命令默认 `list`。
   - `list` → 调 `ctx.archive.scan_summaries()`（已存在），转换为渲染层 row dict，调 `print_session_list(rows, current_id=ctx.session.archive.session_id)`。仅取最近 10 条。
   - `current` → 拼装 dict 调 `print_session_current`。
   - `new` / `resume` → 转给 `mewcode.commands.builtin._handle_session_new` / `_handle_session_resume`（避免循环 import：用局部 import）。
   - 未知子命令 → `print_info("未知子命令")` + 返回。

**验证：** /session list 在仅有 1 个会话文件时输出 1 行。

---

## T12: builtin._handle_session_new / _handle_session_resume

**文件：** `mewcode/commands/builtin.py`
**依赖：** T6, T11

**步骤：**

1. `_handle_session_new(ctx)`：
   - `if ctx.archive is None`: print_info "会话存档未启用" + return。
   - `ctx.archive.rotate(ctx.session)` —— 已有方法。
   - `ctx.session.messages.clear()`
   - `print_info(f"已切换到新会话: {ctx.session.archive.session_id}")`
2. `_handle_session_resume(ctx)`：
   - 取 `ctx.args[1:]`（第一个 arg 是子命令 `resume`）；空 → 提示用法；多于 1 个 → 取首个。
   - `target_id = ctx.args[1]`。如果不存在，先在 `archive.scan_summaries()` 中找前缀匹配；多匹配 → 报"id 模糊匹配多于 1 条"。
   - `restored = ctx.archive.load_by_id(target_id)`；None → 提示 "找不到会话: ..."。
   - 替换 `ctx.session.messages = restored.messages`、`ctx.session.archive.session_id = target_id`、`ctx.session.archive.session_path = ...`（按现有实现字段命名）。
   - 若 `ctx.rebuild_system_prompt` 可调用 → 触发一次（兼容 memory hash）。
   - 打印恢复横幅（复用现有 `print_info`）。

**验证：** /session resume 已存在 id 后，session.messages 等于 JSONL 内消息。

---

## T13: views._handle_memory（show / list 子命令）

**文件：** `mewcode/commands/views.py`
**依赖：** T4, T7

**步骤：**

1. `async def _handle_memory(ctx)`：
   - 子命令默认 `show`。
   - `show` → `text = ctx.memory_manager.get_combined_index_text()`；调 `print_memory_index(text)`。
   - `list` → 取第二参数为 scope 过滤 (`user` / `project` / None)；调 `manager.list_notes(scope)`；用 `print_note_list(rows)`。
   - `refresh` → 转 `_handle_memory_refresh`（builtin）。
2. `manager` 为 None 时 print_info "记忆系统未启用"。

**验证：** /memory list user 仅返回 user scope 的笔记。

---

## T14: builtin._handle_memory_refresh + 注册新命令

**文件：** `mewcode/commands/builtin.py`
**依赖：** T7, T13

**步骤：**

1. `async def _handle_memory_refresh(ctx)`：
   - `if ctx.memory_manager is None`: print_info "记忆系统未启用" + return。
   - `await ctx.memory_manager.refresh()`（refresh 已存在；如非 async 改为 sync 调用）。
   - 若有 `ctx.rebuild_system_prompt`，传 `memory=manager.get_combined_index_text()` 让其按 hash 决定是否重建。
   - print_info "已刷新长期记忆索引"。
2. 注册新命令：
   - `/session`：handler=`views._handle_session`，type=STATEFUL（因为含 new/resume 子命令），usage="/session [list|current|new|resume <id>]"。
   - `/memory`：handler=`views._handle_memory`，type=STATEFUL，usage="/memory [show|list [user|project]|refresh]"。
   - `/status`：handler=`views._handle_status`，type=LOCAL，usage="/status"。
   - `/review`：handler=`review._handle_review`，type=PROMPT，usage="/review [侧重点]"。

**验证：** `register_builtins()` 后 `COMMANDS["status"].type == "local"`，`COMMANDS["review"].type == "prompt"`。

---

## T15: review._handle_review

**文件：** `mewcode/commands/review.py`（新建）
**依赖：** T1

**步骤：**

1. 文件顶部定义 `_REVIEW_PROMPT` 常量，按 spec F12 五条要点全文。
2. `async def _handle_review(ctx) -> CommandResult`：
   - 若 `not ctx.session.messages`: `ctx.renderer.print_info("当前会话尚无内容可回顾。先发起一些对话再用 /review。")`；return `CommandResult()`。
   - `extra = " ".join(ctx.args).strip()`
   - `text = _REVIEW_PROMPT + (f"\n\n本次额外重点关注：{extra}" if extra else "")`
   - `return CommandResult(prompt_text=text)`

**验证：** session 为空时 prompt_text 为 None；非空 + 无参时含 "1. 修改是否完成"；非空 + 有参时含 "本次额外重点关注：..."。

---

## T16: REPL 接 completer + prompt_text 分支 + PLAN 前缀

**文件：** `mewcode/repl/main_loop.py`
**依赖：** T5, T15

**步骤：**

1. import：`from mewcode.repl.completer import SlashCommandCompleter`。
2. 文件顶部 / 函数内新增 helper（实现 spec F14）：

   ```python
   def _make_prompt(session) -> str:
       """根据 session.mode 动态生成 PROMPT 字符串。

       PLAN 模式显示 [PLAN] > 前缀；其他模式（default/do）保持 > 不变。
       仅 mode 一项参与；thinking / permission yolo 等不进 PROMPT。
       """
       if getattr(session, "mode", "do") == "plan":
           return "[PLAN] > "
       return "> "
   ```

3. `pt_session = PromptSession(completer=SlashCommandCompleter())`。
4. 把 `await pt_session.prompt_async(PROMPT)` 改为 `await pt_session.prompt_async(_make_prompt(session))`。原 `PROMPT = "> "` 常量可保留或删除（不再使用）。
5. `CommandContext(...)` 构造改为传 `archive=archive, memory_manager=memory_manager`（这两参数 run_repl 已经接收，只是没传给 ctx）。
6. dispatch 返回值处理：

   ```python
   if result is not None:
       if result.should_exit:
           return 0
       if result.prompt_text is not None:
           # PROMPT 类命令：把预设文本当用户输入送进 run_turn
           try:
               await run_turn(
                   session, result.prompt_text, renderer,
                   registry=registry, confirmer=confirmer, sandbox=sandbox,
                   policy=policy, asker=asker, compactor=compactor,
                   memory_manager=memory_manager,
                   rebuild_system_prompt=rebuild_system_prompt,
               )
           except (KeyboardInterrupt, asyncio.CancelledError):
               pass
       continue
   ```

**验证：** `verify_commands.py` 中 /review 走完整链路；手动验证 `/plan` 后下一行 PROMPT 显示 `[PLAN] > `。

---

## T17: main 捕获 CommandRegistrationError

**文件：** `mewcode/main.py`
**依赖：** T2

**步骤：**

1. import `register_builtins, CommandRegistrationError` from `mewcode.commands`（从 `__init__.py` 重新 export）。
2. 在 `run_repl` 调用之前，提前调一次 `register_builtins()`，并 try/except：

   ```python
   try:
       register_builtins()
   except CommandRegistrationError as e:
       renderer.print_error("CommandRegistration", str(e))
       return 1
   ```

3. `register_builtins` 内部的注册需要做幂等：第二次调用时，所有 Command 对象与 COMMANDS 中已有对象相同 → 跳过，不抛错。在 `register()` 校验中已有"对应 Command 不是同一对象"分支处理。

**验证：** 启动 mewcode 不报错；构造一个故意撞名的注册脚本时退出码 1 + 单行红字。

---

## T18: 测试套补齐

**文件：** 多文件，新建为主
**依赖：** T1-T17

**步骤：**

1. `tests/test_command_dispatch.py` 修订：所有现有 `Command(...)` 构造加上 `type=`；断言不变。
2. 新建 `tests/test_command_registry.py`：
   - 测撞名抛 `CommandRegistrationError`。
   - 测 alias 撞别名抛错。
   - 测 alias 撞自身 name 抛错（自反）。
   - 测 `commands_by_type()` 三段都返回非空。
   - 测 `visible_command_names()` 不含 hidden 命令。
3. 新建 `tests/test_command_views.py`：
   - `_StubArchive` 提供 `scan_summaries` / `load_by_id` / `rotate` / `session_id`。
   - `_StubMemoryManager` 提供 `get_combined_index_text` / `list_notes` / `refresh`。
   - 测 `/help` 调用 `print_command_groups` 且参数三键齐。
   - 测 `/status` 调用 `print_status` 且 snapshot 含 6 节。
   - 测 `/session list` 调用 `print_session_list`。
   - 测 `/session current` 调用 `print_session_current`。
   - 测 `/session new` messages 清空 + archive.rotate 被调。
   - 测 `/session resume <id>` 命中存在的 id → messages 替换。
   - 测 `/memory show` 调用 `print_memory_index`。
   - 测 `/memory list user` 调用 `print_note_list` 且 rows 仅 user scope。
   - 测 `/memory refresh` 调 `manager.refresh`。
4. 新建 `tests/test_command_review.py`：
   - 空 session → renderer.print_info 含 "尚无内容"，prompt_text 为 None。
   - 非空 session 无参 → prompt_text 含 "1. 修改是否完成"。
   - 非空 session + `/review SQL 注入` → prompt_text 末尾含 "本次额外重点关注：SQL 注入"。
5. 新建 `tests/test_completer.py`：
   - `SlashCommandCompleter` 给 `/se` → 候选含 `session`。
   - 给 `/p` → 候选含 `permission` / `plan`。
   - 给 `/help xxx` → 候选为空（已有空格）。
   - 给 `xxx` → 候选为空（无斜杠）。
   - 隐藏命令（如 `/think`）不在候选里。
   - 给 `/`（仅斜杠空 prefix）→ 候选为空（spec F7）。
6. 新建 `tests/test_repl_prompt.py`（spec F14 / AC16）：
   - `_make_prompt(session)`：`session.mode = "do"` → 返回 `"> "`。
   - `session.mode = "plan"` → 返回 `"[PLAN] > "`。
   - `session.mode = "default"` → 返回 `"> "`（与 do 等价）。
   - 缺失 `mode` 属性 → 返回 `"> "`（兜底 default）。

**验证：** `pytest tests/test_command_*.py tests/test_completer.py -q` 全绿。

---

## T19: scripts/verify_commands.py

**文件：** `scripts/verify_commands.py`（新建）
**依赖：** T18

**步骤：** 端到端 8 节验证：

1. import + `register_builtins()` 成功。
2. `commands_by_type()` 三段均含至少一项；可见命令含 `/help /status /session /memory /permission /clear /plan /do /compact /review /exit`。
3. 撞名 panic：构造一个临时 Command(name="help", type=LOCAL,...)，应抛 `CommandRegistrationError`。
4. 大小写：dispatch `/HELP` 走 help handler。
5. /help 输出三段标题包含"查询命令"/"操作命令"/"对话命令"。
6. /status 输出 6 节（用 fake ctx 注入 stub 子系统）。
7. /review 三种情形 prompt_text 形态正确。
8. Completer 候选含 `session` 不含 `think`；`/` 空 prefix 返回空。
9. `_make_prompt` 在 `session.mode = "plan"` 时返回 `[PLAN] > `，其他模式返回 `> `（spec F14 / AC16）。

**验证：** `python scripts/verify_commands.py`，最后输出 `✓ 命令系统端到端通过`。

---

## T20: 回归全部 verify

**文件：** 无（执行）
**依赖：** T19

**步骤：** 依次执行：

1. `pytest tests/ -q`
2. `python scripts/verify_agent_loop.py`
3. `python scripts/verify_round_loop.py`
4. `python scripts/verify_compaction.py`
5. `python scripts/verify_instructions.py`
6. `python scripts/verify_permissions.py`
7. `python scripts/verify_plan_mode.py`
8. `python scripts/verify_mcp.py`
9. `python scripts/verify_memory.py`
10. `python scripts/verify_commands.py`

**验证：** 全部退出码 0。

---

## T21: docs/10/acceptance-report.md

**文件：** `docs/10/acceptance-report.md`（新建/补全）
**依赖：** T20

**步骤：** 按 mew-spec 的"验收报告"模板填写实际输出，标出每条 AC 的证据（命令输出 / 测试 ID / 文件路径）。

**验证：** 检查文件含"通过 (16/16)"或类似全绿摘要。

---

## 执行顺序

```
T1 → T2 → T3 → T4
                ↘
                  T5、T6、T7（可并行）
                                ↘
                                  T8 → T9 → T10 → T11 → T12 → T13 → T14
                                                                       ↘
                                                                         T15 → T16 → T17
                                                                                       ↘
                                                                                         T18 → T19 → T20 → T21
```

LOCAL/STATEFUL 命令注册有先后依赖（T8 必须在 T9-T14 之前完成 type 字段补全），因为 builtin 的 `/help` handler 引用 views，views 又用 registry 的分组接口；这条链条按上面顺序排列即可。
