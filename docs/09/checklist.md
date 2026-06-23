# MewCode 第九阶段 Checklist：会话恢复与长期记忆

> 验证环境：Windows + VS Code / PowerShell 或 CMD，项目根 `e:\AI\vscode_project\mecode`。
> 本清单覆盖 `docs/09/spec.md` 的 AC1-AC21。每项都应通过运行测试、脚本或观察行为验证。

## 编译与基础检查

- [ ] **C1 包可导入** — `python -c "import mewcode; print(mewcode.__version__)"` 正常输出版本。
- [ ] **C2 全部源文件语法合法** — `python -m compileall mewcode/ -q` 通过。
- [ ] **C3 第九阶段新增模块可导入** —
      `python -c "from mewcode.sessions import SessionArchive; from mewcode.memory import MemoryManager; print('ok')"` 输出 `ok`。
- [ ] **C4 全量单测通过** — `pytest tests/ -q` 全部通过。
- [ ] **C5 命令行入口仍可启动** — `python -m mewcode` 能进入 REPL。

## 项目指令优先级与 include（spec F1/F2）

- [ ] **AC1 指令优先级** — 构造用户级、项目级、本地级三层指令，`InstructionsLoader.load_all()` 输出顺序为本地级 → 项目级 → 用户级。（验证：`pytest tests/test_instructions_include.py::test_priority_local_project_user -v`）
- [ ] **AC2 include 展开** — 项目指令包含 `@include docs/rules.md` 时，输出含被引用文件内容，以及 `<!-- begin include: ... -->` / `<!-- end include: ... -->`。（验证：对应 include 单测）
- [ ] **AC3 include 防环** — A include B、B include A 时不会死循环，重复路径被跳过并 warning。（验证：防环单测）
- [ ] **AC4 include 深度限制** — 第 4 层嵌套 include 被跳过并 warning。（验证：深度单测）
- [ ] **AC5 include 越界拦截** — 项目指令 `@include ../outside.md` 不读取项目目录外内容。（验证：越界单测）
- [ ] **include 非 UTF-8 容错** — include 文件非 UTF-8 时 warning + 跳过，不阻塞加载。（验证：编码单测）
- [ ] **include 保持 8KB 限制** — include 文件超过 8KB 时截断并 warning。（验证：大小限制单测）

## 会话 JSONL 存档（spec F3-F5）

- [ ] **AC6 会话 JSONL 追加** — 调用 `append_user_text` / `append_assistant` / `append_tool_results` 后，`<cwd>/.mewcode/sessions/<session_id>.jsonl` 新增对应 message 行。（验证：`pytest tests/test_sessions_archive.py::test_append_messages_to_jsonl -v`）
- [ ] **Message 编解码 roundtrip** — TextBlock、ThinkingBlock、ToolUseBlock、ToolResultBlock 都能 JSON roundtrip。（验证：`pytest tests/test_sessions_codec.py -v`）
- [ ] **AC9 不维护 meta 文件** — 会话目录内只需要 `.jsonl` 文件；标题、消息数、更新时间来自扫描 JSONL，不生成 `meta.json` / `index.json`。（验证：archive 单测 + 目录观察）
- [ ] **会话 ID 格式** — 新会话 ID 符合 `YYYYMMDD-HHMMSS-xxxx`，同秒多次生成不冲突。（验证：archive 单测）

## 会话恢复异常处理（spec F6-F11）

- [ ] **AC7 坏行跳过** — JSONL 插入非法 JSON 行后恢复，坏行计数 + warning，其他有效消息保留。（验证：`pytest tests/test_sessions_archive.py::test_restore_skips_bad_lines -v`）
- [ ] **AC8 孤儿工具调用截断** — 结尾 assistant tool_use 无匹配 tool_result 时，恢复后截断到该 assistant 之前。（验证：截断单测）
- [ ] **孤儿 tool_result 截断** — tool_result 前一条不是匹配 assistant tool_use 时，恢复后截断到该 tool_result 之前。（验证：截断单测）
- [ ] **AC10 自动恢复最近会话** — 多个 JSONL 文件存在时，启动恢复 updated_at 最新且未过期的会话。（验证：restore_latest 单测）
- [ ] **AC11 过期清理** — updated_at 超过 30 天的 JSONL 文件在启动清理中被删除；删除失败不阻塞启动。（验证：cleanup 单测）
- [ ] **AC12 长间隔提醒** — 恢复超过 24 小时未更新的会话时，尾部插入一次 `<system-reminder>`；重复恢复不重复插入。（验证：gap reminder 单测）
- [ ] **AC13 恢复后超限压缩** — 恢复消息估算超过自动压缩阈值时，第一次请求前调用 compactor 压缩一次。（验证：集成单测或 stub compactor）
- [ ] **恢复横幅** — 恢复成功时打印 `💾 已恢复会话: ...（N 条消息，标题：...）`。（验证：启动集成或 renderer stub）

## 自动笔记文件与索引（spec F12-F18）

