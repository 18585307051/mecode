# MewCode 第三阶段 Plan

> 基于已批准的 `docs/04/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第二阶段的兼容矩阵。

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│  REPL 主循环 (repl/main_loop.py)                                │
│    PromptSession → dispatch / run_turn                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        │                                      │
        ▼                                      ▼
┌──────────────────┐              ┌─────────────────────────┐
│ commands/        │              │ chat/engine.py          │
│  registry.py     │              │   run_turn()            │
│  builtin.py      │              │     └─ agent_loop()     │
│  (新增 /plan /do)│              │         └─ _consume_round() × N
└──────────────────┘              │         └─ _execute_tool_batch()
                                  └──────────┬──────────────┘
                                             │ emit
                                  ┌──────────▼──────────────┐
                                  │ chat/events.py (新)      │
                                  │   AgentEvent 联合类型     │
                                  │   IterationStart/End     │
                                  │   ToolBatchStart         │
                                  │   ToolCall / ToolResult  │
                                  │   Stopped / UsageTotal   │
                                  └──────────┬──────────────┘
                                             │ subscribe
                                  ┌──────────▼──────────────┐
                                  │ render/renderer.py       │
                                  │   on_agent_event(ev)     │
                                  │   print_progress / ...   │
                                  └─────────────────────────┘

┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ providers/       │   │ tools/           │   │ config/          │
│  (不变)          │   │  (不变)          │   │  (不变)          │
│  stream_chat     │   │  Tool.execute   │   │                  │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

### 层次关系

| 层 | 职责 | 变化 |
|----|------|------|
| REPL | 读取输入、命令分发、调用 run_turn | 不变 |
| commands | 斜杠命令 | 新增 /plan /do |
| chat.engine | Agent Loop 编排 | **重写** |
| chat.events | AgentEvent 定义 | **新文件** |
| chat.session | 会话状态 | 新增 mode 字段 |
| render | 终端渲染 | 新增 on_agent_event |
| providers | 协议适配 | 不变 |
| tools | 工具实现 | 不变 |
| config | 配置加载 | 不变 |

## 2. 模块设计

### 2.1 chat/events.py（新文件）

定义 AgentEvent 联合类型，所有事件为 frozen dataclass：

```python
@dataclass(frozen=True)
class IterationStart:
    iteration: int        # 1-based
    max_iterations: int   # 50

@dataclass(frozen=True)
class IterationEnd:
    iteration: int

@dataclass(frozen=True)
class ToolBatchStart:
    count: int            # 本批 tool_use 总数
    safe_count: int       # 并发批数量
    dangerous_count: int  # 串行批数量

@dataclass(frozen=True)
class ToolCall:
    name: str
    summary: str          # Tool.render_call_summary(params)

@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str
    name: str
    summary: str          # Tool.render_result_summary(result)
    success: bool

@dataclass(frozen=True)
class Stopped:
    reason: str           # natural / max_iterations / user_cancel /
                          # unknown_tools / error
    iteration: int        # 实际跑了几轮

@dataclass(frozen=True)
class UsageTotal:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None
    iterations: int

AgentEvent = (
    IterationStart | IterationEnd | ToolBatchStart
    | ToolCall | ToolResultEvent | Stopped | UsageTotal
)
```

### 2.2 chat/engine.py（重写）

#### 公开接口（签名不变）

```python
async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
    registry: ToolRegistry | None = None,
    confirmer: Confirmer | None = None,
    sandbox: Sandbox | None = None,
) -> bool:
```

#### 内部结构

```python
MAX_ITERATIONS = 50
MAX_CONCURRENT_SAFE_TOOLS = 8
UNKNOWN_TOOL_THRESHOLD = 2  # 连续未知工具阈值

async def run_turn(...) -> bool:
    session.append_user_text(user_input)
    return await _agent_loop(session, renderer, registry, confirmer, sandbox)

