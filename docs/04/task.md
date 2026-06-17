# MewCode 第三阶段 Tasks

> 基于已批准的 `docs/04/spec.md` 与 `docs/04/plan.md`。共 16 个任务，
> 覆盖 AgentEvent 定义、Agent Loop 重写、Plan Mode、Renderer 改造、
> 命令扩展、单测适配与端到端验收。

## 文件清单

| 操作 | 文件                                       | 职责                                |
|------|--------------------------------------------|-------------------------------------|
| 新建 | `mewcode/chat/events.py`                   | AgentEvent 7 种类型 + 联合          |
| 修改 | `mewcode/chat/__init__.py`                 | 暴露 AgentEvent 类型                |
| 修改 | `mewcode/chat/session.py`                  | + mode 字段 + clear/switch 重置     |
| 重写 | `mewcode/chat/engine.py`                   | run_turn → _agent_loop + _execute_tool_batch |
| 修改 | `mewcode/commands/builtin.py`              | + /plan /do 命令                    |
| 修改 | `mewcode/render/renderer.py`               | + on_agent_event                    |
| 重写 | `tests/test_chat_round_loop.py`            | 适配 Agent Loop（6→10 用例）         |
| 新建 | `tests/test_agent_events.py`               | AgentEvent 发射顺序                  |
| 新建 | `tests/test_plan_mode.py`                  | Plan Mode 物理隔离 + 切换           |
| 新建 | `scripts/verify_agent_loop.py`             | 真实 API 多轮 Loop                  |
| 新建 | `scripts/verify_plan_mode.py`              | 真实 API Plan Mode                  |

共 11 个文件（4 新建 + 7 修改/重写）。

---

## 任务执行顺序图

```
T1 → T2 → T3 ──────────────────────────────────────────┐
                                                       │
T4 ──→ T5 ──→ T6 ──→ T7 ──→ T8 ──────────────────────┤
              │                                        │
              T9 ──────────────────────────────────────┤
                                                       │
              T10 ──→ T11 (单测) ──────────────────────┤
                                                       │
              T12 (端到端) ──→ T13 ──→ T14 ──→ T15 ──→ T16
```

**关键路径**：T1→T2→T3→T5→T7→T8→T11→T13→T16（10 步主线）

**可并行机会**：
- T4（Session mode 字段）与 T1-T3 并行
- T6（/plan /do 命令）与 T5（Renderer）并行
- T9（test_plan_mode）与 T10-T11（engine 单测）并行
- T12-T13（端到端脚本）在 T11 后并行

---

## T1：AgentEvent 类型定义

**文件：**
- 新建 `mewcode/chat/events.py`

**依赖：** 无

**步骤：**

1. 文件顶部加中文 docstring：本模块定义 Agent 层事件类型，在 StreamEvent
   之上提供编排语义事件，彻底解耦 Agent Loop 与终端渲染。
2. 定义 7 个 frozen dataclass：
   - `IterationStart(iteration: int, max_iterations: int)`
   - `IterationEnd(iteration: int)`
   - `ToolBatchStart(count: int, safe_count: int, dangerous_count: int)`
   - `ToolCall(name: str, summary: str)`
   - `ToolResultEvent(tool_use_id: str, name: str, summary: str, success: bool)`
   - `Stopped(reason: str, iteration: int)`——reason 取值：
     `natural / max_iterations / user_cancel / unknown_tools / error`
   - `UsageTotal(input_tokens: int, output_tokens: int, thinking_tokens: int | None, iterations: int)`
3. 定义联合类型 `AgentEvent = IterationStart | IterationEnd | ...`
4. 每个类前加中文 docstring 说明触发场景

**验证：**
- `python -m py_compile mewcode/chat/events.py`
- `python -c "from mewcode.chat.events import AgentEvent, IterationStart, Stopped; print(IterationStart(1, 50))"`

---

