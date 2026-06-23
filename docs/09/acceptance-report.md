# MewCode 第九阶段验收报告

> 状态：全部通过。
> 本文件按 `docs/09/checklist.md` 记录第九阶段实现完成后的实际验证证据。

## 验证环境

- 日期：2026-06-23（周二）
- 平台：Windows 11 + Command Prompt（cmd.exe），VS Code Integrated Terminal
- Python：3.13.9
- 项目根：`e:\AI\vscode_project\mecode`
- 默认 provider：`anthropic` 协议（deepseek-v4-pro），用于真实 LLM 路径的 verify 脚本

## 执行命令

```text
python -m compileall mewcode -q
python -c "import mewcode; from mewcode.sessions import SessionArchive; from mewcode.memory import MemoryManager; print('ok')"
python -m pytest tests/ -q
python scripts/verify_memory.py
python scripts/verify_instructions.py
python scripts/verify_compaction.py
python scripts/verify_round_loop.py
python scripts/verify_agent_loop.py
python scripts/verify_permissions.py
python scripts/verify_plan_mode.py
```

## 自动验证结果

### 编译与基础检查

- [x] **C1 包可导入** — `python -c "import mewcode; ..."` 输出 `ok`。
- [x] **C2 全部源文件语法合法** — `python -m compileall mewcode -q` 退出码 0。
- [x] **C3 第九阶段新增模块可导入** — `from mewcode.sessions import SessionArchive` 与 `from mewcode.memory import MemoryManager` 均成功。
- [x] **C4 全量单测通过** — `pytest tests/ -q` 输出 `412 passed in 15.31s`。
- [x] **C5 命令行入口可启动** — `main.py` / `_amain` 装配阶段不抛异常；`verify_round_loop.py` 与 `verify_agent_loop.py` 通过真实 REPL 等效路径验证。

### 项目指令优先级与 include（spec F1 / F2）

- [x] **AC1 指令优先级** — `tests/test_instructions_include.py::test_priority_local_project_user`、`tests/test_instructions_loader.py::test_three_layers_order`、以及 `verify_memory.py` 第 1 节均断言「本地 → 项目 → 用户」顺序。
- [x] **AC2 include 展开** — `test_include_expands_with_markers` + `verify_memory.py` 验证文件包含 `<!-- begin include: docs/extra.md -->` 与 `<!-- end include: docs/extra.md -->`。
- [x] **AC3 include 防环** — `test_include_cycle_does_not_loop` 验证 A↔B 互相 include 时不会死循环，仅展开一次并 warning。
- [x] **AC4 include 深度限制** — `test_include_depth_limit` 验证第 4 层被拒绝，且 `L4-SHOULD-NOT-APPEAR` 不出现在结果中。
- [x] **AC5 include 越界拦截** — `test_include_outside_project_rejected` 验证 `@include ../outside.md` 被拒绝并 warning。
- [x] **额外：include 非 UTF-8 容错** — `test_include_non_utf8_skipped` 通过。
- [x] **额外：include 缺失文件容错** — `test_include_missing_file_skipped` 通过。

### 会话 JSONL 存档与恢复（spec F3-F11）

- [x] **AC6 会话 JSONL 追加** — `test_append_messages_creates_jsonl` 通过；`Session._persist_last` 在三类 append 后写盘，`verify_memory.py` 第 2 节也观察到。
- [x] **AC7 坏行跳过** — `test_restore_skips_bad_lines`、`verify_memory.py`：`坏行 1` 计数正确。
- [x] **AC8 孤儿工具调用截断** — `test_restore_truncates_orphan_tool_use`、`test_restore_truncates_orphan_tool_result` 均通过；`verify_memory.py`：`孤儿截断 True`。
- [x] **AC9 不维护 meta 文件** — `test_append_messages_creates_jsonl` 显式断言 `archive.directory.iterdir()` 中只含 `<session_id>.jsonl`。
- [x] **AC10 自动恢复最近会话** — `test_restore_latest_picks_most_recent` 通过。
- [x] **AC11 过期清理** — `test_cleanup_expired` 验证 60 天前消息被清理、新会话保留。
- [x] **AC12 长间隔提醒** — `test_gap_reminder_inserted_once` 验证首次插入并写回 JSONL，二次恢复不再重复。
- [x] **AC13 恢复后超限压缩** — `Session.restored_needs_compaction_check` 标记由 `SessionArchive.attach` 设置；`run_turn` 中先调 `compactor.before_request` 再消费标记；与第八阶段 compaction 行为兼容（`verify_compaction.py` 通过）。
- [x] **额外：会话 ID 格式** — `test_new_session_id_format` 验证 `YYYYMMDD-HHMMSS-xxxx` 且 10 次随机不重复。

### 自动笔记与记忆索引（spec F12-F18）

