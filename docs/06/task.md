# MewCode 第五阶段 Tasks

> 基于已批准的 `docs/06/spec.md` 与 `docs/06/plan.md`。共 16 个任务，
> 覆盖 permissions 子模块、Sandbox.safe_open、chat 集成、命令扩展、
> 单测与端到端验收。

## 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/permissions/__init__.py` |
| 新建 | `mewcode/permissions/blocklist.py` |
| 新建 | `mewcode/permissions/rules.py` |
| 新建 | `mewcode/permissions/loader.py` |
| 新建 | `mewcode/permissions/policy.py` |
| 新建 | `mewcode/permissions/interactive.py` |
| 修改 | `mewcode/tools/errors.py` (+ PathRaceConditionError) |
| 修改 | `mewcode/tools/sandbox.py` (+ safe_open) |
| 修改 | `mewcode/tools/read.py` / `write.py` / `edit.py` (改用 safe_open) |
| 修改 | `mewcode/chat/session.py` (+ permission_* 字段) |
| 修改 | `mewcode/chat/engine.py` (集成 policy.check) |
| 修改 | `mewcode/commands/registry.py` (CommandContext + policy) |
| 修改 | `mewcode/commands/builtin.py` (+ /permissions 子命令) |
| 修改 | `mewcode/main.py` (加载 PermissionPolicy) |
| 修改 | `mewcode/repl/main_loop.py` (透传 policy) |
| 新建 | `tests/test_blocklist.py` |
| 新建 | `tests/test_permissions_rules.py` |
| 新建 | `tests/test_permissions_loader.py` |
| 新建 | `tests/test_permissions_policy.py` |
| 新建 | `tests/test_permissions_interactive.py` |
| 新建 | `tests/test_sandbox_toctou.py` |
| 新建 | `tests/test_permissions_command.py` |
| 新建 | `scripts/verify_permissions.py` |

共 22 个文件（13 新建 + 9 修改）。

---

## 任务执行顺序图

```
T1 (blocklist) ──→ T6 (policy)
T2 (rules)     ──→ T6                    ┌→ T11 (单测合一)
T3 (loader)    ──→ T6                    │
T4 (errors)    ──→ T5 (sandbox.safe_open)│
                                          │
T6 ──→ T7 (interactive) ──→ T8 (chat 集成)──┤
T9 (commands) ──→ T10 (main + repl 装配) ──┤
                                          ▼
                              T12 (test_chat_round_loop 适配)
                                          │
                              T13 (verify_permissions.py)
                                          │
                              T14 (默认 permissions.yaml + .gitignore)
                                          │
                              T15 (验收 + acceptance-report.md)
                                          │
                              T16 (commit + push)
```

**关键路径**：T1→T6→T8→T11→T13→T15（6 步主线）
**可并行**：T2/T3 / T4-T5 / T7 / T9 与 T6 平行

---

## T1: blocklist.py

**文件：** 新建 `mewcode/permissions/blocklist.py`

**依赖：** 无

**步骤：**
1. 定义 DANGEROUS_PATTERNS 列表（10 条正则，按 plan 2.1）
2. 实现 `match_blocklist(command: str) -> str | None`
3. docstring 说明：仅作用于 run 工具，不可绕过

**验证：**
- `python -c "from mewcode.permissions.blocklist import match_blocklist; print(match_blocklist('rm -rf /')); print(match_blocklist('git status'))"` 输出正则模式与 None

---

## T2: rules.py

**文件：** 新建 `mewcode/permissions/rules.py`

**依赖：** 无

**步骤：**
1. 定义 TOOL_NAME_MAP 字典（Bash↔run 等）
2. 定义 `Rule` frozen dataclass + `matches(tool_name, target)` 方法
3. 实现 `parse_rule(raw: str) -> Rule | None`（用正则提取工具名+模式）
4. 实现 `extract_match_target(tool_name, params) -> str | None`

**验证：**
- `parse_rule("Bash(git *)")` 返回 Rule(tool="run", pattern="git *", ...)
- `parse_rule("invalid")` 返回 None
- `Rule(tool="run", pattern="git *", raw=...).matches("run", "git status")` → True

---

## T3: loader.py

**文件：** 新建 `mewcode/permissions/loader.py`

**依赖：** T2

**步骤：**
1. 定义 `PermissionConfig` dataclass (mode / allow / deny)
2. 实现 `load_layer(path) -> tuple[mode, allow, deny]`：
   - 文件不存在 → (None, [], [])
   - YAML 解析失败 → 打印 warning + 返回空
   - 非法规则 → 跳过 + warning
3. 实现 `load_all(cwd) -> PermissionConfig`：
   - 加载用户级 / 项目级 / 本地级三层
   - mode：本地→项目→用户→"default"
   - allow/deny：本地+项目+用户拼接

**验证：**
- 在 tmp 路径下构造三层 YAML 文件，调 load_all 检查合并结果

---

## T4: errors.py 增加 PathRaceConditionError

**文件：** 修改 `mewcode/tools/errors.py`

