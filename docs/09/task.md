# MewCode 第九阶段 Tasks：会话恢复与长期记忆

> 基于 `docs/09/spec.md` 与 `docs/09/plan.md`。共 15 个任务。四份文档确认后才能进入实现。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/instructions/loader.py` | 指令优先级调整 + `@include` 展开 |
| 新建 | `mewcode/sessions/__init__.py` | sessions 公共出口 |
| 新建 | `mewcode/sessions/codec.py` | Message/ContentBlock JSON 编解码 |
| 新建 | `mewcode/sessions/archive.py` | JSONL 追加、扫描、恢复、清理 |
| 修改 | `mewcode/chat/session.py` | archive hook、恢复标记、clear rotate |
| 新建 | `mewcode/memory/__init__.py` | memory 公共出口 |
| 新建 | `mewcode/memory/notes.py` | 笔记 frontmatter 读写 |
| 新建 | `mewcode/memory/index.py` | index.md 生成与限制 |
| 新建 | `mewcode/memory/updater.py` | LLM 记忆更新 prompt 与解析 |
| 新建 | `mewcode/memory/manager.py` | 记忆注入、hash、后台调度 |
| 修改 | `mewcode/chat/engine.py` | natural stop 后调度记忆更新 |
| 修改 | `mewcode/main.py` | 装配 sessions/memory，恢复会话 |
| 修改 | `mewcode/repl/main_loop.py` | 透传 archive/memory_manager |
| 修改 | `.gitignore` | 忽略 sessions/transcripts，可保留 memory 是否入库由用户决定 |
| 新建 | `tests/test_instructions_include.py` | include 与优先级测试 |
| 新建 | `tests/test_sessions_codec.py` | 消息编解码测试 |
| 新建 | `tests/test_sessions_archive.py` | JSONL 恢复/清理/截断测试 |
| 新建 | `tests/test_memory_notes.py` | 笔记 frontmatter 测试 |
| 新建 | `tests/test_memory_index.py` | index 限制测试 |
| 新建 | `tests/test_memory_manager.py` | memory context/hash/update 测试 |
| 新建 | `tests/test_memory_agent_integration.py` | natural stop 调度测试 |
| 新建 | `scripts/verify_memory.py` | 会话恢复 + 记忆端到端验证 |
| 覆盖 | `docs/09/checklist.md` | 第九阶段验收清单 |
| 覆盖 | `docs/09/acceptance-report.md` | 第九阶段验收报告 |

## 执行顺序图

```text
T1 instructions include

T2 sessions codec ──→ T3 sessions archive ──→ T4 Session 持久化 hook
                                               │
T5 memory notes ──→ T6 memory index ──→ T7 memory updater ──→ T8 manager
                                               │                 │
                                               └──────┬──────────┘
                                                      ▼
T9 chat.engine 集成 ──→ T10 main/repl 装配

