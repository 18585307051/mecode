# MewCode 第三阶段 Spec

## 背景

MewCode 第二阶段已交付工具系统（docs/03/）：模型可以请求调用 read /
write / edit / run / glob / search 共 6 个核心工具，由 MewCode 本地执行后
把结果回灌进对话历史。但第二阶段实现的是"一次工具 + 一次答复"的**单轮
闭环**——Round 1 若含 tool_use，执行后发起 Round 2；Round 2 再含 tool_use
则**硬停**。

这意味着模型只能"一步一停"：用户每发一条 prompt，模型最多干一件事就被
迫给出最终答复；想继续必须用户再追问。即便是一个简单的"读取三个文件并
对比"任务，模型也得分三轮对话才能完成。

本阶段（第三阶段）为 MewCode 装上 **Agent Loop**——让模型按 ReAct 模式
（Reasoning + Acting 交替）自主循环：先想，再调工具，看结果，边做边调整，
直到任务完成。从此 MewCode 从被动应答的"工具调用雏形"进化为真正能自主
干活的 Agent。

同时引入 **Plan Mode** 两段式工作流：`/plan` 切换到只读工具让模型先出方案，
`/do` 切回全工具去执行，覆盖"先规划后执行"的典型开发场景。

## 目标

- 用 ReAct 循环替换第二阶段的单轮闭环：模型可以连续多轮调工具，直到
  自己判断"完成"为止
- 覆盖 5 种停止条件：模型不再请求工具（自然完成）、迭代上限兜底、用户
  Ctrl+C 取消、连续未知工具调用、LLM 流出错
- 引入分层事件流：在 StreamEvent 之上新增 AgentEvent 层，彻底解耦 Agent
  编排逻辑与终端渲染
- 多 tool_use 分批执行：SAFE 只读工具（read/glob/search）并发跑，
  DANGEROUS 有副作用工具（write/edit/run）串行跑
- 流式收集器双路：一边实时把文本推给界面，一边累积出完整响应供后续
  判断（停止条件、Plan Mode 输出等）
- Plan Mode 两段式：`/plan` 只放开只读工具让模型先出计划文本，`/do`
  切回全工具集去执行
- 迭代进度可见：每轮 LLM 请求前打印进度行 `── 迭代 N/M ──`
- Token 用量在 Loop 结束时一次性显示累计值，不在中间打扰用户
- 完全替换第二阶段的 `run_turn`——Agent Loop 是其超集，无 tool_use 时
  自然退化为一轮直答
- 第一阶段与第二阶段所有已通过的功能不退化

## 功能需求

### F1. Agent Loop 主循环

`chat.run_turn` 从"单轮闭环"升级为"多轮 ReAct 循环"。用户每发一条 prompt
触发一次完整的 Loop：

```
用户 prompt
    ↓
┌─── Loop 开始 ───────────────────────────────┐
│ ── 迭代 1/50 ──                              │
│ LLM 请求 → 流式 text + 可能的 tool_use × N   │
│     ↓                                       │
│ 分批执行 tool_use（并发只读 / 串行写类）       │
│     ↓                                       │
│ tool_results 追加到历史                       │
│     ↓                                       │
│ 检查停止条件 → 不满足则继续                   │
│ ── 迭代 2/50 ──                              │
│ LLM 请求 → …                                 │
└─────────────────────────────────────────────┘
    ↓ （满足停止条件）
打印累计用量 + 回到主输入提示符
```

每次 LLM 请求前打印一行进度：`── 迭代 N/50 ──`（N 从 1 开始，
M 为迭代上限）。进度行用 dim 灰色，与正文视觉分层。

### F2. 五种停止条件

Loop 在以下任一条件满足时终止：

1. **自然完成**：本轮 LLM 回复不含 tool_use 块——模型给出纯文本答复，
   视为"模型认为任务完成"。把该文本入历史并渲染，Loop 正常结束。

2. **迭代上限**：Loop 跑到第 50 轮仍未自然完成——触发"软停止"：构造
   一条系统消息注入对话，内容为"你已用完 50 轮迭代上限，请基于当前
   进展用一段文字总结完成情况与未完成事项"，再发起**最后一轮** LLM
   请求让模型自己收尾；该轮不再允许 tool_use（从 tools_format 中
   移除工具），模型只能输出纯文本。若该轮仍输出 tool_use 则忽略并
   直接用 text 部分结束。

