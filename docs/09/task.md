# MewCode 第八阶段 Tasks

> 基于已批准的 `docs/09/spec.md` 与 `docs/09/plan.md`。共 14 个任务。

## 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/compaction/__init__.py` |
| 新建 | `mewcode/compaction/tokens.py` |
| 新建 | `mewcode/compaction/lightweight.py` |
| 新建 | `mewcode/compaction/summarizer.py` |
| 新建 | `mewcode/compaction/compactor.py` |
| 修改 | `mewcode/chat/session.py` |
| 修改 | `mewcode/chat/engine.py` |
| 修改 | `mewcode/commands/registry.py` |
| 修改 | `mewcode/commands/builtin.py` |
| 修改 | `mewcode/main.py` |
| 修改 | `mewcode/repl/main_loop.py` |
| 修改 | `.gitignore` |
| 新建 | `tests/test_compaction_tokens.py` |
| 新建 | `tests/test_compaction_lightweight.py` |
| 新建 | `tests/test_compaction_summarizer.py` |
| 新建 | `tests/test_compaction_compactor.py` |
| 新建 | `tests/test_compact_command.py` |
| 新建 | `scripts/verify_compaction.py` |
| 新建 | `docs/09/checklist.md` |
| 新建 | `docs/09/acceptance-report.md` |

共 20 个文件（11 新建 + 9 修改）。

---

## 任务执行顺序图

```
T1 (tokens.py) ──┐
                  ├─→ T3 (summarizer)
T2 (lightweight) ─┘                     ├─→ T4 (compactor)
                                                │
T5 (session.py 字段) ───────────────────────────┤
                                                │
T6 (chat/engine 集成) ──────────────────────────┤
                                                │
T7 (commands 集成) ─────────────────────────────┤
                                                │
T8 (main + repl + .gitignore) ──────────────────┤
                                                │
                          ┌───────────────────── ▼
                          ├──→ T9 (test_tokens)
                          ├──→ T10 (test_lightweight)
                          ├──→ T11 (test_summarizer)
                          ├──→ T12 (test_compactor)
                          ├──→ T13 (test_compact_command)
                          └──→ T14 (verify + acceptance + push)
```

**关键路径**：T1 → T3 → T4 → T6 → T8 → T14（6 步主线）

---

## T1: tokens.py

**文件：** 新建 `mewcode/compaction/tokens.py`

**步骤：**
1. `serialize_message_for_estimation(msg)` — TextBlock/ToolUseBlock/ToolResultBlock 拼接
2. `estimate_tokens(messages, last_usage_input_tokens, anchor_message_count)` — 锚定 + 增量
3. 边界处理：`last_usage=0` 或 `anchor > len(messages)` → 全字符估算

**验证：**
- `python -c "from mewcode.compaction.tokens import estimate_tokens; print(estimate_tokens([], 0, 0))"` → 0

---

## T2: lightweight.py

**文件：** 新建 `mewcode/compaction/lightweight.py`

**步骤：**
1. 常量：`SINGLE_TOOL_LIMIT=10240`、`SINGLE_MSG_LIMIT=25600`、预览前后行数
2. `StashEvent` dataclass
3. `_build_preview(content, file_path, size)` — 前 20 + 后 5 行；≤ 25 行不截
4. `_stash_block(block, msg_idx, cwd, session_id)` — 写盘 + 返回预览/event
5. `apply_lightweight(messages, cwd, session_id)`：
   - 找最新 tool_results 消息
   - 阶段 1：单工具 > 10KB 存盘
   - 阶段 2：消息总和 > 25KB 排序+依次存盘
   - 替换 ToolResultBlock 与 Message（frozen → 新对象）

**验证：**
- 单测 T10 覆盖

---

## T3: summarizer.py

**文件：** 新建 `mewcode/compaction/summarizer.py`

**步骤：**
1. 常量：`COMPACTION_SYSTEM_PROMPT`、`KEEP_TOKEN_TARGET=10000`、`KEEP_MIN_MESSAGES=5`
2. `compute_keep_boundary(messages)`：从尾累计 → 至少 5 条 → 扩展真实 user 边界
3. `summarize_messages_to_text(messages)`：拼可读文本
4. `extract_summary(llm_output)`：正则匹配 `<summary>` + 5 段标题校验（≥ 3 个）
5. `summarize_async(provider, messages, instruction)`：调 stream_chat（tools=None） → 解析
6. `build_boundary_message(summary_text, compacted_count)`：含 `<system-reminder>` + 时间戳

**验证：**
- 单测 T11 覆盖

---

## T4: compactor.py

**文件：** 新建 `mewcode/compaction/compactor.py`

