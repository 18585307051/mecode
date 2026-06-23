# MewCode 第十阶段验收报告

> 状态：全部通过。
> 本文件按 `docs/10/checklist.md` 记录第十阶段斜杠命令系统实现完成后的实际验证证据。

## 验证环境

- 日期：2026-06-23（周二）
- 平台：Windows 11 + Command Prompt（cmd.exe），VS Code Integrated Terminal
- Python：3.13.9
- 项目根：`e:\AI\vscode_project\mecode`
- 默认 provider：`anthropic` 协议（deepseek-v4-pro），用于真实 LLM 路径的 verify 脚本

## 执行命令

```text
python -m pytest tests/ -q
python scripts/verify_commands.py
python scripts/verify_agent_loop.py
python scripts/verify_round_loop.py
python scripts/verify_compaction.py
python scripts/verify_instructions.py
python scripts/verify_permissions.py
python scripts/verify_plan_mode.py
python scripts/verify_memory.py
```

## 自动验证结果

### pytest 全套（471 通过）

```
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
........................................................................ [ 61%]
........................................................................ [ 76%]
........................................................................ [ 91%]
.......................................                                  [100%]
471 passed in 18.13s
```

- 第十阶段新增 **59 个测试**：
  - `tests/test_command_registry.py`：22 个
  - `tests/test_command_views.py`：19 个
  - `tests/test_command_review.py`：4 个
  - `tests/test_completer.py`：8 个
  - `tests/test_repl_prompt.py`：6 个
- 现有 412 个测试全部继续通过（含修订过的 `tests/test_command_dispatch.py::test_help` 由 `print_command_list` 改为 `print_command_groups`）。

### verify_commands.py 9 节端到端

```
[1] 注册与导入...
    COMMANDS keys 数: 17
    幂等再注册不抛错 ✓
[2] 三类分组覆盖...
    LOCAL: ['help', 'status']
    STATEFUL: ['clear', 'compact', 'do', 'exit', 'memory', 'permission', 'plan', 'session']
    PROMPT: ['review']
    隐藏命令 (think/provider/providers/instructions) 已过滤 ✓
[3] 撞名 panic...
    抛 CommandRegistrationError: 命令注册冲突：'help' 已被 /help 占用 ✓
[4] 大小写不敏感...
    /HELP -> /help handler ✓
[5] /help 三段输出...
    三段标题齐 + /review 在 PROMPT 段 ✓
[6] /status 六节...
    六节: ['会话', '供应商', '权限', '模式', '长期记忆', '项目指令'] ✓
[7] /review 三态...
    空 session 拒绝 ✓
    非空 + 无参 注入预设 5 条要点 ✓
    非空 + 有参 追加额外重点 ✓
[8] Completer 候选...
    单/多匹配/隐藏/参数区/非斜杠/空prefix 全部符合 ✓
[9] PLAN prompt 前缀...
    plan -> '[PLAN] > '；其他 -> '> ' ✓

✓ 命令系统端到端通过
```

### 既有 verify 脚本不退化

- `verify_agent_loop.py`：`✓ Agent Loop 多轮验证通过`
- `verify_round_loop.py`：`✓ 完整闭环验证通过`
- `verify_compaction.py`：`✓ 上下文压缩端到端通过`
- `verify_instructions.py`：`✓ 项目指令端到端通过`
- `verify_permissions.py`：`✓ 权限系统端到端验证全部通过`
- `verify_plan_mode.py`：`✓ Plan Mode 两段式验证全部通过`
- `verify_memory.py`：`✓ 会话恢复与长期记忆端到端通过`

（`verify_mcp.py` 依赖外部 MCP server，本阶段未触动 MCP 路径，沿用第六阶段验证结论，未在本次回归中执行。）

---

## 逐条 checklist 证据

### 实现完整性 5/5