**依赖：** 无

**步骤：**
1. 新增 `PathRaceConditionError(ToolError)` 类
2. category = "TOCTOU 竞态"

**验证：**
- `from mewcode.tools.errors import PathRaceConditionError` 可导入

---

## T5: Sandbox.safe_open

**文件：** 修改 `mewcode/tools/sandbox.py`

**依赖：** T4

**步骤：**
1. import os + contextmanager
2. 在 Sandbox 类内新增 `@contextmanager safe_open(self, raw_path, mode="r", encoding="utf-8")` 方法
3. 实现 plan 2.7 节伪代码：resolve → open → fstat/lstat 比对 → yield → close
4. 处理 binary 模式（不带 encoding）
5. Windows 上某些 OSError 宽容（不当作竞态）

**验证：**
- 写一个临时文件，with sandbox.safe_open(...) as f: f.read() 正常工作
- 跑 Sandbox 既有测试不退化

---

## T6: policy.py

**文件：** 新建 `mewcode/permissions/policy.py`

**依赖：** T1, T2, T3

**步骤：**
1. 定义 `Decision` frozen dataclass (action/reason/error_category)
2. 定义 `PermissionPolicy` 类：
   - `__init__(cwd)`：调 load_all 加载三层 YAML
   - `mode` property：mode_override 或 config.mode
   - `reload()`：重加载 + 清空 session
   - `add_session_allow / deny / set_mode_override`
   - `check(tool_name, params) -> Decision`：按 plan 2.4 节实现五层
3. 关键：黑名单触发时 error_category="黑名单拦截"，规则触发用"权限拒绝"

**验证：**
- 单测覆盖：
  - rm -rf / → deny + 黑名单拦截
  - git status (无规则) → ask
  - git status (有 allow rule) → allow
  - mode=yolo + 普通命令 → allow（黑名单仍拦）

---

## T7: interactive.py

**文件：** 新建 `mewcode/permissions/interactive.py`

**依赖：** T2

**步骤：**
1. 定义 `PermissionAsker` 类（懒构造 PromptSession）
2. `async ask(tool_name, target, cwd) -> str`：
   - 打印 4 行选项 + prompt
   - 解析答案 → "once" / "session" / "forever" / "deny"
   - Ctrl+C → 抛 ConfirmCancelled
   - EOF → "deny"
3. `_format_call`：动词式（Bash/Read/Wrote/Edit/Glob/Search）
4. `_write_to_local_yaml`：追加 allow 列表，去重

**验证：**
- 单测 mock prompt 输入 "y"/"s"/"a"/""/EOF，验证返回值

---

## T8: chat.engine 集成

**文件：** 修改 `mewcode/chat/engine.py`

**依赖：** T6, T7

**步骤：**
1. import PermissionPolicy, PermissionAsker, parse_rule
2. `run_turn` 签名增加 `policy: PermissionPolicy | None = None`,
   `asker: PermissionAsker | None = None`
3. 透传到 `_agent_loop` → `_execute_tool_batch`
4. `_execute_tool_batch` 在工具实际执行前调 `_check_permission(...)`：
   - policy.check → Decision
   - deny → 直接返回 ToolResultBlock
   - ask → 调 asker.ask → 处理四种回答
   - allow → 进入正常执行流（保留 DangerLevel.DANGEROUS confirmer）
5. session 选项后 add_session_allow

**验证：**
- run_turn 不传 policy 时回退第四阶段行为（无权限检查）
- 145 个已有单测全过

---

## T9: /permissions 命令

**文件：** 修改 `mewcode/commands/registry.py` + `builtin.py`

**依赖：** T6

**步骤：**
1. CommandContext 增加 `policy: PermissionPolicy | None = None` 字段
2. 实现 `_handle_permissions(ctx)` 主分发器
3. 实现 6 个子命令 handler:
   - show / allow / deny / mode / reload / init
4. init 时同时检查 .gitignore 追加 .mewcode/permissions.local.yaml

**验证：**
- `/permissions show` 打印当前规则
- `/permissions allow "Bash(test*)"` 添加成功
- `/permissions init` 创建模板 + 改 .gitignore

---

## T10: main + repl 装配

**文件：** 修改 `mewcode/main.py` + `mewcode/repl/main_loop.py`

**依赖：** T6, T7, T8, T9

**步骤：**
1. main.py 启动时构造 PermissionPolicy(cwd) + PermissionAsker
2. yolo 模式时打印警告横幅
3. 透传到 run_repl
4. run_repl 透传到 chat.run_turn 与 CommandContext
5. /clear 后调 policy.reload()

**验证：**
- python -m mewcode 启动正常
- yolo 模式启动看到 ⚠️ 警告

---

## T11: 单元测试合一

**文件：** 7 个新测试文件

**依赖：** T1-T10