T11 instructions 测试
T12 sessions 测试
T13 memory 测试
T14 集成 verify
T15 checklist + acceptance
```

---

## T1: 扩展 InstructionsLoader 优先级与 include

**文件：** 修改 `mewcode/instructions/loader.py`

**依赖：** 无

**步骤：**
1. 将 `load_all()` 的层级加载顺序调整为：本地级 → 项目级 → 用户级。
2. 更新 framing 文本，明确「靠前内容优先级更高」。
3. 新增 `_INCLUDE_RE` 与 `_MAX_INCLUDE_DEPTH = 3`。
4. 抽出 `_read_text_with_limit(path)`，复用现有 8KB、UTF-8、warning 逻辑。
5. 实现 `_resolve_include_path(base_file, raw, allowed_root)`：
   - 相对当前文件目录解析。
   - `resolve()` 后必须在 allowed_root 内。
   - 越界 warning + 返回 None。
6. 实现 `_expand_includes(text, current_file, allowed_root, depth, visited)`：
   - 逐行识别独占 `@include <path>`。
   - 深度超过 3 warning + 跳过。
   - visited 防环。
   - 展开内容加 begin/end include 注释。
7. `_read_layer` 读取主文件后调用 include 展开。
8. 保持公开方法 `load_all/current_text/current_hash/loaded_layers/reload_and_check` 不变。

**验证：**
- `pytest tests/test_instructions_include.py -v`
- 旧 `test_instructions_loader.py` 仍通过（如存在）。

---

## T2: sessions codec

**文件：** 新建 `mewcode/sessions/__init__.py`、`mewcode/sessions/codec.py`

**依赖：** 无

**步骤：**
1. 在 `codec.py` 实现 `block_to_dict(block)`。
2. 实现 `block_from_dict(data)`，支持 text/thinking/tool_use/tool_result，非法输入抛 `ValueError`。
3. 实现 `message_to_record(message, ts)`，输出 `{type, ts, role, content}`。
4. 实现 `message_from_record(record)`，校验字段并返回 `(Message, datetime)`。
5. 实现 `message_to_jsonl(message)` 与 `message_from_jsonl(line)` 便捷函数。
6. `__init__.py` 暴露 codec 公共函数。

**验证：**
- `pytest tests/test_sessions_codec.py -v`
- `python -c "from mewcode.sessions.codec import message_to_record; print('ok')"`

---

## T3: SessionArchive JSONL 存档与恢复

**文件：** 新建 `mewcode/sessions/archive.py`

**依赖：** T2

**步骤：**
1. 定义 `SessionSummary`、`RestoreResult` dataclass。
2. 实现 `new_session_id()`：`YYYYMMDD-HHMMSS-xxxx`。
3. 实现 `session_path(session_id)`。
4. 实现 `append_message(session_id, message)`：
   - 创建 `.mewcode/sessions`。
   - append UTF-8 JSONL。
   - `ensure_ascii=False`。
   - flush。
5. 实现 `_read_messages(path)`：逐行解析，坏行计数跳过。
6. 实现 `_summarize(path)`：扫描 JSONL 计算 title/message_count/created_at/updated_at。
7. 实现 `scan_summaries()`，按 updated_at 倒序。
8. 实现 `cleanup_expired(days=30)`。
9. 实现 `_truncate_incomplete_tool_pairing(messages)`。
10. 实现 `_maybe_insert_gap_reminder(...)`。
11. 实现 `restore_latest()` 与 `restore(session_id)`。
12. 实现 `attach(session, restore)` 与 `rotate(session)`。

**验证：**
- `pytest tests/test_sessions_archive.py -v`

---

## T4: Session 持久化 hook

**文件：** 修改 `mewcode/chat/session.py`

**依赖：** T3

**步骤：**
1. 增加字段：
   - `archive: object = None`
   - `restored_needs_compaction_check: bool = False`
2. 新增私有方法 `_persist_last()`：archive/session_id 存在时写最后一条 message。
3. 在 `append_user_text`、`append_assistant`、`append_tool_results` 末尾调用 `_persist_last()`。
4. `clear()` 末尾：如果 archive 有 `rotate(session)`，生成新 session_id。
5. `switch_provider()` 同样重置压缩状态；是否 rotate 可按实现选择，推荐 rotate 避免不同 provider 历史混在同文件。
6. 持久化异常不应让主流程崩溃；可打印 warning 或静默跳过（测试推荐捕获）。

**验证：**
- append 三类消息后 JSONL 有新增行。
- 既有 Session 测试仍过。

---

## T5: memory notes

**文件：** 新建 `mewcode/memory/__init__.py`、`mewcode/memory/notes.py`

**依赖：** 无

**步骤：**
1. 定义 `MemoryNote` dataclass。
2. 实现 `new_note_id()`：`mem_YYYYMMDD_HHMMSS_xxxx`。
3. 实现 `note_to_markdown(note)`：写 frontmatter + body。
4. 实现 `note_from_markdown(path)`：解析 frontmatter，非法抛 `ValueError`。
5. 实现 `scope_root(cwd, scope)`：user → `~/.mewcode/memory`，project → `<cwd>/.mewcode/memory`。
6. 实现 `write_note_atomic(note, root)`：tmp + replace。
7. 实现 `delete_note_safe(note_id, root)`：路径安全校验。
8. 实现 `list_notes(root)`：坏文件 warning 后跳过。
9. `__init__.py` 暴露 `MemoryNote`。

**验证：**
- `pytest tests/test_memory_notes.py -v`

---

## T6: memory index

**文件：** 新建 `mewcode/memory/index.py`

**依赖：** T5

**步骤：**
1. 定义常量：`MAX_INDEX_LINES=200`、`MAX_INDEX_BYTES=25*1024`。
2. 定义分类优先级：correction > preference > project_knowledge > reference。
3. 实现 `_note_to_index_line(note)`，包含 `[id]` 与简短 body。
4. 实现 `build_index(notes, scope)`：按分类组织、updated_at 倒序。
5. 实现超限裁剪：加入下一条前检查行数/字节数。
6. 实现 `rebuild_index(root, scope)`：读取 notes、构建、原子写 `index.md`。
7. 实现 `read_index(root)`：不存在返回 None，超过 25KB 时读取前 25KB 并 warning。

**验证：**
- `pytest tests/test_memory_index.py -v`

---

## T7: memory updater

**文件：** 新建 `mewcode/memory/updater.py`

**依赖：** T5, T6

**步骤：**
1. 定义 `MEMORY_UPDATE_SYSTEM`，要求：
   - 只记录稳定事实。
   - 分类四选一。
   - 输出 JSON。
   - 去重由 LLM 判断。
   - 不调用工具。
2. 定义 `MemoryOperation` dataclass。
3. 实现 `recent_messages_to_text(messages)`。
4. 实现 `parse_operations(text)`：从 JSON 中解析 operations，非法返回空列表。
5. 实现 `propose_memory_operations(provider, recent_messages, user_index, project_index, session_id)`：
   - 调 provider.stream_chat。
   - `thinking=False`。
   - `tools_format=None`。
   - system 使用 `MEMORY_UPDATE_SYSTEM`。
6. 解析失败或 provider 异常返回空列表。

**验证：**
- 用 stub provider 测试 create/update/delete/noop 解析。
- `pytest tests/test_memory_manager.py -v` 中覆盖。

---

## T8: MemoryManager

**文件：** 新建 `mewcode/memory/manager.py`

**依赖：** T5, T6, T7

**步骤：**
1. 定义 `MemoryContext` dataclass。
2. 实现 `MemoryManager.__init__(cwd)`，保存 user/project roots。
3. 实现 `load_context()`：读取 user/project index，拼接 `## 长期记忆` 内容，计算 hash。
4. 实现 `refresh_system_prompt_if_changed(session, rebuild_system_prompt)`。
5. 实现 `_apply_operation(op, session_id)`：
   - create：生成 note 并写入。
   - update：读取旧 note，更新 body/tags/category/scope/updated_at。
   - delete：安全删除。
   - noop：不做事。