3. **用户取消**：用户在 Loop 运行中按 Ctrl+C——立即终止当前正在执行
   的工具子进程（如果有），取消当前 LLM 流（如果在等待），打印
   "（已取消）"提示，**整个 Loop 结束**回到 `>` 提示符。已入历史的
   消息保留（中断点之前的进展不丢），但当前未完成迭代不入历史。

4. **连续未知工具**：模型连续 2 次调用注册表中不存在的工具名。第一次
   未知工具调用返回结构化错误"未知工具：xxx"作为 tool_result 反馈给
   模型（给一次修正机会）；第二次仍调用未知工具——立即停止 Loop，
   打印"连续调用未知工具，已停止"提示。单轮内多个未知工具也算"连续"
   （即一轮内出现 2 个未知工具即停）。

5. **LLM 流出错**：Provider 抛出 ProviderError（网络错误、鉴权失败、
   流解析错误等）——打印红字错误信息，Loop 结束。已入历史的消息保留。

### F3. 多 tool_use 分批执行

一次 LLM 回复可能含多个 tool_use 块。按工具 `danger_level` 分两批：

- **并发批（SAFE 只读）**：所有 `danger_level == SAFE` 的 tool_use 用
  `asyncio.gather` 并发执行；为防止文件句柄/进程数爆炸，同批最多
  并发 8 个（超出则截断为前 8 个，剩余的追加到串行批末尾）。
- **串行批（DANGEROUS 写类）**：所有 `danger_level == DANGEROUS` 的
  tool_use 按模型给出的顺序串行执行，一个完成后再执行下一个。

执行顺序：**先并发批，后串行批**。tool_results 按原始 tool_use 顺序
（模型给出的顺序）拼装成 user 消息回灌历史，保证模型看到的结果顺序
与它发出的调用顺序一致——不因并发完成先后而乱序。

### F4. 分层事件流（AgentEvent）

在第二阶段 StreamEvent 之上新增 AgentEvent 层，彻底解耦 Agent 编排
与终端渲染：

```
Provider 层：StreamEvent（TextDelta / ThinkingDelta / ToolUse* / Usage / Done）
                         ↓ chat.engine._consume_round 累积
Agent 层：  AgentEvent（IterationStart / IterationEnd / ToolBatchStart /
                        ToolCall / ToolResult / Stopped / UsageTotal）
                         ↓ Renderer 订阅
终端：      进度行 / 调用提示 / 简略反馈 / 停止原因 / 累计用量
```

AgentEvent 类型（全部 frozen dataclass）：

- `IterationStart(iteration: int, max_iterations: int)`——每轮开始
- `IterationEnd(iteration: int)`——每轮结束
- `ToolBatchStart(count: int, safe_count: int, dangerous_count: int)`
  ——一批 tool_use 即将执行
- `ToolCall(name: str, summary: str)`——单个工具调用前提示
- `ToolResult(tool_use_id: str, name: str, summary: str, success: bool)`
  ——单个工具结果简略反馈
- `Stopped(reason: str, iteration: int)`——Loop 停止，reason 取值：
  `natural` / `max_iterations` / `user_cancel` / `unknown_tools` / `error`
- `UsageTotal(input_tokens: int, output_tokens: int, thinking_tokens: int | None, iterations: int)`
  ——Loop 结束的累计用量

Renderer 订阅 AgentEvent 并输出对应终端文本。chat.engine 内部不再
直接调 Renderer 的语义方法（如 print_tool_call），而是 emit AgentEvent。

### F5. 流式收集器双路

`_consume_round` 在收到 StreamEvent 时同时做两件事：

1. **实时推 UI**：TextDelta → Renderer.push_text；ThinkingDelta →
   Renderer.push_thinking（与第二阶段一致）
2. **累积完整响应**：本地维护 `text_buf / thinking_buf / tool_uses`，
   流结束后返回 `(blocks, usage)` 供 Agent Loop 判断停止条件与组装
   tool_results

双路在同一个 async for 循环中完成，不重复消费流。

### F6. Plan Mode 两段式

新增两个斜杠命令：

- `/plan`——切换到 Plan Mode：
  - 此后所有 prompt 的 LLM 请求只携带只读工具（read/glob/search）的
    tools_format，模型看不到 write/edit/run
  - 模型只能调研与出方案，不能修改文件
  - 切换时打印 `📋 Plan Mode：只读工具（read / glob / search）`
- `/do`——切回执行模式：
  - 恢复全部 6 个工具的 tools_format
  - 切换时打印 `🔧 执行模式：全部工具`