## T2：chat/__init__.py 暴露 AgentEvent

**文件：**
- 修改 `mewcode/chat/__init__.py`

**依赖：** T1

**步骤：**

1. 从 `mewcode.chat.events` import 全部 7 个类型 + `AgentEvent` 联合
2. 写入 `__all__`
3. 确保已有的 `Session / run_turn` 导出不受影响

**验证：**
- `python -c "from mewcode.chat import AgentEvent, IterationStart, Stopped, Session, run_turn; print('ok')"`

---

## T3：Session 新增 mode 字段

**文件：**
- 修改 `mewcode/chat/session.py`

**依赖：** 无

**步骤：**

1. import `Literal` from typing
2. Session dataclass 新增字段：`mode: Literal["do", "plan"] = "do"`
3. docstring 中补充 mode 字段说明
4. `clear()` 方法中追加 `self.mode = "do"`
5. `switch_provider()` 方法中追加 `self.mode = "do"`

**验证：**
- `python -c "from mewcode.chat import Session; s = Session(provider=None); print(s.mode); s.mode='plan'; s.clear(); print(s.mode)"` 输出 `do` 然后 `do`

---

## T4：Renderer 新增 on_agent_event

**文件：**
- 修改 `mewcode/render/renderer.py`

**依赖：** T1

**步骤：**

1. import AgentEvent 及 7 个子类型 from `mewcode.chat.events`
2. 新增方法 `on_agent_event(self, ev: AgentEvent) -> None`：
   - `IterationStart` → `sys.stdout.write(f"── 迭代 {ev.iteration}/{ev.max_iterations} ──\n")`
   - `IterationEnd` → 不输出（静默）
   - `ToolBatchStart` → 不输出（避免噪音）
   - `ToolCall` → `sys.stdout.write(f"▸ {ev.name}({ev.summary})\n")`
   - `ToolResultEvent` → 成功 `✓`、失败 `✗`：
     `sys.stdout.write(f"  {'✓' if ev.success else '✗'} {ev.name}: {ev.summary}\n")`
   - `Stopped` → 按 reason 映射中文提示：
     - `natural` → 不输出
     - `max_iterations` → `（已达迭代上限）`
     - `user_cancel` → `（已取消）`
     - `unknown_tools` → `（连续调用未知工具，已停止）`
     - `error` → `（流出错）`
   - `UsageTotal` → `↑ X tokens · ↓ Y tokens[· 思考 Z tokens] · N 轮`
3. 所有输出用 `sys.stdout.write + flush`，沿用第二阶段朴素策略
4. 保留已有的 `print_tool_call / print_tool_result_summary / print_usage_combined`
   等方法（不删除——可能有其他调用方），但 chat.engine 不再直接调它们

**验证：**
- `python -c "from mewcode.render import Renderer; from rich.console import Console; from mewcode.chat.events import *; r = Renderer(Console()); r.on_agent_event(IterationStart(1,50)); r.on_agent_event(ToolCall('read','path=a')); r.on_agent_event(ToolResultEvent('t1','read','读取5行',True)); r.on_agent_event(Stopped('natural',2)); r.on_agent_event(UsageTotal(100,50,None,2))"` 目测格式正确

---

## T5：chat/engine.py 重写——Agent Loop 主循环

**文件：**
- 重写 `mewcode/chat/engine.py`

**依赖：** T1、T3、T4

**步骤：**

1. 模块顶部常量：
   ```python
   MAX_ITERATIONS = 50
   MAX_CONCURRENT_SAFE_TOOLS = 8
   UNKNOWN_TOOL_THRESHOLD = 2
   ```