6. 实现 category → 默认 scope 修正：preference/correction=user，project_knowledge/reference=project。
7. 实现 `update_once(session, recent_messages)`：调用 updater，应用操作，重建变更 scope 的 index。
8. 实现 `schedule_update(session, recent_messages, renderer=None)`：`asyncio.create_task` 后台执行，异常 warning。

**验证：**
- `pytest tests/test_memory_manager.py -v`

---

## T9: chat.engine 集成记忆调度

**文件：** 修改 `mewcode/chat/engine.py`

**依赖：** T8

**步骤：**
1. `run_turn` 签名新增 `memory_manager=None`。
2. `_agent_loop` 签名新增 `memory_manager=None` 并透传。
3. 在请求前，如果 memory_manager 有 `refresh_system_prompt_if_changed` 且 rebuild callable 可用，则刷新记忆注入（也可放在 repl/main）。
4. 在 natural stop 分支，调用：
   ```python
   memory_manager.schedule_update(session, _recent_messages_for_memory(session.messages), renderer)
   ```
5. 增加 `_recent_messages_for_memory(messages, limit=8)`。
6. max_iterations、user_cancel、unknown_tools、provider error 均不触发。
7. 保持 compactor 逻辑不变。

**验证：**
- `pytest tests/test_memory_agent_integration.py -v`
- 旧 `test_chat_round_loop.py` 仍通过。