模式状态存在 Session 中（`mode: Literal["do", "plan"] = "do"`）。
`/clear` 与 `/provider` 切换时重置为 `"do"`。

Plan Mode 下如果模型仍尝试调用写类工具（理论上不会，因为 tools_format
里没有）——返回结构化错误"Plan Mode 禁止写类工具"作为 tool_result，
不执行。

### F7. 迭代进度显示

每轮 LLM 请求前 Renderer 输出一行进度：

```
── 迭代 3/50 ──
```

灰色 dim 样式，与正文视觉分层。第 1 轮也打印（让用户知道 Loop 开始了）。

### F8. Token 用量显示

Loop 运行期间**不显示**每轮用量（避免刷屏）。Loop 结束时打印一行累计：

```
↑ 5000 tokens · ↓ 1200 tokens · 8 轮
```

若任一轮的 usage 含 thinking_tokens，累计行追加 `思考 N tokens`。
Loop 因出错或用户取消提前结束时，仍打印已完成的累计用量（若有）。

### F9. 与第二阶段 run_turn 的兼容

`chat.run_turn` 的**函数签名**保持不变（REPL 侧调用方无需改动）：

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

内部从"R1+工具+R2 单轮"重写为"Agent Loop 多轮"。无 tool_use 时退化
为一轮直答——行为与第二阶段一致（流式 + 用量行）。

### F10. Ctrl+C 语义

Agent Loop 中的 Ctrl+C：

- 若正在等待 LLM 流：取消当前流，打印 `（已取消）`，Loop 结束
- 若正在执行工具（并发批或串行批）：取消所有未完成的工具任务，
  终止正在运行的子进程（run 工具），打印 `（已取消）`，Loop 结束
- 若在两轮之间的间隙：直接结束 Loop
- 已入历史的消息保留；当前未完成迭代不入历史

REPL 主循环的 Ctrl+C 双击退出语义不变（只在 `>` 提示符等待时生效；
Loop 运行中的 Ctrl+C 被 chat.run_turn 内部捕获，不冒泡到 REPL）。

### F11. 软停止的最后一轮

触达迭代上限时，构造系统提示注入对话：

```
你已用完 50 轮迭代上限。请基于当前进展，用一段文字总结：
1. 已完成的部分
2. 未完成的部分
3. 建议的后续步骤
不要再调用工具。
```

然后发起最后一轮 LLM 请求——**不携带 tools_format**（模型只能输出
纯文本）。该轮的 text 入历史并渲染，Loop 结束。reason 为
`max_iterations`。

若该轮模型仍尝试调工具（某些后端可能即使不带 tools 也输出 tool_use），
忽略 tool_use 块，仅取 text 部分结束。

### F12. 不做的事

本阶段明确不做：

- 权限系统（工具级 ACL、用户角色）——后续章节
- 上下文压缩（超 token 时摘要早期消息）——后续章节
- 交互式确认（Agent Loop 中的动态审批）——后续章节
- 子代理（任务分解给多个 Agent）——后续章节
- 持久化（进程退出后 Loop 状态不丢）——后续章节
- MCP 协议适配——后续章节
- 新增工具——本阶段工具集仍为第二阶段的 6 个
- 流式渲染升级——仍沿用第二阶段的朴素 sys.stdout.write 策略

## 非功能需求

### N1. 模块边界

- `chat/engine.py` 重写为 Agent Loop 编排，**不直接调** Renderer 的
  语义方法，而是 emit AgentEvent 由 Renderer 订阅
- `render/renderer.py` 新增 AgentEvent 订阅方法
- `chat/events.py`（新文件）定义 AgentEvent 类型
- `tools/` 模块不感知 Agent Loop——execute 接口不变
- `providers/` 模块不感知 Agent Loop——stream_chat 接口不变
- `repl/` 不感知 Agent Loop——run_turn 签名不变
- `commands/builtin.py` 新增 `/plan` 与 `/do` 命令

### N2. 不引入新依赖

`pyproject.toml` 的 dependencies 仍仅含 prompt_toolkit / rich / PyYAML /
httpx 四项。Agent Loop 用 asyncio.gather 做并发，不引入任何并发框架。

### N3. Windows 终端兼容