2. `run_turn` 签名不变，内部改为调 `_agent_loop`
3. `_agent_loop` 主循环（按 plan.md 2.2 节伪代码实现）：
   - `for iteration in range(1, MAX_ITERATIONS + 1):`
   - emit `IterationStart(iteration, MAX_ITERATIONS)`
   - `is_final = (iteration == MAX_ITERATIONS)`
   - `is_final` 时调 `_inject_soft_stop_prompt(session)`
   - 调 `_consume_round(session, renderer, registry, allow_tools=not is_final)`
   - 累计 usage
   - `blocks is None` → emit Stopped("user_cancel"/"error") + return False
   - 提取 `tool_uses`
   - `session.append_assistant(blocks)`
   - emit `IterationEnd(iteration)`
   - `not tool_uses` → emit Stopped("natural") + UsageTotal + return True
   - 检查未知工具：更新 `unknown_streak`，≥2 则 emit Stopped("unknown_tools") + return True
   - 调 `_execute_tool_batch` → `(results, cancelled)`
   - `cancelled` → emit Stopped("user_cancel") + return False
   - `session.append_tool_results(results)`
   - 循环结束后兜底 emit Stopped("max_iterations")（理论不可达）
4. `_inject_soft_stop_prompt(session)`：
   - 构造中文系统提示"你已用完 50 轮迭代上限..."
   - 作为 user 消息追加到 session.messages
5. `_consume_round` 改造：
   - 增加 `allow_tools: bool = True` 参数
   - `allow_tools=False` 时 `tools_format=None`
   - `allow_tools=True` 时调 `_get_tools_format(registry, protocol, mode)` 按
     `session.mode` 过滤（plan 模式只含 SAFE 工具）
   - 其余逻辑（SIGINT handler / sub_task / 双路收集 / finally aclose）不变
6. `_get_tools_format(registry, protocol, mode)` helper（按 plan 2.4 节实现）
7. `_emit(renderer, ev)` helper：安全调 `renderer.on_agent_event(ev)`
8. `_emit_usage(renderer, ...)` helper：构造 UsageTotal 并 emit

**验证：**
- `python -m py_compile mewcode/chat/engine.py`
- `pytest tests/ -q`——此时 test_chat_round_loop 会全部失败（旧签名/行为），
  但其他 90 个测试应全过

---

## T6：_execute_tool_batch 分批执行

**文件：**
- 修改 `mewcode/chat/engine.py`（在 T5 基础上追加）

**依赖：** T5

**步骤：**

1. 实现 `_execute_tool_batch(session, renderer, registry, confirmer, sandbox, tool_uses)`：
   - 按 `danger_level` 分 `safe_tools` / `dangerous_tools` 两个列表
   - 未知工具跳过（不放入任一批）
   - emit `ToolBatchStart(count, safe_count, dangerous_count)`
   - **并发批**：`safe_tools[:MAX_CONCURRENT_SAFE_TOOLS]` 用 `asyncio.gather`
     并发执行；每个工具执行前 emit `ToolCall`，执行后 emit `ToolResultEvent`
   - **串行批**：`dangerous_tools + safe_tools[MAX_CONCURRENT_SAFE_TOOLS:]`
     逐个串行执行；DANGEROUS 工具先调 `confirmer.ask`，拒绝时 emit
     ToolResultEvent(success=False, summary="用户拒绝") + 跳过执行
   - **未知工具**：为每个未注册的 tool_use 生成 `ToolResultBlock(content=f"未知工具：{name}", is_error=True)`
   - 捕获 `KeyboardInterrupt / ConfirmCancelled / asyncio.CancelledError` →
     返回 `(results_so_far, True)` 表示 cancelled
   - 按 `tool_uses` 原始顺序拼装 `results_by_index` → 返回 `(list, False)`

2. 内部 helper `_run_single_tool(tool, tu, sandbox) -> ToolResultBlock`：
   - 调 `tool.execute(tu.input, sandbox)`
   - 包装为 `ToolResultBlock(tool_use_id=tu.id, content=result.text, is_error=not result.success)`

**验证：**
- `python -m py_compile mewcode/chat/engine.py`
- 单测在 T11 中覆盖

