# MewCode 第十阶段 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] **CommandType 三常量**——`from mewcode.commands import CommandType`，确认存在 `LOCAL/STATEFUL/PROMPT` 三个值。验证：`python -c "from mewcode.commands import CommandType; print(CommandType.LOCAL, CommandType.STATEFUL, CommandType.PROMPT)"` 输出 `local stateful prompt`。
- [ ] **Command 八字段**——`Command` dataclass 含 `name / aliases / description / handler / type / usage / arg_hint / hidden`。验证：`pytest tests/test_command_registry.py::test_command_dataclass_fields` 通过。
- [ ] **CommandRegistrationError 暴露**——可从 `mewcode.commands` 直接 import。验证：`python -c "from mewcode.commands import CommandRegistrationError; raise CommandRegistrationError('x')"` 抛出该异常。
- [ ] **CommandResult.prompt_text**——存在并默认为 `None`。验证：`python -c "from mewcode.commands import CommandResult; assert CommandResult().prompt_text is None"`。
- [ ] **CommandContext 新增字段**——`archive` 与 `memory_manager` 可作关键字参数构造。验证：`pytest tests/test_command_dispatch.py` 全绿。

## 注册期校验

- [ ] **同名 panic**——重复注册同 name 抛 `CommandRegistrationError`。验证：`pytest tests/test_command_registry.py::test_duplicate_name_raises` 通过。
- [ ] **alias 撞 name panic**——某条命令 alias 与他人 name 重合抛错。验证：`pytest tests/test_command_registry.py::test_alias_conflict_with_other_name` 通过。
- [ ] **alias 撞 alias panic**——两条命令 alias 互撞抛错。验证：`pytest tests/test_command_registry.py::test_alias_conflict_between_aliases` 通过。
- [ ] **自反 alias panic**——`Command(name="x", aliases=("x",), ...)` 抛错。验证：`pytest tests/test_command_registry.py::test_self_referential_alias` 通过。
- [ ] **非法 type panic**——`type="weird"` 抛错。验证：`pytest tests/test_command_registry.py::test_invalid_type_raises` 通过。
- [ ] **register_builtins 幂等**——连续两次调用不抛错。验证：`python -c "from mewcode.commands import register_builtins; register_builtins(); register_builtins(); print('OK')"` 输出 OK。
- [ ] **main 友好退出**——构造一个故意撞名的 builtin 注册流程，启动后退出码 1，stderr 单行红字、无 traceback。验证：手动跑临时脚本 `python -c "from mewcode.commands.registry import COMMANDS, Command, register, CommandType; from mewcode.commands.builtin import register_builtins; register_builtins(); register(Command(name='help', aliases=(), description='dup', handler=lambda c:None, type=CommandType.LOCAL))"`，预期 `CommandRegistrationError`。

## 解析与分发

- [ ] **大小写不敏感**——`/HELP`、`/Help`、`/help` 都能命中。验证：`pytest tests/test_command_dispatch.py::test_case_insensitive` 通过。
- [ ] **空白行不触发命令**——`""`、`"   "` 在 REPL 上层早返回，dispatch 不被调用。验证：`pytest tests/test_command_dispatch.py::test_blank_skipped`（如已有）或观察 REPL 行为。
- [ ] **纯 `/` 走未知命令分支**——dispatch("/", ctx) 调 `print_unknown_command("", visible_names)`。验证：`pytest tests/test_command_dispatch.py::test_slash_only_unknown` 通过。
- [ ] **未知命令引导**——`/foobar` 触发 `print_unknown_command("foobar", available)`，available 列表仅含 `hidden=False` 的命令。验证：`pytest tests/test_command_dispatch.py::test_unknown_command_only_lists_visible`。

## /help 分组

- [ ] **三段输出**——`/help` 调用 `print_command_groups(grouped)`，grouped 含 `LOCAL/STATEFUL/PROMPT` 三键。验证：`pytest tests/test_command_views.py::test_help_groups_three_sections`。
- [ ] **隐藏命令不出现**——grouped 任何一段不含 `/think` `/provider` `/providers` `/instructions` `/permissions`。验证：`pytest tests/test_command_views.py::test_help_excludes_hidden`。
- [ ] **`/exit` `/quit` 在 STATEFUL 段**——验证：同上测试断言。

## /status 仪表盘

- [ ] **六节标题**——`/status` 输出含"供应商""模式""会话""权限""记忆""项目指令"。验证：`pytest tests/test_command_views.py::test_status_six_sections`。
- [ ] **未启用降级**——某子系统注入 None 时该节显示"未启用"，不抛错。验证：`pytest tests/test_command_views.py::test_status_handles_disabled_subsystems`。

## /session 命令族

- [ ] **`/session list` 调 archive.scan_summaries**——验证：`pytest tests/test_command_views.py::test_session_list`。
- [ ] **`/session current` 输出当前 id**——验证：`pytest tests/test_command_views.py::test_session_current`。
- [ ] **`/session new` 清空 messages 并 rotate**——验证：`pytest tests/test_command_views.py::test_session_new_rotates`。
- [ ] **`/session resume <id>` 加载历史**——验证：`pytest tests/test_command_views.py::test_session_resume_loads`。
- [ ] **`/session resume <missing>` 不切换**——验证：`pytest tests/test_command_views.py::test_session_resume_missing_id`。

## /memory 命令族

- [ ] **`/memory show` 输出注入文本**——`print_memory_index` 收到的内容等于 `manager.get_combined_index_text()` 返回值。验证：`pytest tests/test_command_views.py::test_memory_show`。
- [ ] **`/memory list user` 仅列 user 笔记**——验证：`pytest tests/test_command_views.py::test_memory_list_user_filter`。
- [ ] **`/memory refresh` 调 manager.refresh**——验证：`pytest tests/test_command_views.py::test_memory_refresh_calls_manager`。