- [ ] **AC16 笔记 frontmatter** — 新增笔记包含 id、scope、category、created_at、updated_at、source_session、tags。（验证：`pytest tests/test_memory_notes.py::test_note_frontmatter_roundtrip -v`）
- [ ] **笔记原子写入** — 写 note 使用 tmp + replace，最终 `notes/<id>.md` 可读。（验证：notes 单测）
- [ ] **坏笔记跳过** — `list_notes()` 遇到坏 frontmatter 文件 warning + 跳过。（验证：notes 单测）
- [ ] **路径安全** — 删除/写入 note 不允许路径穿越到 memory root 外。（验证：notes 单测）
- [ ] **AC17 用户级/项目级分开存** — preference/correction 默认写 `~/.mewcode/memory`；project_knowledge/reference 默认写 `<cwd>/.mewcode/memory`。（验证：manager 单测）
- [ ] **AC18 index 行数限制** — `index.md` 重建后行数 ≤ 200。（验证：`pytest tests/test_memory_index.py::test_index_line_limit -v`）
- [ ] **AC18 index 大小限制** — `index.md` 重建后 UTF-8 字节数 ≤ 25KB。（验证：index 单测）
- [ ] **index 优先级裁剪** — 超限时优先保留 correction > preference > project_knowledge > reference；同类按 updated_at 倒序。（验证：index 单测）

## 记忆注入与自动更新（spec F14-F17）

- [ ] **AC19 请求前注入记忆** — 同时存在用户级和项目级 `index.md` 时，下一次构造的 `session.system_prompt` 含 `## 长期记忆`、项目记忆、用户记忆。（验证：manager 单测 + `verify_memory.py`）
- [ ] **AC20 memory hash 不变不重建 system_prompt** — index 内容不变时连续刷新不会调用 rebuild；内容变化时调用一次。（验证：manager hash 单测）
- [ ] **AC14 自动笔记触发** — Agent Loop natural stop 且最终回复无 tool_use 时，调用 `MemoryManager.schedule_update(...)`。（验证：`pytest tests/test_memory_agent_integration.py::test_natural_stop_schedules_memory_update -v`）
- [ ] **AC15 非自然停止不更新笔记** — user_cancel、provider error、max_iterations、仍有工具调用时不触发自动笔记更新。（验证：agent integration 单测）
- [ ] **LLM 更新不带工具** — `propose_memory_operations` 调 `provider.stream_chat(..., tools_format=None)`。（验证：updater stub 单测）
- [ ] **去重操作格式** — updater 能解析 create/update/delete/noop；解析失败返回空列表不影响主流程。（验证：manager/updater 单测）
- [ ] **后台异常不阻塞** — `schedule_update` 后台任务异常只 warning，不影响 REPL 下一次输入。（验证：manager 单测）

## main/repl 集成

- [ ] **main 装配 SessionArchive** — 启动时创建 archive，执行过期清理，恢复 latest 或创建新会话。（验证：`verify_memory.py` 或 main 集成测试）
- [ ] **main 装配 MemoryManager** — 启动时读取 memory context 并传给 `build_system_prompt(..., memory=...)`。（验证：`verify_memory.py`）
- [ ] **/instructions reload 保留 memory** — 指令 reload 重建 system prompt 后，长期记忆段仍存在。（验证：命令单测或集成测试）
- [ ] **run_repl 透传 memory_manager** — 对话分支调用 `run_turn(..., memory_manager=memory_manager)`。（验证：集成测试）
- [ ] **clear 后新会话** — `/clear` 后 session messages 清空，压缩状态重置，后续消息写入新 session_id 文件。（验证：archive/session 单测）

## 端到端验证

- [ ] **verify_memory.py 通过** — `python scripts/verify_memory.py` 输出 `✓ 会话恢复与长期记忆端到端通过`。
- [ ] **既有 verify 脚本不退化**：
  - [ ] `python scripts/verify_instructions.py`
  - [ ] `python scripts/verify_compaction.py`
  - [ ] `python scripts/verify_mcp.py`
  - [ ] `python scripts/verify_permissions.py`
  - [ ] `python scripts/verify_plan_mode.py`
  - [ ] `python scripts/verify_cache_hit.py`
  - [ ] `python scripts/verify_round_loop.py`
  - [ ] `python scripts/verify_agent_loop.py`

## 不退化（spec AC21）

- [ ] **AC21 全量测试** — `pytest tests/ -q` 全部通过。
- [ ] **无 sessions/memory 时兼容第八阶段** — 删除/移走 `.mewcode/sessions` 与 `.mewcode/memory` 后启动，不报错，短对话行为正常。
- [ ] **compaction 不退化** — 第八阶段 compaction 单测与 `verify_compaction.py` 通过。
- [ ] **instructions 不退化** — 第七阶段 instructions 单测与 `verify_instructions.py` 通过。
- [ ] **permissions/MCP/tools 不退化** — 相关测试与 verify 脚本通过。

## Windows 兼容

- [ ] **路径合法** — session_id 不含 Windows 非法字符，JSONL 文件能在 CMD/PowerShell 下创建。
- [ ] **UTF-8 正常** — 中文会话内容、中文笔记、emoji 横幅不会导致编码异常。
- [ ] **pathlib 路径** — include、sessions、memory 均使用 `Path`，无硬编码 `/`。

## 验收报告要求

完成实现后在 `docs/09/acceptance-report.md` 记录：

- 实际执行的命令。
- 关键输出摘要（如 passed 数量、verify 输出）。
- 每个失败项的修复记录（如有）。
- 明确列出 AC1-AC21 的通过证据。