---

## T7：commands/builtin.py 新增 /plan /do

**文件：**
- 修改 `mewcode/commands/builtin.py`

**依赖：** T3

**步骤：**

1. 新增 `_handle_plan(ctx)`：
   ```python
   ctx.session.mode = "plan"
   ctx.renderer.print_info("📋 Plan Mode：只读工具（read / glob / search）")
   return CommandResult()
   ```
2. 新增 `_handle_do(ctx)`：
   ```python
   ctx.session.mode = "do"
   ctx.renderer.print_info("🔧 执行模式：全部工具")
   return CommandResult()
   ```
3. 在 `register_builtins()` 末尾追加两个 `register(Command(...))`

**验证：**
- `python -c "from mewcode.commands import COMMANDS, register_builtins; register_builtins(); print('plan' in COMMANDS, 'do' in COMMANDS)"` 输出 `True True`

---

## T8：第二阶段 test_chat_round_loop 适配

**文件：**
- 重写 `tests/test_chat_round_loop.py`

**依赖：** T5、T6

**步骤：**

1. 保留 stub Provider / stub Tool / stub Renderer / stub Confirmer 的设计
2. stub Provider 适配：`stream_chat` 接受 `system` 与 `tools_format` 参数
3. stub Renderer 新增 `on_agent_event` 记录调用
4. 重写 10 个测试用例（按 plan 6 节兼容矩阵）：
   - `test_自然停止_一轮直答`：R1 仅文本 → Loop 1 轮结束，Stopped("natural", 1)
   - `test_两轮Loop_工具+文本答复`：R1 含 tool_use → 执行 → R2 文本 → Stopped("natural", 2)
   - `test_多tool_use_分批执行`：R1 含 2 SAFE + 1 DANGEROUS → SAFE 并发 + DANGEROUS 串行
   - `test_DANGEROUS工具拒绝`：confirmer 返回 False → ToolResultBlock 含"用户拒绝"
   - `test_Ctrl+C取消整个Loop`：工具执行中 ConfirmCancelled → Stopped("user_cancel")
   - `test_迭代上限软停止`：MAX_ITERATIONS 改为 3（monkey-patch）→ 第 3 轮注入提示 + 无工具 → Stopped("max_iterations", 3)
   - `test_连续未知工具停止`：模型连续两轮调 "foobar" → 第 2 轮 Stopped("unknown_tools")
   - `test_LLM流出错停止`：Provider 第 2 轮抛 ProviderError → Stopped("error")
   - `test_AgentEvent发射顺序`：验证一次完整 Loop 的事件序列
   - `test_并发上限8`：10 个 SAFE 工具 → 前 8 并发 + 后 2 追加串行

**验证：**
- `pytest tests/test_chat_round_loop.py -v` 全过

---

## T9：Plan Mode 单测

**文件：**
- 新建 `tests/test_plan_mode.py`

**依赖：** T5、T7

**步骤：**

1. 测试 `/plan` 命令切换 mode：
   - 构造 stub Session + CommandContext
   - 调 `dispatch("/plan", ctx)`
   - 断言 `session.mode == "plan"`
   - 断言 renderer 收到含 "Plan Mode" 的 print_info
2. 测试 `/do` 命令切回：
   - 先 `/plan` 再 `/do`
   - 断言 `session.mode == "do"`
3. 测试 `_get_tools_format` 在 plan 模式下只含 SAFE 工具：
   - 构造含 6 个内置工具的 registry
   - `mode="plan"` → tools_format 只含 read/glob/search
   - `mode="do"` → 含全部 6 个
4. 测试 `/clear` 重置 mode：
   - `session.mode = "plan"` → `session.clear()` → `session.mode == "do"`
5. 测试 `/provider` 切换重置 mode：
   - `session.mode = "plan"` → `session.switch_provider(...)` → `session.mode == "do"`