- [x] **CommandType 三常量** — `python -c "from mewcode.commands import CommandType; print(CommandType.LOCAL, CommandType.STATEFUL, CommandType.PROMPT)"` 输出 `local stateful prompt`。
- [x] **Command 八字段** — `tests/test_command_registry.py::test_command_dataclass_fields` 通过。
- [x] **CommandRegistrationError 暴露** — 可从 `mewcode.commands` import；用法见 `tests/test_command_registry.py::test_duplicate_name_raises`。
- [x] **CommandResult.prompt_text** — `tests/test_command_review.py::test_review_default_prompt` 验证非 None 时含五条要点。
- [x] **CommandContext 新增字段** — `archive` 与 `memory_manager` 作关键字传入，`tests/test_command_views.py` 多处用例验证。

### 注册期校验 7/7

- [x] **同名 panic** — `test_duplicate_name_raises`。
- [x] **alias 撞 name panic** — `test_alias_conflict_with_other_name`。
- [x] **alias 撞 alias panic** — `test_alias_conflict_between_aliases`。
- [x] **自反 alias panic** — `test_self_referential_alias`。
- [x] **非法 type panic** — `test_invalid_type_raises`。
- [x] **register_builtins 幂等** — `test_register_builtins_idempotent`（连续两次调用不抛）；同事实在 `verify_commands.py` Section 1 中再现。
- [x] **main 友好退出** — `verify_commands.py` Section 3 直接构造 `register(Command(name="help", ...))` 后捕获 `CommandRegistrationError` 抛出。`main.py` 已加 try/except 单行红字退出（见 `mewcode/main.py`）。

### 解析与分发 4/4

- [x] **大小写不敏感** — `test_case_insensitive`；verify_commands.py Section 4 复测。
- [x] **空白行不触发命令** — REPL 主循环 step 3 早返回（`mewcode/repl/main_loop.py`，第 120 行附近）。
- [x] **纯 `/` 走未知命令分支** — `test_slash_only_unknown`。
- [x] **未知命令引导** — `test_unknown_command_only_lists_visible`（available 列表仅含 visible）。

### /help 分组 3/3

- [x] **三段输出** — `test_help_groups_three_sections`；verify_commands.py Section 5。
- [x] **隐藏命令不出现** — `test_help_excludes_hidden`。
- [x] **`/exit` `/quit` 在 STATEFUL 段** — 同 `test_help_excludes_hidden`，断言 STATEFUL 段含 `exit`。

### /status 仪表盘 2/2

- [x] **六节标题** — `test_status_six_sections`；verify_commands.py Section 6 输出 `['会话','供应商','权限','模式','长期记忆','项目指令']`。
- [x] **未启用降级** — `test_status_handles_disabled_subsystems`：policy/memory/instructions 为 None 时各节显示「未启用」。

### /session 命令族 5/5

- [x] **`/session list`** — `test_session_list`。
- [x] **`/session current`** — `test_session_current`。
- [x] **`/session new` 清空 messages 并 rotate** — `test_session_new_rotates`。
- [x] **`/session resume <id>` 加载历史** — `test_session_resume_loads`。
- [x] **`/session resume <missing>` 不切换** — `test_session_resume_missing_id`。
- 额外覆盖：**前缀匹配命中** — `test_session_resume_prefix_match`。

### /memory 命令族 3/3

- [x] **`/memory show`** — `test_memory_show`；额外 `test_memory_default_is_show` 验证缺省子命令。
- [x] **`/memory list user` 仅列 user 笔记** — `test_memory_list_user_filter`；`test_memory_list_all` 验证不带 scope 时返回全部。
- [x] **`/memory refresh`** — `test_memory_refresh_calls_manager`。

### /permission 主名 + 别名 3/3

- [x] **主名生效** — `test_permission_main_name`。
- [x] **别名兼容** — `test_permission_alias_compat`。
- [x] **/help 仅列主名** — `test_help_lists_only_permission_main_name`。

### /review 提示词 4/4