async def _agent_loop(...) -> bool:
    total_in = total_out = 0
    total_thinking = None
    unknown_streak = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        _emit(renderer, IterationStart(iteration, MAX_ITERATIONS))

        # 最后一轮（软停止）时不带 tools_format
        is_final = (iteration == MAX_ITERATIONS)
        if is_final:
            _inject_soft_stop_prompt(session)

        blocks, usage = await _consume_round(
            session, renderer, registry,
            allow_tools=not is_final,
        )

        if usage:
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            if usage.thinking_tokens is not None:
                total_thinking = (total_thinking or 0) + usage.thinking_tokens

        if blocks is None:
            # 用户取消或流出错
            reason = "user_cancel" if _was_cancelled else "error"
            _emit(renderer, Stopped(reason, iteration - 1))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration - 1)
            return False

        tool_uses = [b for b in blocks if isinstance(b, ToolUseBlock)]
        session.append_assistant(blocks)
        _emit(renderer, IterationEnd(iteration))

        # 停止条件 1: 自然完成
        if not tool_uses:
            _emit(renderer, Stopped("natural", iteration))
            _emit_usage(renderer, total_in, total_out, total_thinking, iteration)
            return True

        # 停止条件 4: 连续未知工具
        unknown_count = sum(1 for tu in tool_uses if registry.get(tu.name) is None)
        if unknown_count > 0:
            unknown_streak += 1
            if unknown_streak >= UNKNOWN_TOOL_THRESHOLD:
                _emit(renderer, Stopped("unknown_tools", iteration))
                _emit_usage(...)
                return True
        else:
            unknown_streak = 0

        # 停止条件 2: 迭代上限（下一轮就是第 51 轮，不进 for）
        # → for 循环自然结束，走下面的 max_iterations 收尾
        # 注：实际的软停止在 is_final 那轮处理

        # 执行工具
        tool_results, cancelled = await _execute_tool_batch(
            session, renderer, registry, confirmer, sandbox, tool_uses,
        )
        if cancelled:
            _emit(renderer, Stopped("user_cancel", iteration))
            _emit_usage(...)
            return False

        session.append_tool_results(tool_results)

    # for 循环正常结束 = 跑完 50 轮
    # 但最后一轮是软停止轮，应该已在循环内 return
    # 兜底（理论不可达）
    _emit(renderer, Stopped("max_iterations", MAX_ITERATIONS))
    _emit_usage(...)
    return True