---

## T10: main/repl 装配 sessions + memory

**文件：** 修改 `mewcode/main.py`、`mewcode/repl/main_loop.py`

**依赖：** T1-T9

**步骤：**
1. main.py 构造 `SessionArchive(sandbox.cwd)`。
2. 启动时 `cleanup_expired(30)`。
3. `restore_latest()`，并 attach 到 session。
4. 根据 RestoreResult 打印恢复横幅、坏行 warning、截断 warning。
5. 构造 `MemoryManager(sandbox.cwd)`，读取 memory context。
6. 构造/重建 system_prompt 时同时传 `custom_instructions` 和 `memory`。
7. 更新 `_rebuild_system_prompt` callable，使 `/instructions reload` 后不会丢 memory。
8. `run_repl` 签名新增 `archive=None, memory_manager=None, rebuild_system_prompt=None`（已有 rebuild 则复用）。
9. CommandContext 如有需要新增 `archive` / `memory_manager` 字段；本阶段无命令也可不加 archive。
10. 对话分支调用 `run_turn(..., memory_manager=memory_manager)`。

**验证：**
- `python -m py_compile mewcode/main.py mewcode/repl/main_loop.py`
- `python scripts/verify_memory.py`

---

## T11: instructions include 单测

**文件：** 新建 `tests/test_instructions_include.py`

**依赖：** T1

**测试用例：**
1. 三层优先级：本地 → 项目 → 用户。
2. include 正常展开并带 begin/end 标记。
3. include 相对当前文件目录解析。
4. include 防环：A↔B 不死循环，输出只展开一次。
5. include 深度超过 3 warning + 跳过。
6. include 越界 `../outside.md` 被拒绝。
7. include 非 UTF-8 跳过。

**验证：**
- `pytest tests/test_instructions_include.py -v`

---

## T12: sessions 单测

**文件：** 新建 `tests/test_sessions_codec.py`、`tests/test_sessions_archive.py`

**依赖：** T2-T4

**测试用例：**

`test_sessions_codec.py`：
1. TextBlock roundtrip。
2. ThinkingBlock roundtrip。
3. ToolUseBlock roundtrip。
4. ToolResultBlock roundtrip。
5. 非法 block type 抛 ValueError。

`test_sessions_archive.py`：
1. append 三类消息生成 JSONL。
2. 坏行跳过。
3. scan summary 计算 title/count/updated_at。
4. restore_latest 选择最新。
5. 孤儿 tool_use 截断。
6. 孤儿 tool_result 截断。
7. 24h gap reminder 插入且不重复。
8. 30 天过期清理。
9. 不生成 meta/index 文件。

**验证：**
- `pytest tests/test_sessions_codec.py tests/test_sessions_archive.py -v`

---

## T13: memory 单测

**文件：** 新建 `tests/test_memory_notes.py`、`tests/test_memory_index.py`、`tests/test_memory_manager.py`

**依赖：** T5-T8

**测试用例：**

`test_memory_notes.py`：
1. frontmatter 写读 roundtrip。
2. write_note_atomic 生成 notes 文件。
3. list_notes 跳过坏文件。
4. delete_note_safe 不允许路径穿越。

`test_memory_index.py`：
1. 按分类生成 index。
2. 同类按 updated_at 倒序。
3. 超过 200 行会裁剪。
4. 超过 25KB 会裁剪。
5. 优先保留 correction/preference。