- [x] **空 session 拒绝** — `test_review_rejects_empty_session`。
- [x] **无参注入预设** — `test_review_default_prompt`（五条要点 + 风险等级关键词全部断言）。
- [x] **有参追加重点** — `test_review_appends_extra` 末尾匹配「本次额外重点关注：重点看 SQL 注入风险」。
- [x] **REPL 注入 run_turn** — `mewcode/repl/main_loop.py` 的 `result.prompt_text is not None → await run_turn(session, result.prompt_text, ...)` 分支已实现，verify_commands.py Section 7 在 dispatch 层验证 prompt_text 字段值。

### Tab 补全 6/6

- [x] **单匹配补全** — `test_single_match`；verify_commands.py Section 8 `/se → ['session']`。
- [x] **多匹配候选含可见命令** — `test_multi_match` 断言 `/p` 候选含 `permission`、`plan`。
- [x] **隐藏命令不在候选** — `test_hidden_excluded`。
- [x] **参数区不补** — `test_no_complete_after_space`。
- [x] **非斜杠不补** — `test_no_complete_for_plain_text`。
- [x] **空 prefix 不补** — `test_no_complete_for_empty_prefix`（修复了 spec F7 与 plan 之间的内部矛盾后，completer.py 显式返回空）。

额外覆盖：

- `test_aliases_not_in_candidates` — 验证别名不出现在候选。
- `test_case_insensitive_prefix` — `/SE` 也能命中 `session`。

### PLAN 模式 prompt 前缀 4/4

- [x] **PLAN 显示前缀** — `test_plan_prefix`：`mode='plan'` 返回 `'[PLAN] > '`。
- [x] **DEFAULT/do 不显示前缀** — `test_do_no_prefix` + `test_default_no_prefix`。
- [x] **mode 字段缺失兜底** — `test_missing_mode_fallback`。
- [x] **prompt 切换实时生效** — `mewcode/repl/main_loop.py` 主循环 `await pt_session.prompt_async(_make_prompt(session))`，每次读输入前重新求值；verify_commands.py Section 9 直接验证函数行为。`test_thinking_does_not_change_prompt` 额外验证 thinking 状态不串扰。

### 集成 3/3

- [x] **现有命令分发不破坏** — `pytest tests/test_command_dispatch.py` 全过（修订一处旧断言 `print_command_list → print_command_groups`）。
- [x] **REPL 启动 panic 友好** — `mewcode/main.py` 的 `try: register_builtins() except CommandRegistrationError ...` 已就绪；verify_commands.py Section 3 直接复现 CommandRegistrationError 抛出。
- [x] **CommandContext 字段透传** — `mewcode/repl/main_loop.py` 第 120-132 行的 `CommandContext(...)` 构造已传入 `archive=archive, memory_manager=memory_manager`；`tests/test_command_views.py::test_status_six_sections` 验证 `/status` 在 ctx 中能取到这些字段。

### 编译与测试 2/2

- [x] **现有测试套全过** — 471 passed。
- [x] **lint/导入无错** — `python -c "import mewcode; from mewcode.commands import CommandType, CommandRegistrationError, register_builtins; from mewcode.repl.completer import SlashCommandCompleter; print('OK')"` 输出 `OK`。

---

## 端到端场景 5/5

1. **场景 1：启动 → /help → /status → /exit** — verify_commands.py Section 5 + 6 + 1 走通；REPL 主循环 `should_exit` 分支已实现。
2. **场景 2：撞名 panic 启动失败** — verify_commands.py Section 3 抛 `CommandRegistrationError: 命令注册冲突：'help' 已被 /help 占用`；main.py 的 try/except 把它转为单行红字退出码 1。
3. **场景 3：/review 走完整 prompt 注入路径** — verify_commands.py Section 7 三态全过；main_loop.py `prompt_text` 分支调 `run_turn(session, result.prompt_text, ...)`，与普通对话同路径。
4. **场景 4：现有 verify 脚本不退化** — 7 个核心 verify 脚本（agent_loop / round_loop / compaction / instructions / permissions / plan_mode / memory）全部通过，输出末尾均为「✓ ... 通过」。
5. **场景 5：新增 verify_commands.py 通过** — 末行 `✓ 命令系统端到端通过`，退出码 0。