```

#### _consume_round（改造）

从第二阶段的 `_consume_round` 演化：
- 增加 `allow_tools: bool` 参数（软停止轮传 False）
- `allow_tools=False` 时 `tools_format=None`，模型只能输出文本
- 其余逻辑（SIGINT handler / sub_task / 双路收集 / finally aclose）不变

#### _execute_tool_batch（新函数）

```python
async def _execute_tool_batch(
    session, renderer, registry, confirmer, sandbox, tool_uses: list[ToolUseBlock],
) -> tuple[list[ToolResultBlock], bool]:
    """分批执行 tool_use，返回 (results, cancelled)。"""

    safe_tools = []  # (index, tu, tool)
    dangerous_tools = []
    for idx, tu in enumerate(tool_uses):
        tool = registry.get(tu.name)
        if tool is None:
            # 未知工具：单独处理，结果放回原位
            continue
        if tool.danger_level == DangerLevel.SAFE:
            safe_tools.append((idx, tu, tool))
        else:
            dangerous_tools.append((idx, tu, tool))

    _emit(renderer, ToolBatchStart(
        count=len(tool_uses),
        safe_count=len(safe_tools),
        dangerous_count=len(dangerous_tools),
    ))

    results_by_index: dict[int, ToolResultBlock] = {}

    # 1) 并发执行 SAFE 工具（上限 8）
    safe_to_run = safe_tools[:MAX_CONCURRENT_SAFE_TOOLS]
    safe_overflow = safe_tools[MAX_CONCURRENT_SAFE_TOOLS:]

    async def _run_one(idx, tu, tool):
        _emit(renderer, ToolCall(tool.name, tool.render_call_summary(tu.input)))
        result = await tool.execute(tu.input, sandbox)
        _emit(renderer, ToolResultEvent(
            tu.id, tool.name, tool.render_result_summary(result), result.success
        ))
        return idx, ToolResultBlock(
            tool_use_id=tu.id, content=result.text, is_error=not result.success
        )

    try:
        completed = await asyncio.gather(
            *[_run_one(i, tu, t) for i, tu, t in safe_to_run]
        )
        for idx, tr in completed:
            results_by_index[idx] = tr
    except asyncio.CancelledError:
        return [], True  # cancelled

    # 2) 串行执行 DANGEROUS 工具 + SAFE 溢出
    for idx, tu, tool in dangerous_tools + safe_overflow:
        try:
            _emit(renderer, ToolCall(tool.name, tool.render_call_summary(tu.input)))
            if tool.danger_level == DangerLevel.DANGEROUS and confirmer:
                approved = await confirmer.ask(tool.name)
                if not approved:
                    _emit(renderer, ToolResultEvent(
                        tu.id, tool.name, "用户拒绝", False
                    ))
                    results_by_index[idx] = ToolResultBlock(
                        tu.id, "用户拒绝执行此工具", is_error=True
                    )
                    continue
            result = await tool.execute(tu.input, sandbox)
            _emit(renderer, ToolResultEvent(
                tu.id, tool.name, tool.render_result_summary(result), result.success
            ))
            results_by_index[idx] = ToolResultBlock(
                tu.id, result.text, is_error=not result.success
            )
        except (KeyboardInterrupt, ConfirmCancelled, asyncio.CancelledError):
            return [], True

    # 3) 未知工具的 result
    for idx, tu in enumerate(tool_uses):
        if idx not in results_by_index:
            _emit(renderer, ToolCall(tu.name, "(未知工具)"))
            _emit(renderer, ToolResultEvent(
                tu.id, tu.name, "失败：未知工具", False
            ))
            results_by_index[idx] = ToolResultBlock(
                tu.id, f"未知工具：{tu.name}", is_error=True
            )

    # 按原始顺序返回
    return [results_by_index[i] for i in range(len(tool_uses))], False
```

### 2.3 chat/session.py（修改）

新增 `mode` 字段：

```python
@dataclass
class Session:
    ...
    mode: Literal["do", "plan"] = "do"
```

`clear()` 与 `switch_provider()` 中重置 `self.mode = "do"`。

### 2.4 chat/engine.py 中 Plan Mode 的 tools_format 过滤

`_consume_round` 根据 `session.mode` 过滤 tools_format：

```python
def _get_tools_format(registry, protocol, mode) -> list[dict] | None:
    if registry is None:
        return None
    if mode == "plan":
        # Plan Mode: 只保留 SAFE 工具
        tools = [t for t in registry if t.danger_level == DangerLevel.SAFE]
    else:
        tools = list(registry)

    if not tools:
        return None
    if protocol == "anthropic":
        return [{"name": t.name, "description": t.description,
                 "input_schema": t.parameters_schema} for t in tools]
    else:
        return [{"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": t.parameters_schema}} for t in tools]
```

注：不复用 `registry.to_anthropic_format()`，因为 Plan Mode 需要过滤子集。

### 2.5 commands/builtin.py（修改）

新增两个命令 handler：

```python
async def _handle_plan(ctx: CommandContext) -> CommandResult:
    ctx.session.mode = "plan"
    ctx.renderer.print_info("📋 Plan Mode：只读工具（read / glob / search）")
    return CommandResult()

async def _handle_do(ctx: CommandContext) -> CommandResult:
    ctx.session.mode = "do"
    ctx.renderer.print_info("🔧 执行模式：全部工具")
    return CommandResult()