`test_memory_manager.py`：
1. load_context 同时包含项目/用户 index。
2. hash 不变不重建 system_prompt。
3. hash 变化调用 rebuild。
4. create operation 写正确 scope。
5. update operation 更新已有 note。
6. delete operation 删除 note。
7. category/scope 冲突时按默认 scope 修正。
8. schedule_update 后台异常不影响主流程。

**验证：**
- `pytest tests/test_memory_notes.py tests/test_memory_index.py tests/test_memory_manager.py -v`

---

## T14: 集成测试与 verify_memory

**文件：** 新建 `tests/test_memory_agent_integration.py`、`scripts/verify_memory.py`

**依赖：** T1-T13

**步骤：**
1. `test_memory_agent_integration.py` 用 stub memory_manager 验证：
   - natural stop 调度一次。
   - tool_use 中间不调度。
   - user_cancel/max_iterations 不调度。
2. `verify_memory.py` 端到端：
   - 创建临时 cwd。
   - 写 AGENTS.md + include 文件。
   - 创建 SessionArchive，append 几条消息。
   - 恢复 latest。
   - 写 user/project memory notes，rebuild index。
   - load memory context 并 build_system_prompt，验证含 `## 长期记忆`。
   - 模拟 MemoryManager update_once create 操作。
   - 打印 `✓ 会话恢复与长期记忆端到端通过`。
3. 全量回归：
   - `pytest tests/ -q`
   - `python scripts/verify_memory.py`
   - 既有 verify 脚本：`verify_compaction.py`、`verify_instructions.py` 等。

**验证：**
- `python scripts/verify_memory.py`
- `pytest tests/ -q`

---

## T15: checklist + acceptance

**文件：** 覆盖 `docs/09/checklist.md`、`docs/09/acceptance-report.md`

**依赖：** T1-T14

**步骤：**
1. 根据 spec AC1-AC21 写 checklist。
2. 执行 checklist 中所有自动验证。
3. 记录命令、输出摘要、通过/失败项。
4. 如失败，回到对应任务修复后重跑。
5. 全部通过后写 acceptance-report.md。

**验证：**
- `docs/09/checklist.md` 覆盖所有 AC。
- `docs/09/acceptance-report.md` 有实际证据，不写「应该通过」。

## 任务汇总

| # | 任务 | 主要文件 | 依赖 |
|---|---|---|---|
| T1 | instructions include | `instructions/loader.py` | 无 |
| T2 | sessions codec | `sessions/codec.py` | 无 |
| T3 | sessions archive | `sessions/archive.py` | T2 |
| T4 | Session hook | `chat/session.py` | T3 |
| T5 | memory notes | `memory/notes.py` | 无 |
| T6 | memory index | `memory/index.py` | T5 |
| T7 | memory updater | `memory/updater.py` | T5/T6 |
| T8 | MemoryManager | `memory/manager.py` | T5-T7 |
| T9 | chat 集成 | `chat/engine.py` | T8 |
| T10 | main/repl 装配 | `main.py`, `repl/main_loop.py` | T1-T9 |
| T11 | instructions 测试 | `test_instructions_include.py` | T1 |
| T12 | sessions 测试 | `test_sessions_*.py` | T2-T4 |
| T13 | memory 测试 | `test_memory_*.py` | T5-T8 |
| T14 | 集成 verify | `verify_memory.py` | T1-T13 |
| T15 | 验收文档 | `checklist.md`, `acceptance-report.md` | T14 |

## 自检结论

- ✅ spec 的 F1-F19 均有任务覆盖。
- ✅ 任务依赖无环。
- ✅ 每个任务都有验证方式。
- ✅ 会话存档、指令 include、自动记忆三个子系统边界清晰。
- ✅ 不引入新依赖。
- ✅ 默认无 sessions/memory 时兼容第八阶段。