**步骤：**
1. 常量：`AUTO_BUFFER=13000`、`MANUAL_BUFFER=3000`、`DEFAULT_CONTEXT_WINDOW=128000`
2. `_CONTEXT_WINDOWS` 字典（已知模型 → window）
3. `_detect_window(model)`：模糊匹配
4. `CompactStats` dataclass
5. `Compactor.__init__(cwd)`
6. `Compactor.before_request(session, manual=False, instruction="")`：
   - 跑第一层
   - 估算 token
   - 熔断检查（自动触发）
   - 计算阈值（auto vs manual）
   - 必要时调 summarize_async
   - 失败计数 + disabled
   - 成功 → 替换 messages + 重置锚点
7. `Compactor.compact_now(session, instruction)` → before_request(manual=True)
8. `Compactor.after_response(session, usage)`：写 last_usage + anchor
9. `Compactor.reset_state(session)`：/clear / switch 调

**验证：**
- 单测 T12 覆盖

---

## T5: session.py 字段扩展

**文件：** 修改 `mewcode/chat/session.py`

**步骤：**
1. 加 5 个字段：`last_usage_input_tokens` / `last_anchor_message_count` / `compaction_failures` / `compaction_disabled` / `session_id`
2. `clear()` 重置压缩状态（保留 session_id）
3. `switch_provider()` 重置压缩状态

**验证：**
- 既有 session 测试通过

---

## T6: chat/engine.py 集成

**文件：** 修改 `mewcode/chat/engine.py`

**步骤：**
1. `run_turn` 签名加 `compactor=None`
2. append_user_text 之后调 `compactor.before_request(session)`
3. 把 stats 通过 renderer.print_info 提示用户
4. `_agent_loop` 签名加 `compactor=None`
5. 在 stream_chat 完成（拿到 Usage）处调 `compactor.after_response(session, usage)`
6. 异常容错：压缩异常不阻塞 turn

**验证：**
- `python -m py_compile mewcode/chat/engine.py`
- 全套已有单测仍过（compactor 默认 None 时旧行为）

---

## T7: commands 集成

**文件：** 修改 `mewcode/commands/registry.py` + `builtin.py`

**步骤：**
1. CommandContext 加 `compactor` 字段（default=None）
2. 实现 `_handle_compact(ctx)` handler
3. 注册 `/compact` 命令
4. 显示存盘事件 / 摘要结果 / 失败原因

**验证：**
- 单测 T13 覆盖

---

## T8: main + repl + .gitignore

**文件：** 修改 `mewcode/main.py` + `mewcode/repl/main_loop.py` + `.gitignore`

**步骤：**
1. main.py 阶段 2：
   - `from mewcode.compaction import Compactor`
   - `from datetime import datetime`
   - `session.session_id = datetime.now().strftime(...)`
   - `compactor = Compactor(cwd=sandbox.cwd)`
2. _amain 把 compactor 透传给 run_repl
3. run_repl 签名加 `compactor=None`，透传给 chat.run_turn 与 CommandContext
4. .gitignore 加 `.mewcode/transcripts/`

**验证：**
- python -m mewcode 启动正常

---

## T9-T13: 单测（约 22 个）

**T9 test_compaction_tokens**（3 个）：
- 锚定估算
- 无锚点全字符
- 锚点超出回退

**T10 test_compaction_lightweight**（5 个）：
- 单工具 > 10KB 存盘
- 单工具 < 10KB 不动
- 单消息排序存盘
- 预览前 20 + 后 5
- ≤ 25 行不截

**T11 test_compaction_summarizer**（6 个）：
- compute_keep_boundary token 边界
- compute_keep_boundary 至少 5 条
- compute_keep_boundary 真实 user 扩展
- extract_summary 正常
- extract_summary 失败（无标签 / 标签为空 / 段不足 3）
- build_boundary_message 含 system-reminder

**T12 test_compaction_compactor**（6 个）：
- before_request 估算未达不触发
- 估算达阈值触发并替换 messages（stub provider）
- 熔断 3 次 → disabled
- disabled 跳过自动
- compact_now 必触发
- after_response 更新锚点

**T13 test_compact_command**（3 个）：
- /compact 默认
- /compact 带指示
- /compact 失败不熔断

---

## T14: verify + checklist + acceptance + push

**文件：**
- 新建 `scripts/verify_compaction.py`
- 新建 `docs/09/checklist.md`
- 新建 `docs/09/acceptance-report.md`

**步骤：**
1. verify_compaction.py：
   - 构造 mock provider 模拟摘要响应
   - 端到端跑：第一层 → 第二层 → messages 替换
2. checklist.md：基于 spec AC1-AC23 展开
3. 全量回归：
   - pytest tests/ -q (320 + 22 ≈ 342)
   - 9 个旧端到端脚本仍通过
   - verify_compaction.py 通过
4. acceptance-report.md
5. git add + commit + push

**验证：**
- 全部 AC PASSED
- 不退化

---

## 自检结论

- ✅ plan 14 个改动都有任务对应
- ✅ 执行图无环
- ✅ 命名一致（Compactor / CompactStats / StashEvent / 函数名）
- ✅ 不退化覆盖（compactor=None 时回退第七阶段）
- ✅ 单测约 22 个新增，符合 spec N4