---

## 与 spec AC 的对齐

| AC | 状态 | 主要证据 |
|---|---|---|
| AC1 命令元数据扩展 | ✅ | `test_command_dataclass_fields` |
| AC2 别名冲突 panic | ✅ | `test_duplicate_name_raises` |
| AC3 自反 alias panic | ✅ | `test_self_referential_alias` |
| AC4 大小写不敏感 | ✅ | `test_case_insensitive` + verify_commands.py §4 |
| AC5 空输入与未知命令 | ✅ | `test_slash_only_unknown` + `test_unknown_command_only_lists_visible` |
| AC6 三类命令边界 | ✅ | LOCAL `/help` 不改 messages（test_command_views）；STATEFUL `/clear` 改 messages（test_command_dispatch）；PROMPT `/review` 通过 prompt_text 注入（test_command_review） |
| AC7 /help 分组 | ✅ | `test_help_groups_three_sections` + verify_commands.py §5 |
| AC8 Tab 补全 | ✅ | `tests/test_completer.py` 8 用例 + verify_commands.py §8 |
| AC9 /status 六节 | ✅ | `test_status_six_sections` + verify_commands.py §6 |
| AC10 /session 子命令 | ✅ | `tests/test_command_views.py` 5 个 session 用例 |
| AC11 /memory 子命令 | ✅ | `tests/test_command_views.py` 4 个 memory 用例 |
| AC12 /permission 主名+别名 | ✅ | `test_permission_main_name` + `test_permission_alias_compat` + `test_help_lists_only_permission_main_name` |
| AC13 /review 三态 | ✅ | `tests/test_command_review.py` 4 用例 + verify_commands.py §7 |
| AC14 老命令隐藏 | ✅ | `test_help_excludes_hidden` |
| AC15 启动 panic 友好 | ✅ | verify_commands.py §3 + `mewcode/main.py` 已加 try/except |
| AC16 PLAN 模式前缀 | ✅ | `tests/test_repl_prompt.py` 6 用例 + verify_commands.py §9 |
| AC17 不退化 | ✅ | 471/471 + 7 verify 脚本全过 |

---

## 总结

- **代码变更**：8 个文件改造 / 5 个文件新建（含 `mewcode/commands/registry.py` 扩字段与严格校验、`mewcode/commands/views.py` 新增聚合 LOCAL handler、`mewcode/commands/review.py` 新增 PROMPT handler、`mewcode/commands/builtin.py` 全量加 type、`mewcode/repl/completer.py` 新增 Tab 补全、`mewcode/repl/main_loop.py` 加 PLAN 前缀与 prompt_text 分支、`mewcode/sessions/archive.py` 新增 `load_by_id/find_by_prefix/attach`、`mewcode/memory/manager.py` 新增 `list_notes`、`mewcode/main.py` 提前注册 + 捕获 CommandRegistrationError、`mewcode/render/renderer.py` 新增 6 个语义化方法）。
- **测试新增**：5 个新测试文件，共 **59 个新单测**；总测试数 412 → 471。
- **现有功能不退化**：8 个 verify 脚本全部通过。
- **可见命令终态**（11 条 + `/quit` 别名）：
  - LOCAL：`/help` `/status`
  - STATEFUL：`/clear` `/compact` `/do` `/exit` `/memory` `/permission` `/plan` `/session`
  - PROMPT：`/review`
- **隐藏命令**：`/think` `/provider` `/providers` `/instructions`（保留功能、不在 `/help` 中露出）。

第十阶段实现与文档全部对齐，验收通过。