```

在 `register_builtins()` 中注册：
```python
register(Command(name="plan", aliases=(), description="切换到 Plan Mode（只读工具）", handler=_handle_plan))
register(Command(name="do", aliases=(), description="切回执行模式（全部工具）", handler=_handle_do))
```

### 2.6 render/renderer.py（修改）

新增 `on_agent_event(ev: AgentEvent)` 方法，按事件类型输出：

```python
def on_agent_event(self, ev: AgentEvent) -> None:
    if isinstance(ev, IterationStart):
        sys.stdout.write(f"── 迭代 {ev.iteration}/{ev.max_iterations} ──\n")
        sys.stdout.flush()
    elif isinstance(ev, ToolBatchStart):
        pass  # 不输出（避免噪音）
    elif isinstance(ev, ToolCall):
        sys.stdout.write(f"▸ {ev.name}({ev.summary})\n")
        sys.stdout.flush()
    elif isinstance(ev, ToolResultEvent):
        icon = "✓" if ev.success else "✗"
        sys.stdout.write(f"  {icon} {ev.name}: {ev.summary}\n")
        sys.stdout.flush()
    elif isinstance(ev, Stopped):
        reason_map = {
            "natural": "",  # 自然完成不打印原因
            "max_iterations": "（已达迭代上限）",
            "user_cancel": "（已取消）",
            "unknown_tools": "（连续调用未知工具，已停止）",
            "error": "（流出错）",
        }
        msg = reason_map.get(ev.reason, "")
        if msg:
            sys.stdout.write(f"{msg}\n")
            sys.stdout.flush()
    elif isinstance(ev, UsageTotal):
        parts = [f"↑ {ev.input_tokens} tokens", f"↓ {ev.output_tokens} tokens"]
        if ev.thinking_tokens is not None:
            parts.append(f"思考 {ev.thinking_tokens} tokens")
        parts.append(f"{ev.iterations} 轮")
        sys.stdout.write(" · ".join(parts) + "\n")
        sys.stdout.flush()
```

### 2.7 chat/engine.py 中 _emit helper

```python
def _emit(renderer: Renderer, ev: AgentEvent) -> None:
    """安全地给 renderer 推 AgentEvent。"""
    try:
        renderer.on_agent_event(ev)
    except Exception:
        pass  # UI 渲染失败不影响 Agent 逻辑