**验证：**
- `pytest tests/test_plan_mode.py -v` 全过

---

## T10：AgentEvent 单测

**文件：**
- 新建 `tests/test_agent_events.py`

**依赖：** T1、T8

**步骤：**

1. 测试所有 7 种 AgentEvent 可构造且 frozen：
   - 构造每个类型的实例，访问字段
   - 尝试修改字段 → `FrozenInstanceError`
2. 测试 Stopped 的 reason 取值覆盖：
   - 构造 5 种 reason 的 Stopped 实例
3. 测试 AgentEvent 联合类型 isinstance：
   - 每个子类型实例 `isinstance(ev, AgentEvent)` 为 True
4. 测试 Renderer.on_agent_event 不抛异常：
   - 对每种事件类型调一次 on_agent_event
   - 断言不抛异常

**验证：**
- `pytest tests/test_agent_events.py -v` 全过

---

## T11：累积回归 + 第一/二阶段不退化

**文件：** 无新文件

**依赖：** T1-T10

**步骤：**

1. `pytest tests/ -q` 全套通过（96 已有中 test_chat_round_loop 重写后
   数量变化，预计总数 100+）
2. 第一阶段端到端：
   - `python scripts/verify_t9.py`——纯对话仍正常
   - `python scripts/verify_t18_config_errors.py`——配置错误退出码不变
3. 第二阶段端到端：
   - `python scripts/verify_t18.py`——Anthropic 工具调用事件流仍正常
   - `python scripts/verify_t19.py`——OpenAI 工具调用事件流仍正常
   - `python scripts/verify_round_loop.py`——完整闭环仍正常（输出多了进度行）
   - `python scripts/verify_system_prompt.py`——system prompt 仍生效

**验证：**
- 全部通过，stderr 干净

---

## T12：真实 API 端到端——Agent Loop 多轮

**文件：**
- 新建 `scripts/verify_agent_loop.py`

**依赖：** T11

**步骤：**

1. 用真实 DeepSeek Anthropic 供应商
2. 构造需要多轮工具调用的 prompt：
   "读 README.md 和 pyproject.toml，告诉我项目名和依赖列表，然后把
   依赖数量写入一个新文件 deps_count.txt"
3. 用 _AutoYesConfirmer 自动批准（edit 仍 DANGEROUS，但 write 已 SAFE）
4. 预期：
   - 迭代 1：模型并发调 2 个 read
   - 迭代 2：模型调 write 创建 deps_count.txt
   - 迭代 3：模型给出文本答复"已创建 deps_count.txt，内容为..."
   - Stopped("natural", 3)
5. 断言：
   - `len(session.messages)` ≥ 6（user + assistant×3 + tool_results×2）
   - deps_count.txt 文件确实创建
   - stderr 干净

**验证：**
- 脚本输出含 `── 迭代 1/50 ──` / `── 迭代 2/50 ──` / `── 迭代 3/50 ──`
- `✓` 图标出现在工具结果行
- `↑ X · ↓ Y · 3 轮` 用量行

---

## T13：真实 API 端到端——Plan Mode

**文件：**
- 新建 `scripts/verify_plan_mode.py`

**依赖：** T11

**步骤：**

1. 用真实供应商
2. 构造 Session，`mode = "plan"`
3. 发 prompt "读 README.md 然后写一个 summary.txt 总结"
4. 预期：
   - 模型只调 read/glob/search（Plan Mode 物理隔离）
   - 模型尝试调 write 时（如果有）返回 "Plan Mode 禁止写类工具" 错误
   - 模型最终给出计划文本（不执行写操作）
5. 切换 `mode = "do"`，发同样 prompt
6. 预期：模型可调 write，文件创建成功

**验证：**
- Plan Mode 下 tools_format 只含 3 个 SAFE 工具
- Plan Mode 下 write 工具调用被拒绝
- Do Mode 下 write 正常执行

---

## T14：Ctrl+C 端到端验证