## /permission 主名 + 别名

- [ ] **主名生效**——`/permission show` 命中 handler。验证：`pytest tests/test_command_views.py::test_permission_main_name`。
- [ ] **别名兼容**——`/permissions show` 同样命中。验证：`pytest tests/test_command_views.py::test_permission_alias_compat`。
- [ ] **/help 仅列主名**——`/help` 输出含 `/permission`、不含 `/permissions` 单独行。验证：`pytest tests/test_command_views.py::test_help_lists_only_main_name`。

## /review 提示词

- [ ] **空 session 拒绝**——`session.messages == []` 时 `/review` 不返回 prompt_text，仅 print_info。验证：`pytest tests/test_command_review.py::test_review_rejects_empty_session`。
- [ ] **无参注入预设**——非空 session 时 `/review` 返回的 `prompt_text` 含 "1. 修改是否完成了用户要求的目标"。验证：`pytest tests/test_command_review.py::test_review_default_prompt`。
- [ ] **有参追加重点**——`/review 重点看 SQL` 时 `prompt_text` 末尾含 "本次额外重点关注：重点看 SQL"。验证：`pytest tests/test_command_review.py::test_review_appends_extra`。
- [ ] **REPL 注入 run_turn**——观察 `verify_commands.py` 的输出：`/review` 后会触发一次模拟的 run_turn 调用并把 prompt_text 作为 user 输入。验证：`python scripts/verify_commands.py` 含"/review prompt 注入 run_turn 成功"。

## Tab 补全

- [ ] **单匹配补全**——`/se<TAB>` 候选只 `session`。验证：`pytest tests/test_completer.py::test_single_match`。
- [ ] **多匹配候选含可见命令**——`/p<TAB>` 候选包含 `permission` `plan`。验证：`pytest tests/test_completer.py::test_multi_match`。
- [ ] **隐藏命令不在候选**——`/t<TAB>` 候选不含 `think`。验证：`pytest tests/test_completer.py::test_hidden_excluded`。
- [ ] **参数区不补**——已有空格的输入 `/help xxx<TAB>` 不返回候选。验证：`pytest tests/test_completer.py::test_no_complete_after_space`。
- [ ] **非斜杠不补**——普通文本 `xxx<TAB>` 不返回候选。验证：`pytest tests/test_completer.py::test_no_complete_for_plain_text`。
- [ ] **空 prefix 不补**——只输入 `/` 时按 Tab 候选为空（spec F7）。验证：`pytest tests/test_completer.py::test_no_complete_for_empty_prefix`。

## PLAN 模式 prompt 前缀

- [ ] **PLAN 显示前缀**——`session.mode = "plan"` 时 `_make_prompt(session)` 返回 `"[PLAN] > "`。验证：`pytest tests/test_repl_prompt.py::test_plan_prefix`。
- [ ] **DEFAULT/do 不显示前缀**——`session.mode in {"do", "default"}` 时返回 `"> "`。验证：`pytest tests/test_repl_prompt.py::test_default_no_prefix`。
- [ ] **mode 字段缺失兜底**——session 上没有 `mode` 属性时返回 `"> "`。验证：`pytest tests/test_repl_prompt.py::test_missing_mode_fallback`。
- [ ] **prompt 切换实时生效**——`/plan` 后下一行 PROMPT 立即变成 `[PLAN] > `；`/do` 后立即恢复 `> `。验证：手动跑 `python -m mewcode` 进入 REPL 后输入 `/plan` 观察 PROMPT 变化；或 `verify_commands.py` 的"PLAN 前缀"小节通过。

## 集成

- [ ] **现有命令分发不破坏**——`pytest tests/test_command_dispatch.py` 全绿。
- [ ] **REPL 启动 panic 友好**——见上方"main 友好退出"。
- [ ] **CommandContext 字段透传**——`run_repl` 启动时 `archive` 和 `memory_manager` 传入构造函数；`/status` 在 ctx 中能取到这些字段。验证：`pytest tests/test_command_views.py::test_status_reads_from_ctx`。

## 编译与测试

- [ ] **现有测试套全过**——`pytest tests/ -q` 全绿（含本阶段新增 5 个测试文件）。
- [ ] **lint/导入无错**——`python -c "import mewcode; from mewcode.commands import CommandType, CommandRegistrationError, register_builtins; from mewcode.repl.completer import SlashCommandCompleter; print('OK')"`。

## 端到端场景

- [ ] **场景 1：用户启动 → /help → 看到三段分组 → 选 /status → 看到六节面板 → /exit**

  验证：手动跑或在 `verify_commands.py` 中模拟该序列，最后输出干净退出。

- [ ] **场景 2：撞名 panic 启动失败**

  构造一个临时 patch 让 `register_builtins` 注册两条 name="help" 的命令，启动后立即看到 `[CommandRegistration]` 红字单行 + 退出码 1。验证：`python scripts/verify_commands.py` 中"撞名 panic"小节通过。

- [ ] **场景 3：/review 走完整 prompt 注入路径**

  非空 session 调 `/review`，看到 run_turn 收到的 user 输入文本含五条要点。验证：`verify_commands.py` 中"PROMPT 类命令注入对话"小节通过。

- [ ] **场景 4：现有 9 条 verify 脚本全部通过**

  `python scripts/verify_agent_loop.py` `verify_round_loop` `verify_compaction` `verify_instructions` `verify_permissions` `verify_plan_mode` `verify_mcp` `verify_memory` 退出码均 0。

- [ ] **场景 5：新增 verify_commands.py 通过**

  `python scripts/verify_commands.py` 退出码 0，最末一行 `✓ 命令系统端到端通过`。