```

## 3. 技术决策

### D1. 为什么完全替换 run_turn 而不是新增 agent_loop

**决策**：完全替换。

**理由**：
- Agent Loop 是单轮闭环的超集——无 tool_use 时退化为一轮直答，行为一致
- 保留两套并行引擎（run_turn + agent_loop）会导致 REPL 调用方分支
- 单测也需要维护两套，维护成本翻倍
- 第二阶段的 test_chat_round_loop.py 中 6 个测试用 stub Provider 验证编排
  逻辑，重写 engine 后这些测试需要适配——但测试覆盖的场景（自然停止 /
  工具执行 / 拒绝 / 中断 / 硬停）在 Agent Loop 中都有对应，改适配成本
  低于维护两套引擎

**影响**：
- test_chat_round_loop.py 需要重写（从"单轮 R1+R2"改为"多轮 Loop"）
- verify_round_loop.py 端到端脚本需要适配（输出多了进度行）

### D2. 为什么 AgentEvent 不复用 StreamEvent

**决策**：新增 AgentEvent 层，不复用 StreamEvent。

**理由**：
- StreamEvent 是 Provider 层的——它描述"LLM 流中的一帧"（一个 text delta
  / 一个 tool_use start / 一个 usage）
- AgentEvent 是 Agent 层的——它描述"Agent 编排中的一个语义事件"（一轮
  开始 / 一批工具执行 / Loop 停止）
- 两层事件的生命周期不同：一个 AgentEvent（如 IterationStart）对应多个
  StreamEvent（一整轮的 TextDelta + ToolUse* + Done）
- 混在一起会让 Renderer 既要处理流式增量又要处理编排语义，职责混乱
- 分层后 Renderer 有两个入口：push_text（StreamEvent 实时推 UI）+
  on_agent_event（AgentEvent 语义事件）

### D3. 为什么 SAFE 工具并发而不全部串行

**决策**：SAFE 只读工具并发，DANGEROUS 写类工具串行。

**理由**：
- 只读工具无副作用，并发安全（文件句柄有限但 8 个上限够用）
- 写类工具可能有顺序依赖（如先 write 再 edit 同一文件），串行保证正确性
- Claude Code / Cursor 等同类产品也采用此策略
- 并发把"N 个只读工具的总耗时"从 sum 降为 max，对 Agent Loop 的体感
  提升明显（一轮里调 5 个 read 从 5×RTT 降为 1×RTT）

### D4. 为什么软停止要注入系统提示而不是直接停

**决策**：迭代上限触发时注入"请总结"系统提示，发起最后一轮无工具请求。

**理由**：
- 直接停在工具执行后，用户看到的是"一堆工具结果但没有总结"——信息不完整
- 注入提示让模型用自然语言收尾，用户能知道"做了什么 / 没做什么 / 下一步建议"
- 最后一轮不带 tools_format——模型只能输出文本，不会再次进入工具循环
- 这是 Claude Code 的做法，用户体感最好

### D5. 为什么未知工具阈值是 2 而不是 1

**决策**：连续 2 次未知工具调用才停。

**理由**：
- 模型偶尔会幻觉一个不存在的工具名（如 "read_file" 而非 "read"）
- 第一次返回"未知工具"错误后，模型有概率在下一轮自我修正（改调 "read"）
- 阈值为 1 会把"可修正的幻觉"变成"硬停"，浪费已完成的迭代
- 阈值为 2 给一次容错，第二次仍错说明模型确实"撞墙"了

**实现**：`unknown_streak` 跨迭代累计——连续两轮都有未知工具才停；
中间任何一轮没有未知工具就重置 streak。

### D6. 为什么 Ctrl+C 取消整个 Loop 而不是当前迭代

**决策**：Ctrl+C 取消整个 Loop。

**理由**：
- Agent Loop 可能正在改文件改到一半（如 write 了 3 个文件中的 1 个），
  "继续下一轮"会让模型基于不完整状态继续，可能产生错误决策
- 用户按 Ctrl+C 的意图通常是"这个方向不对，我要重新来"
- "取消当前迭代"的语义模糊——模型已经看到了部分 tool_results，继续
  还是不继续？
- 直接取消整个 Loop 最安全、最符合直觉

**实现**：`_execute_tool_batch` 捕获 CancelledError 返回 `cancelled=True`，
`_agent_loop` 收到后直接 Stopped("user_cancel") 退出。`_consume_round`
中的 SIGINT handler 不变——Ctrl+C 在 LLM 流等待阶段也直接 cancel
sub_task 返回 None。

### D7. 为什么 Plan Mode 用物理隔离而不是运行时拒绝

**决策**：Plan Mode 下 tools_format 直接不含写类工具。

**理由**：
- 模型看不到 write/edit/run 的工具定义，根本不会生成对它们的 tool_use
- 比起"看得到但调了就拒绝"更干净——后者浪费一轮 LLM 请求
- 物理隔离在协议层就保证了安全，不依赖运行时检查
- 兜底：如果模型仍然输出了写类 tool_use（某些后端的幻觉），
  `_execute_tool_batch` 中检查 `session.mode == "plan"` 并返回
  "Plan Mode 禁止写类工具"错误

### D8. 为什么进度行用 ── 而不是 [N/M]

**决策**：用 `── 迭代 N/50 ──` 格式。

**理由**：
- `──` 是 Unicode box-drawing 字符，视觉上比 `[` `]` 更柔和
- 与正文文本有明显的视觉分层（进度行像"分隔线"）
- 在 Windows PowerShell 5.x + sys.stdout utf-8 reconfigure 下正常显示
- Claude Code 用类似格式

### D9. 为什么不暴露迭代上限到 mewcode.yaml

**决策**：本阶段 `MAX_ITERATIONS = 50` 硬编码为模块常量。

**理由**：
- spec Q1 明确决策为固定 50
- 配置化需要同步改 mewcode.yaml.example / config/models.py / config/loader.py
  增加校验——本阶段 YAGNI
- 后续章节如果需要（如"长任务模式 200 轮"）再改

### D10. 为什么 Renderer 用 on_agent_event 统一入口而不是多个方法

**决策**：Renderer 新增单个 `on_agent_event(ev)` 方法，内部 isinstance 分派。

**理由**：
- AgentEvent 有 7 种，每种一个方法会让 Renderer 接口爆炸（7 个新方法）
- 统一入口让 chat.engine 的 `_emit` 只调一个方法
- isinstance 分派在 Python 3.10+ 用 match-case 更优雅，但 isinstance
  if-elif 链兼容性更好
- Renderer 内部实现可以自由重构（如未来改为 dict 派发），不影响 chat.engine

### D11. 为什么 _consume_round 保留双路收集而不是拆成"先收集再渲染"

**决策**：在同一个 async for 循环中同时推 UI + 累积 blocks。

**理由**：
- 流式场景下"先收集完整响应再渲染"会丧失逐字打印体验
- 双路收集让 TextDelta 既实时推 UI 又累积到 text_buf
- 流结束后 text_buf 封装为 TextBlock 入 blocks，供 Agent Loop 判断
- 这是第二阶段 _consume_round 已有的模式，第三阶段保持不变

### D12. 为什么 ToolResultEvent 带 success 字段而 ToolCall 不带

**决策**：ToolResultEvent 有 success 字段，ToolCall 没有。

**理由**：
- ToolCall 是"即将执行"——此时不知道成功失败
- ToolResultEvent 是"已执行完"——有明确结果
- Renderer 用 success 决定图标（✓ / ✗），让用户一眼看到哪步出了问题
- ToolCall 只有 name + summary，简洁

## 4. 时序图

### 4.1 正常两轮 Loop（读两个文件 + 对比）

```
用户       REPL    run_turn    _agent_loop    _consume_round    _execute_tool_batch    Renderer
 │          │         │            │                │                   │                 │
 │ prompt   │         │            │                │                   │                 │
 ├─────────►│         │            │                │                   │                 │
 │          │ run_turn│            │                │                   │                 │
 │          ├────────►│            │                │                   │                 │
 │          │         │ _agent_loop│                │                   │                 │
 │          │         ├───────────►│                │                   │                 │
 │          │         │            │ IterationStart(1,50)               │                 │
 │          │         │            ├─────────────────────────────────────────────────────►│
 │          │         │            │                │                   │   ── 迭代 1/50 ──│
 │          │         │            │ _consume_round │                   │                 │
 │          │         │            ├───────────────►│                   │                 │
 │          │         │            │                │ stream_chat       │                 │
 │          │         │            │                │ (LLM 流式)        │                 │
 │          │         │            │                │ TextDelta × N ─────────────────────►│
 │          │         │            │                │ ToolUseStart/End × 2               │
 │          │         │            │                │ Done              │                 │
 │          │         │            │◄───────────────┤                   │                 │
 │          │         │            │ (blocks, usage)│                   │                 │
 │          │         │            │                                    │                 │
 │          │         │            │ append_assistant(blocks)           │                 │
 │          │         │            │                                    │                 │
 │          │         │            │ IterationEnd(1)                    │                 │
 │          │         │            ├─────────────────────────────────────────────────────►│
 │          │         │            │                                    │                 │
 │          │         │            │ tool_uses = [read(README), read(yaml)]              │
 │          │         │            │ _execute_tool_batch│               │                 │
 │          │         │            ├──────────────────────────────────►│                 │
 │          │         │            │                  ToolBatchStart(2,2,0)──────────────►│
 │          │         │            │                  ToolCall("read","path=README")────►│
 │          │         │            │                  ToolCall("read","path=yaml")─────►│
 │          │         │            │                  │ asyncio.gather   │                 │
 │          │         │            │                  │ (并发执行 2 个 read)              │
 │          │         │            │                  │ ToolResultEvent × 2──────────────►│
 │          │         │            │◄─────────────────┤ (results, False) │                 │
 │          │         │            │                                    │                 │
 │          │         │            │ append_tool_results(results)       │                 │
 │          │         │            │                                    │                 │
 │          │         │            │ IterationStart(2,50)──────────────────────────────►│
 │          │         │            │ ── 迭代 2/50 ──                    │                 │
 │          │         │            │ _consume_round │                   │                 │
 │          │         │            ├───────────────►│                   │                 │
 │          │         │            │                │ stream_chat       │                 │
 │          │         │            │                │ TextDelta × N ─────────────────────►│
 │          │         │            │                │ Done              │                 │
 │          │         │            │◄───────────────┤                   │                 │
 │          │         │            │ (blocks, usage)│                   │                 │
 │          │         │            │ append_assistant(blocks)           │                 │
 │          │         │            │ tool_uses = []  │                   │                 │
 │          │         │            │ Stopped("natural", 2)──────────────────────────────►│
 │          │         │            │ UsageTotal(…, 2)───────────────────────────────────►│
 │          │         │            │ ↑ X · ↓ Y · 2 轮                   │                 │
 │          │         │◄───────────┤                                    │                 │
 │          │◄────────┤ return True│                                    │                 │
 │◄─────────┤          │            │                                    │                 │