所有新增 UI 元素（进度行、模式切换提示、停止原因、累计用量）沿用
第二阶段的朴素 sys.stdout.write 策略，不使用 rich Live，不发 ANSI
清行/光标移动转义。Unicode 字符（── / 📋 / 🔧 / ↑↓ ·）依赖第一阶段
已修复的 `sys.stdout.reconfigure(encoding="utf-8")` 正常显示。

### N4. 单测覆盖

新增单测覆盖：
- Agent Loop 的 5 种停止条件各至少 1 个用例
- 分批执行（并发 SAFE + 串行 DANGEROUS）至少 1 个用例
- Plan Mode 切换与只读工具隔离至少 1 个用例
- AgentEvent 发射顺序至少 1 个用例
- 与第二阶段兼容（无 tool_use 退化为一轮直答）至少 1 个用例

预计新增单测约 15-20 个。

### N5. 第一阶段与第二阶段不退化

- 96 个已有单测全过
- 第一阶段端到端验证脚本（verify_t9/t10/t11/t15/t18_config_errors）仍通过
- 第二阶段端到端验证脚本（verify_t18/t19/verify_round_loop/verify_system_prompt）仍通过
- REPL 的 /exit /quit /help /clear /think /provider /providers 命令不变
- 纯对话场景（不调工具）行为与第二阶段一致

### N6. 迭代上限可调

迭代上限（50）作为 `chat/engine.py` 的模块级常量 `MAX_ITERATIONS = 50`。
本阶段不暴露为配置项（spec Q1 决策为固定 50）；后续章节需要时改为从
`mewcode.yaml` 读取。

### N7. 并发上限可调

同批最多并发工具数（8）作为模块级常量 `MAX_CONCURRENT_SAFE_TOOLS = 8`。
同 N6，本阶段固定，后续可配置化。

### N8. Ctrl+C 不渗漏 traceback

Loop 中任何阶段的 Ctrl+C 都不得把 traceback 渗漏到终端。沿用第二阶段的
SIGINT handler + sub_task cancel + finally aclose 防线；并发批的
asyncio.gather 在被 cancel 时也要正确清理每个子任务。

### N9. api_key 不回显

所有新增 UI 元素（进度行、停止原因、累计用量、模式切换提示）绝不输出
api_key。继承第二阶段 N9 的防线。

### N10. 中文优先

所有面向用户的文案（进度行、模式提示、停止原因、错误信息）使用中文。
代码注释与 docstring 使用中文。AgentEvent 类型的字段名用英文（代码层），
但其渲染到终端的内容用中文。

### N11. 软停止的系统提示为中文

F11 中"你已用完 50 轮迭代上限…"的系统提示使用中文，与 system_prompt.py
的整体风格一致。

### N12. 历史合法性

Loop 结束后，`session.messages` 的末尾序列必须符合协议层要求：
- 自然完成：末尾是 `assistant(text)` 消息
- 软停止：末尾是 `assistant(text)` 消息（最后一轮无工具调用）
- 用户取消：末尾是最后一个已完成的 `assistant + user(tool_results)`
  对（当前未完成迭代的 assistant 不入历史）
- 未知工具停止：末尾是 `assistant(tool_use × N) + user(tool_results)`
  其中 tool_results 含未知工具错误反馈
- 流出错：末尾是最后一个已完成的 `assistant + user(tool_results)` 对

任何停止条件下，都不应出现"孤儿 tool_use"（assistant 含 ToolUseBlock 但
后续 user 消息中没有对应 tool_use_id 的 ToolResultBlock）。

## 验收标准

### AC1. Agent Loop 基础循环

发起 prompt "读 README.md 和 mewcode.yaml.example，对比两个文件的第一行"
——模型应在一轮内并发调用 2 个 read 工具，然后在第二轮给出对比文本答复。
终端可见 `── 迭代 1/50 ──` 与 `── 迭代 2/50 ──` 两个进度行。

### AC2. 自然停止

发起 prompt "你好"——模型一轮直答，不含 tool_use。终端只见 `── 迭代 1/50 ──`
一个进度行与文本答复，Loop 自然结束。行为与第二阶段一致。

### AC3. 迭代上限软停止

构造一个会让模型无限循环的 prompt（如 "不停地读 README.md 并重复其内容"），
把 MAX_ITERATIONS 临时改为 3 加速测试——第 3 轮后触发软停止，注入系统提示，
第 4 轮（最后一轮）模型给出总结文本，Loop 结束。终端打印停止原因
"已达迭代上限"。

### AC4. 用户 Ctrl+C 取消