**步骤：**
1. `test_blocklist.py`：5 条致命命令必拒；5 条正常命令必通过
2. `test_permissions_rules.py`：parse_rule 合法/非法；matches 前缀语义
3. `test_permissions_loader.py`：缺失文件、YAML 错误、三层合并
4. `test_permissions_policy.py`：五层防御 + 三种模式 + 优先级
5. `test_permissions_interactive.py`：4 种回答 + 写盘
6. `test_sandbox_toctou.py`：mock symlink race，safe_open 抛错
7. `test_permissions_command.py`：6 个子命令

**验证：**
- pytest 新测试全过（预计 25-30 个）

---

## T12: test_chat_round_loop.py 适配

**文件：** 修改 `tests/test_chat_round_loop.py`

**依赖：** T8

**步骤：**
1. 测试用 stub policy（默认 allow 一切，避免阻塞）
2. 增加新测试 `test_权限拒绝不终止Loop`：
   - stub policy 第一个工具 deny，第二个 allow
   - run_turn 完成；R2 答复含权限错误信息
   - messages 历史包含 deny 的 ToolResultBlock

**验证：**
- 既有 11 个测试全过 + 新增 1 个

---

## T13: 真实 API 端到端

**文件：** 新建 `scripts/verify_permissions.py`

**依赖：** T11

**步骤：**
1. 构造临时项目 + 写入测试 YAML：
   ```yaml
   mode: default
   allow:
     - "Bash(git *)"
   deny:
     - "Bash(rm *)"
   ```
2. 启动 mewcode 内部 API（不走 REPL）
3. 测试三个场景：
   - 模型调 Bash(git status) → 通过
   - 模型调 Bash(rm test.txt) → 拒绝 + 模型在 R2 含错误信息
   - 模型调 Bash(rm -rf /) → 黑名单拦截
4. 断言行为正确

**验证：**
- 脚本输出 "✓ 权限系统端到端验证通过"

---

## T14: 默认配置 + .gitignore

**文件：** 修改 `.gitignore`

**依赖：** T9

**步骤：**
1. .gitignore 追加 `.mewcode/permissions.local.yaml`（手动加，避免依赖 init）
2. 项目根新建 `.mewcode/permissions.yaml` 作为示例（可选）

**验证：**
- git check-ignore .mewcode/permissions.local.yaml 显示忽略

---

## T15: 全量验收

**文件：** 新建 `docs/06/checklist.md` + `docs/06/acceptance-report.md`

**依赖：** T11, T12, T13

**步骤：**
1. 写 checklist.md（已有 spec AC，再扩展手工项）
2. 跑全量回归：
   - pytest tests/ -q（145 + 30 ≈ 175）
   - verify_t9 / verify_t18 / verify_t19 / verify_round_loop /
     verify_agent_loop / verify_plan_mode / verify_cache_hit /
     verify_permissions（新）
3. 写 acceptance-report.md

**验证：**
- 全部 AC PASSED

---

## T16: commit + push

**文件：** 无新文件

**依赖：** T15

**步骤：**
1. `git status --short` 确认改动清单
2. 安全检查：findstr ad6cc9a8 .（确认无 key 泄漏）
3. `git add -A` + 详细 commit message
4. `git push origin main`

**验证：**
- GitHub 上看到第五阶段 commit

---

## 任务汇总

| #   | 任务 | 依赖 | 文件数 | 测试 |
|-----|------|------|--------|------|
| T1  | blocklist.py | 无 | 1 | - |
| T2  | rules.py | 无 | 1 | - |
| T3  | loader.py | T2 | 1 | - |
| T4  | errors.py + PathRaceConditionError | 无 | 1 修 | - |
| T5  | Sandbox.safe_open | T4 | 1 修 | - |
| T6  | policy.py | T1/T2/T3 | 1 | - |
| T7  | interactive.py | T2 | 1 | - |
| T8  | chat.engine 集成 | T6/T7 | 1 修 | - |
| T9  | /permissions 命令 | T6 | 2 修 | - |
| T10 | main + repl 装配 | T6/T7/T8/T9 | 2 修 | - |
| T11 | 单元测试合一 | T1-T10 | 7 | ✅ ~28 |
| T12 | test_chat_round_loop 适配 | T8 | 1 修 | ✅ +1 |
| T13 | 真实 API 端到端 | T11 | 1 | 真实 |
| T14 | 默认配置 + .gitignore | T9 | 1 修 | - |
| T15 | 全量验收 | T11/T12/T13 | 2 | 全量 |
| T16 | commit + push | T15 | - | - |

**单测累计**：约 30 个新增 + 145 个已有 + 1 个 round_loop 新增 = **~175**

---

## 自检结论

- ✅ **plan 覆盖**：13 个 plan 模块都有任务对应
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落
- ✅ **依赖链**：执行图有合法拓扑序，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（PermissionPolicy / PermissionAsker /
  Decision / Rule / PermissionConfig / safe_open 等）
- ✅ **不退化覆盖**：T11 跑全套回归 + T12 适配 round_loop + T13 端到端
- ✅ **API 兼容**：run_turn 签名通过新增可选参数兼容；不传 policy 时回退
  第四阶段行为