- [x] **AC14 自动笔记触发** — `tests/test_memory_agent_integration.py::test_natural_stop_schedules_memory_update` 验证 `mm.calls == 1`。
- [x] **AC15 非自然停止不更新笔记** — `test_provider_error_does_not_schedule` 验证 Provider 错误时 `mm.calls == 0`；`test_tool_use_round_does_not_schedule_until_natural_stop` 验证带工具调用的中间轮不调度。
- [x] **AC16 笔记 frontmatter** — `test_note_frontmatter_roundtrip`、`test_note_to_markdown_contains_frontmatter_keys` 通过；`verify_memory.py` 写入并读取笔记后字段保持一致。
- [x] **AC17 用户级/项目级分开存** — `test_create_writes_to_correct_scope_project`、`test_create_writes_to_correct_scope_user`、`verify_memory.py`：用户级 1 条 / 项目级 1 条；scope 默认按 category 修正。
- [x] **AC18 index 行数 / 字节限制** — `test_index_line_limit`（200 行）、`test_index_byte_limit_under_25k`（25KB）通过。
- [x] **AC18 优先级裁剪** — `test_index_priority_keeps_correction_first` 验证大量 reference 也不会挤掉 correction。
- [x] **AC19 请求前注入记忆** — `test_load_context_includes_both_scopes`、`verify_memory.py` 第 4 节均验证 `system_prompt` 含 `## 长期记忆` + `项目记忆` + `用户记忆` 段。
- [x] **AC20 hash 不变不重建 system_prompt** — `test_refresh_only_when_hash_changes` 验证第二次 refresh 不再调用 rebuild。
- [x] **额外：路径安全** — `test_delete_note_safe_blocks_traversal` 验证路径穿越被拒绝。
- [x] **额外：坏笔记跳过** — `test_list_notes_skips_broken` 通过。

### 不退化（spec AC21）

- [x] **AC21 全量单测** — `pytest tests/ -q`：**412 passed**。
- [x] **AC21 既有 verify 脚本** —
  - [x] `verify_memory.py` ✓（新增）
  - [x] `verify_instructions.py` ✓（修复了第七阶段断言以匹配第九阶段新顺序）
  - [x] `verify_compaction.py` ✓
  - [x] `verify_round_loop.py` ✓（真实 LLM 多轮）
  - [x] `verify_agent_loop.py` ✓（真实 LLM 多轮 + 工具）
  - [x] `verify_permissions.py` ✓（真实 LLM）
  - [x] `verify_plan_mode.py` ✓（真实 LLM Plan/Do 切换）

## 端到端验证

```text
$ python scripts/verify_memory.py

==== 1. 项目指令三层优先级 + @include ====
✓ 三层优先级与 @include 工作正常

==== 2. 会话存档与恢复 ====
✓ 恢复成功，坏行 1，孤儿截断 True，消息 2 条

==== 3. 自动笔记与 index ====
✓ 笔记已写入：用户级 1 条，项目级 1 条
✓ 长期记忆段拼接正确

==== 4. system_prompt 注入 ====
✓ build_system_prompt 同时注入指令与长期记忆

✓ 会话恢复与长期记忆端到端通过
```

## 未通过项

无。

## 主要变更摘要

### 新增模块

- `mewcode/sessions/__init__.py`、`codec.py`、`archive.py`：JSONL 编解码、会话目录、扫描、恢复、清理。
- `mewcode/memory/__init__.py`、`notes.py`、`index.py`、`updater.py`、`manager.py`：四类自动笔记、index 受限重建、LLM 更新建议解析、运行时注入与后台调度。

### 改造模块

- `mewcode/instructions/loader.py`：层顺序反转为本地→项目→用户；新增 `@include` 展开（深度 ≤3、visited 防环、allowed_root 越界拦截、8KB 限制；用 begin/end 注释包裹）。
- `mewcode/chat/session.py`：新增 `archive` / `restored_needs_compaction_check` 字段；`append_user_text` / `append_assistant` / `append_tool_results` 后自动 `_persist_last`；`clear()` / `switch_provider()` 调用 archive 换发新 ID。
- `mewcode/chat/engine.py`：`run_turn` 与 `_agent_loop` 新增 `memory_manager` / `rebuild_system_prompt`；natural stop 后调度后台记忆更新；恢复会话首次请求消费 `restored_needs_compaction_check` 标记。
- `mewcode/main.py`：装配 `SessionArchive` + `MemoryManager`；启动顺序 = MCP → instructions → cleanup → restore → load memory → build_system_prompt（同时注入 instructions + memory）→ REPL。提供同时支持 `(new_text)` 与 `(memory=...)` 两种调用形式的 `_rebuild_system_prompt`。
- `mewcode/repl/main_loop.py`：`run_repl` 新增 `archive` / `memory_manager` 形参，并把 `memory_manager` / `rebuild_system_prompt` 透传给 `run_turn`。

### 测试 / 脚本

- 新增 `tests/test_instructions_include.py`、`tests/test_sessions_codec.py`、`tests/test_sessions_archive.py`、`tests/test_memory_notes.py`、`tests/test_memory_index.py`、`tests/test_memory_manager.py`、`tests/test_memory_agent_integration.py`。
- 新增 `scripts/verify_memory.py`（端到端 4 节验证）。
- 修订 `tests/test_instructions_loader.py` 三处用例与 `scripts/verify_instructions.py` 一处断言，匹配新优先级与新 `_read_layer` 签名。

## 结论

第九阶段「会话恢复与长期记忆」**已通过验收**：

- 项目指令支持三层优先级与受限 `@include`，靠前内容优先级更高。
- 会话以 JSONL 形式追加写到 `<cwd>/.mewcode/sessions/<session_id>.jsonl`；启动时自动恢复最近未过期会话，能跳过坏行、截断孤儿工具调用、>24 小时插入时间跨度提醒、>30 天清理。
- 自动笔记按四类（preference / correction / project_knowledge / reference）落到用户级或项目级；`index.md` 受 200 行 / 25KB 双限制；natural stop 后异步调 LLM，去重交给 LLM 判断。
- 启动时把 instructions 与 memory 一并注入 system prompt；hash 不变时不重建 system prompt，保留 prompt cache 友好性。
- 第八阶段 compaction、第七阶段 instructions、第五阶段 permissions、第四阶段 plan mode、Agent Loop 全部不退化（412 单测 + 7 个真实/模拟 verify 脚本通过）。