```

### 4.2 用户 Ctrl+C 取消

```
用户       _agent_loop    _consume_round    _execute_tool_batch    Renderer
 │              │                │                   │                 │
 │ (迭代 3 中)  │                │                   │                 │
 │              │ _consume_round │                   │                 │
 │              ├───────────────►│                   │                 │
 │ Ctrl+C       │                │                   │                 │
 │              │                │ SIGINT → cancel   │                 │
 │              │                │ sub_task          │                 │
 │              │◄───────────────┤ return (None,None)│                 │
 │              │ Stopped("user_cancel", 3)──────────────────────────►│
 │              │                │   （已取消）      │                 │
 │              │ UsageTotal(…)──┤                   │                 │
 │              │ return False   │                   │                 │
```

### 4.3 迭代上限软停止

```
_agent_loop                                    Renderer
    │
    │ iteration 50:
    │   is_final = True
    │   _inject_soft_stop_prompt(session)
    │     → 注入 "你已用完 50 轮..." user 消息
    │   IterationStart(50, 50) ──────────────► ── 迭代 50/50 ──
    │   _consume_round(allow_tools=False)
    │     → tools_format=None
    │     → LLM 只能输出文本
    │   blocks = [TextBlock("总结：...")]
    │   append_assistant(blocks)
    │   tool_uses = []
    │   Stopped("max_iterations", 50)──────────► （已达迭代上限）
    │   UsageTotal(…, 50) ─────────────────────► ↑ X · ↓ Y · 50 轮
    │   return True