发起一个会触发多轮工具调用的 prompt（如 "读 README.md 然后读 mewcode.yaml.example
然后读 pyproject.toml"），在第二轮工具执行中按 Ctrl+C——当前工具被终止，
打印 `（已取消）`，Loop 结束，回到 `>` 提示符，无 traceback 渗漏。

### AC5. 连续未知工具停止

通过 stub Provider 单测验证：构造模型连续两轮调用不存在的工具名
"foobar"——第一次返回未知工具错误，第二次仍调未知工具——Loop 立即停止，
打印 "连续调用未知工具，已停止"。

### AC6. LLM 流出错停止

通过 stub Provider 单测验证：构造 Provider 在第 2 轮抛 ProviderError——
第 1 轮正常执行，第 2 轮打印红字错误信息，Loop 结束。已入历史的第 1 轮
消息保留。

### AC7. 多 tool_use 分批执行

通过 stub Provider 单测验证：一轮中模型同时调用 3 个 SAFE 工具（read × 2 +
search × 1）+ 2 个 DANGEROUS 工具（write × 2）——3 个 SAFE 用 asyncio.gather
并发执行（总耗时约等于最慢的那个），2 个 DANGEROUS 串行执行。tool_results
按模型给出的原始顺序回灌历史。

### AC8. 并发上限

通过 stub Provider 单测验证：一轮中模型调用 10 个 SAFE 工具——只并发前 8 个，
剩余 2 个追加到串行批末尾执行。所有 10 个 tool_result 都回灌历史。

### AC9. AgentEvent 发射顺序

通过 stub Provider 单测验证一次完整 Loop 的 AgentEvent 序列：
```
IterationStart(1, 50)
ToolBatchStart(2, 2, 0)
ToolCall("read", ...)
ToolCall("read", ...)
ToolResult("t1", "read", "读取 47 行", True)
ToolResult("t2", "read", "读取 12 行", True)
IterationEnd(1)
IterationStart(2, 50)
IterationEnd(2)
Stopped("natural", 2)
UsageTotal(...)
```

### AC10. Plan Mode 切换

在 REPL 中输入 `/plan`——打印 `📋 Plan Mode：只读工具（read / glob / search）`。
此后发起 prompt "读 README.md 然后写一个 summary.txt 总结"——模型只能
调用 read/glob/search，尝试调 write 时返回 "Plan Mode 禁止写类工具"
错误。输入 `/do`——打印 `🔧 执行模式：全部工具`，此后模型可调用全部
6 个工具。

### AC11. Plan Mode 物理隔离

通过单测验证：Plan Mode 下 `tools_format` 只含 read/glob/search 三个工具
的元信息，不含 write/edit/run。模型协议层面看不到写类工具。

### AC12. 累计用量显示

完整 Loop 结束后，终端打印一行 `↑ X tokens · ↓ Y tokens · N 轮`。
X 与 Y 是所有迭代的 input/output 累加；N 是实际迭代次数。若任一轮
含 thinking_tokens，追加 `思考 Z tokens`。

### AC13. 不退化——纯对话

发起 prompt "你好"——行为与第二阶段一致：流式逐字打印、用量行、
无 tool_use、无进度行之外的额外输出。

### AC14. 不退化——单工具闭环

发起 prompt "读 README.md 的第一行"——模型一轮调 read、二轮给文本答复。
与第二阶段的 verify_round_loop.py 行为一致，但终端多了进度行。

### AC15. 不退化——第一阶段命令

/exit /quit /help /clear /think /provider /providers 命令行为不变。
/clear 与 /provider 重置 mode 为 "do"。

### AC16. 不退化——Windows 终端

所有新增 UI 元素在 Windows PowerShell 5.x 下无 `?[2K` 类乱码、
无 traceback 渗漏。── / 📋 / 🔧 / ↑↓ · 等 Unicode 字符正常显示。

### AC17. 不退化——已有单测

`pytest tests/ -q` 全过（96 个已有 + 新增约 15-20 个）。

### AC18. 不退化——已有端到端

verify_t9/t10/t11/t15/t18_config_errors/verify_t18/t19/round_loop/
system_prompt 全部仍通过。

## 依赖与约束

- 继承第二阶段的全部模块结构与接口契约
- 工具集不变（read/write/edit/run/glob/search）
- Provider 接口不变（stream_chat 签名含 system / tools_format）
- Sandbox / Confirmer / ToolRegistry 接口不变
- 不引入新依赖
- Python 3.10+