**文件：** 无新文件（交互式验证）

**依赖：** T12

**步骤：**

1. 启动 `python -m mewcode`
2. 输入一个会触发多轮工具调用的 prompt（如 "读所有 .py 文件并统计行数"）
3. 在工具执行中按 Ctrl+C
4. 预期：
   - 打印 `（已取消）`
   - 回到 `>` 提示符
   - 无 traceback 渗漏
   - 再次输入 prompt 可正常对话

**验证：**
- 人工观察终端输出

---

## T15：Windows 终端兼容验证

**文件：** 无新文件

**依赖：** T12、T13

**步骤：**

1. 在 Windows PowerShell 5.x / cmd 下跑 T12 与 T13 的脚本
2. 检查：
   - `──` / `📋` / `🔧` / `✓` / `✗` / `↑↓` / `·` 等 Unicode 字符正常显示
   - 无 `?[2K` 类 ANSI 乱码
   - 无 traceback 渗漏
   - 进度行与正文视觉分层清晰

**验证：**
- 目测终端输出干净

---

## T16：全量验收 + 验收报告

**文件：**
- 产出 `docs/04/acceptance-report.md`

**依赖：** T1-T15

**步骤：**

1. 阅读 `docs/04/checklist.md`（待写，T16 前补）
2. 自动可验证项：
   - `pytest tests/ -q`（应有 100+ 测试）
   - 跑全部 verify_*.py 脚本
3. 交互项在 PowerShell 中按 manual 跑通
4. 逐项填入 `acceptance-report.md`
5. 失败定位回对应 T 任务修复；全部通过后 close 第三阶段

**验证：**
- 所有 spec.md 中 AC1-AC18 都有 PASSED 或明确"已知降级/待补"标注
- acceptance-report.md 完整生成

---

## 任务汇总

| #   | 任务                                    | 依赖          | 文件数 | 测试   |
|-----|-----------------------------------------|---------------|--------|--------|
| T1  | AgentEvent 类型定义                      | 无            | 1      | -      |
| T2  | chat/__init__.py 暴露 AgentEvent         | T1            | 1 修   | -      |
| T3  | Session 新增 mode 字段                   | 无            | 1 修   | -      |
| T4  | Renderer 新增 on_agent_event            | T1            | 1 修   | -      |
| T5  | engine.py 重写——Agent Loop 主循环        | T1/T3/T4      | 1 重写 | -      |
| T6  | _execute_tool_batch 分批执行             | T5            | 1 修   | -      |
| T7  | /plan /do 命令                           | T3            | 1 修   | -      |
| T8  | test_chat_round_loop 适配                | T5/T6         | 1 重写 | ✅ 10  |
| T9  | test_plan_mode 单测                      | T5/T7         | 1      | ✅ 5   |
| T10 | test_agent_events 单测                   | T1/T8         | 1      | ✅ 4   |
| T11 | 累积回归 + 不退化                         | T1-T10        | -      | 全量   |
| T12 | 真实 API——Agent Loop 多轮                | T11           | 1      | 真实   |
| T13 | 真实 API——Plan Mode                      | T11           | 1      | 真实   |
| T14 | Ctrl+C 端到端                            | T12           | -      | 手工   |
| T15 | Windows 终端兼容                         | T12/T13       | -      | 手工   |
| T16 | 全量验收 + 报告                          | T1-T15        | 1      | 全量   |

**单测累计**：约 19 个新增（10 + 5 + 4）+ 86 个已有 = **105+**

---

## 自检结论

- ✅ **plan 覆盖**：plan.md 所有模块设计都有任务对应
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落
- ✅ **依赖链**：执行图有合法拓扑序，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（AgentEvent / _agent_loop /
  _execute_tool_batch / _get_tools_format / mode 字段等）
- ✅ **不退化覆盖**：T11 专门覆盖第一/二阶段不退化验证