```

### 4.4 Plan Mode 切换与执行

```
用户       REPL    commands    Session          run_turn
 │          │         │           │                │
 │ /plan    │         │           │                │
 ├─────────►│ dispatch│           │                │
 │          ├────────►│           │                │
 │          │         │ mode="plan"                │
 │          │         ├──────────►│                │
 │          │         │ print_info("📋 Plan Mode")│
 │          │◄────────┤           │                │
 │◄─────────┤          │           │                │
 │          │          │           │                │
 │ 读README │          │           │                │
 ├─────────►│ run_turn │           │                │
 │          ├─────────────────────────────────────►│
 │          │         │           │ _consume_round │
 │          │         │           │  mode="plan"   │
 │          │         │           │  → tools_format│
 │          │         │           │    只含 SAFE   │
 │          │         │           │  LLM 只看到    │
 │          │         │           │  read/glob/search│
 │          │         │           │                │
 │ /do      │         │           │                │
 ├─────────►│ dispatch│           │                │
 │          ├────────►│ mode="do" │                │
 │          │         ├──────────►│                │
 │          │         │ print_info("🔧 执行模式") │
```

## 5. 文件清单

| 操作 | 文件 | 职责 | 行数估计 |
|------|------|------|----------|
| 新建 | `mewcode/chat/events.py` | AgentEvent 7 种类型 + 联合 | ~70 |
| 重写 | `mewcode/chat/engine.py` | run_turn → _agent_loop → _consume_round + _execute_tool_batch | ~350 |
| 修改 | `mewcode/chat/session.py` | + mode 字段 + clear/switch 重置 | +5 |
| 修改 | `mewcode/chat/__init__.py` | 暴露 AgentEvent 类型 | +10 |
| 修改 | `mewcode/commands/builtin.py` | + _handle_plan / _handle_do | +30 |
| 修改 | `mewcode/render/renderer.py` | + on_agent_event | +50 |
| 重写 | `tests/test_chat_round_loop.py` | 适配 Agent Loop（6→8+ 用例） | ~350 |
| 新建 | `tests/test_agent_events.py` | AgentEvent 发射顺序 + 类型 | ~100 |
| 新建 | `tests/test_plan_mode.py` | Plan Mode 物理隔离 + 切换 | ~80 |
| 新建 | `scripts/verify_agent_loop.py` | 真实 API 端到端多轮 Loop | ~60 |
| 新建 | `scripts/verify_plan_mode.py` | 真实 API Plan Mode | ~50 |

共 11 个文件（4 新建 + 7 修改/重写）。

## 6. 与第二阶段的兼容矩阵

| 第二阶段行为 | 第三阶段是否保留 | 说明 |
|-------------|-----------------|------|
| run_turn 签名 | ✅ 不变 | REPL 调用方零改动 |
| 无 tool_use 退化为一轮直答 | ✅ | Agent Loop 第一轮无 tool_use → Stopped("natural", 1) |
| 流式逐字打印 | ✅ | _consume_round 双路收集不变 |
| SIGINT handler + sub_task | ✅ | _consume_round 不变 |
| DANGEROUS 工具确认 | ✅ | _execute_tool_batch 中保留 |
| ConfirmCancelled 回滚 | ✅ | _execute_tool_batch 返回 cancelled=True |
| ToolResultBlock 结构 | ✅ | 不变 |
| system_prompt 注入 | ✅ | _consume_round 透传 |
| Provider stream_chat 签名 | ✅ | 不变 |
| ToolRegistry 接口 | ✅ | 不变 |
| 96 个已有单测 | ✅ 全过 | test_chat_round_loop 重写适配 |
| 端到端验证脚本 | ✅ 适配 | verify_round_loop 输出多了进度行 |
| /exit /quit /help /clear /think /provider /providers | ✅ | 不变 |
| /clear 与 /provider 重置 mode | ✅ 新增 | mode 字段重置为 "do" |

### 需要适配的已有测试

**test_chat_round_loop.py**（6 个用例 → 重写为 8+ 用例）：

| 第二阶段用例 | 第三阶段对应 |
|-------------|-------------|
| test_R1直答_退化第一阶段 | test_自然停止_一轮直答 |
| test_R1工具_R2文本_完整闭环 | test_两轮Loop_工具+文本答复 |
| test_单R1多tool_use_串行 | test_多tool_use_分批执行 |
| test_用户拒绝_R1入历史_拒绝result进R2 | test_DANGEROUS工具拒绝 |
| test_中断回滚_ConfirmCancelled | test_Ctrl+C取消整个Loop |
| test_R2含tool_use硬停 | （删除——Agent Loop 不再硬停） |
| — | test_迭代上限软停止 |
| — | test_连续未知工具停止 |
| — | test_LLM流出错停止 |
| — | test_AgentEvent发射顺序 |
