# MewCode 第三阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证，聚焦系统行为。
> 验证环境：Windows + Windows PowerShell 5.x，项目根
> `e:\AI\vscode_project\mecode`，启动命令 `python -m mewcode`。
> 全部通过后第三阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装**
  验证：`pip install -e .` 输出 "Successfully installed mewcode-0.1.0"，
  无 error。

- [ ] **C2 包可导入**
  验证：`python -c "import mewcode; print(mewcode.__version__)"` 输出
  `0.1.0`。

- [ ] **C3 单元测试全部通过**
  验证：`pytest tests/ -q`，第一阶段 31 + 第二阶段 59（其中
  test_chat_round_loop 重写后约 10 用例）+ 第三阶段约 19 个新增
  = **105+ 个测试全部通过**，0 个失败/错误。

- [ ] **C4 全部源文件语法合法**
  验证：`python -m compileall mewcode/ -q`，输出无 error。

- [ ] **C5 命令行入口可调用**
  验证：`python -m mewcode` 不报"找不到模块"或"command not found"。

## Agent Loop 主循环（spec F1/F2/F9）

- [ ] **AC1 Agent Loop 基础循环**
  验证：发起 prompt "读 README.md 和 mewcode.yaml.example，对比两个
  文件的第一行"——模型应在一轮内并发调用 2 个 read 工具，然后在第二轮
  给出对比文本答复。终端可见 `── 迭代 1/50 ──` 与 `── 迭代 2/50 ──`
  两个进度行。

- [ ] **AC2 自然停止**
  验证：发起 prompt "你好"——模型一轮直答，不含 tool_use。终端只见
  `── 迭代 1/50 ──` 一个进度行与文本答复，Loop 自然结束。行为与
  第二阶段一致。

- [ ] **AC13 不退化——纯对话**
  验证：发起 prompt "你好"——行为与第二阶段一致：流式逐字打印、
  用量行、无 tool_use、无进度行之外的额外输出。

## 五种停止条件（spec F2）

- [ ] **AC3 迭代上限软停止**
  验证：通过 stub Provider 单测，把 MAX_ITERATIONS 临时改为 3 加速
  测试——第 3 轮后触发软停止，注入系统提示"你已用完 3 轮迭代上限…"，
  第 3 轮（最后一轮）模型给出总结文本，Loop 结束。终端打印停止原因
  "（已达迭代上限）"。

- [ ] **AC4 用户 Ctrl+C 取消**
  验证：发起一个会触发多轮工具调用的 prompt（如 "读 README.md 然后
  读 mewcode.yaml.example 然后读 pyproject.toml"），在第二轮工具
  执行中按 Ctrl+C——当前工具被终止，打印 `（已取消）`，Loop 结束，
  回到 `>` 提示符，无 traceback 渗漏。

- [ ] **AC5 连续未知工具停止**
  验证：通过 stub Provider 单测验证：构造模型连续两轮调用不存在的
  工具名 "foobar"——第一次返回未知工具错误，第二次仍调未知工具——
  Loop 立即停止，打印 "（连续调用未知工具，已停止）"。

- [ ] **AC6 LLM 流出错停止**
  验证：通过 stub Provider 单测验证：构造 Provider 在第 2 轮抛
  ProviderError——第 1 轮正常执行，第 2 轮打印红字错误信息，Loop
  结束。已入历史的第 1 轮消息保留。

## 分批执行（spec F3）

- [ ] **AC7 多 tool_use 分批执行**
  验证：通过 stub Provider 单测验证：一轮中模型同时调用 3 个 SAFE
  工具（read × 2 + search × 1）+ 2 个 DANGEROUS 工具（edit × 2）——
  3 个 SAFE 用 asyncio.gather 并发执行（总耗时约等于最慢的那个），
  2 个 DANGEROUS 串行执行。tool_results 按模型给出的原始顺序回灌
  历史。

- [ ] **AC8 并发上限**
  验证：通过 stub Provider 单测验证：一轮中模型调用 10 个 SAFE 工具——
  只并发前 8 个，剩余 2 个追加到串行批末尾执行。所有 10 个 tool_result
  都回灌历史。

## AgentEvent 事件流（spec F4/F5）

- [ ] **AC9 AgentEvent 发射顺序**
  验证：通过 stub Provider 单测验证一次完整 Loop 的 AgentEvent 序列：
  ```
  IterationStart(1, 50)
  ToolBatchStart(2, 2, 0)
  ToolCall("read", ...)
  ToolCall("read", ...)
  ToolResultEvent("t1", "read", "读取 47 行", True)
  ToolResultEvent("t2", "read", "读取 12 行", True)
  IterationEnd(1)
  IterationStart(2, 50)
  IterationEnd(2)
  Stopped("natural", 2)
  UsageTotal(...)
  ```

## Plan Mode（spec F6）

- [ ] **AC10 Plan Mode 切换**
  验证：在 REPL 中输入 `/plan`——打印 `📋 Plan Mode：只读工具
  （read / glob / search）`。此后发起 prompt "读 README.md 然后
  写一个 summary.txt 总结"——模型只能调用 read/glob/search，尝试调
  write 时返回 "Plan Mode 禁止写类工具" 错误。输入 `/do`——打印
  `🔧 执行模式：全部工具`，此后模型可调用全部 6 个工具。

- [ ] **AC11 Plan Mode 物理隔离**
  验证：通过单测验证：Plan Mode 下 `tools_format` 只含 read/glob/search
  三个工具的元信息，不含 write/edit/run。模型协议层面看不到写类工具。

## 用户体验（spec F7/F8）

- [ ] **进度行显示**
  验证：任意多轮 Loop 场景下，每轮 LLM 请求前终端打印一行
  `── 迭代 N/50 ──`（灰色 dim 样式），与正文视觉分层。

- [ ] **AC12 累计用量显示**
  验证：完整 Loop 结束后，终端打印一行 `↑ X tokens · ↓ Y tokens ·
  N 轮`。X 与 Y 是所有迭代的 input/output 累加；N 是实际迭代次数。
  若任一轮含 thinking_tokens，追加 `思考 Z tokens`。

## 中断与错误（spec F10/N8）

- [ ] **Ctrl+C 不渗漏 traceback**
  验证：Agent Loop 中任何阶段的 Ctrl+C（LLM 流等待 / 工具执行 /
  迭代间隙）都不得把 traceback 渗漏到终端。沿用第二阶段的 SIGINT
  handler + sub_task cancel + finally aclose 防线。

- [ ] **Ctrl+C 在并发批中的清理**
  验证：通过 stub Provider 单测验证：并发批（asyncio.gather）被
  Ctrl+C cancel 时，每个子任务正确清理，不残留 asyncio 警告。

## 历史合法性（spec N12）

- [ ] **自然完成历史结构**
  验证：通过 stub Provider 单测确认：自然停止后，session.messages
  末尾是 `assistant(text)` 消息。

- [ ] **软停止历史结构**
  验证：通过 stub Provider 单测确认：软停止后，末尾是
  `assistant(text)` 消息（最后一轮无工具调用）。

- [ ] **用户取消历史结构**
  验证：通过 stub Provider 单测确认：用户取消后，末尾是最后一个
  已完成的 `assistant + user(tool_results)` 对（当前未完成迭代的
  assistant 不入历史）。

- [ ] **未知工具停止历史结构**
  验证：通过 stub Provider 单测确认：末尾是
  `assistant(tool_use × N) + user(tool_results)`，其中 tool_results
  含未知工具错误反馈。

- [ ] **无孤儿 tool_use**
  验证：通过 stub Provider 单测确认：任何停止条件下，都不应出现
  "孤儿 tool_use"（assistant 含 ToolUseBlock 但后续 user 消息中没有
  对应 tool_use_id 的 ToolResultBlock）。

## 不退化（spec N5）

- [ ] **AC14 不退化——单工具闭环**
  验证：发起 prompt "读 README.md 的第一行"——模型一轮调 read、
  二轮给文本答复。与第二阶段的 verify_round_loop.py 行为一致，
  但终端多了进度行。

- [ ] **AC15 不退化——第一阶段命令**
  验证：/exit /quit /help /clear /think /provider /providers 命令
  行为不变。/clear 与 /provider 重置 mode 为 "do"。

- [ ] **AC17 不退化——已有单测**
  验证：`pytest tests/ -q` 全过（96 个已有 + 新增约 19 个）。

- [ ] **AC18 不退化——已有端到端**
  验证：verify_t9/t10/t11/t15/t18_config_errors/verify_t18/t19/
  round_loop/system_prompt 全部仍通过。

## 模块集成（plan 层验证）

- [ ] **I1 Agent 与渲染解耦**
  验证：阅读代码确认 `chat/engine.py` 不直接调 Renderer 的语义方法
  （如 print_tool_call），而是 emit AgentEvent 由 Renderer 订阅。
  Renderer 的 `on_agent_event` 是唯一入口。

- [ ] **I2 模块边界清晰**
  验证：阅读代码确认：
  - `chat/events.py` 不依赖 Renderer / Provider / tools
  - `chat/engine.py` 通过参数注入 registry / sandbox / confirmer
  - `tools/` 模块不感知 Agent Loop——execute 接口不变
  - `providers/` 模块不感知 Agent Loop——stream_chat 接口不变
  - `repl/` 不感知 Agent Loop——run_turn 签名不变

- [ ] **I3 中文注释与文案**
  验证：随机抽查 5 个新/改源文件（chat/events.py、chat/engine.py、
  chat/session.py、commands/builtin.py、render/renderer.py），
  函数 docstring 与用户可见提示均为中文。

- [ ] **I4 不引入新依赖**
  验证：`pyproject.toml` 的 dependencies 仍仅含
  prompt_toolkit / rich / PyYAML / httpx 四项；无 asyncio 之外的
  并发框架。

- [ ] **I5 迭代上限与并发上限可调**
  验证：阅读代码确认 `MAX_ITERATIONS = 50` 与
  `MAX_CONCURRENT_SAFE_TOOLS = 8` 是 `chat/engine.py` 的模块级常量，
  后续可改为从配置读取。

## 兼容性

- [ ] **AC16 不退化——Windows 终端**
  验证：所有新增 UI 元素在 Windows PowerShell 5.x 下无 `?[2K` 类
  乱码、无 traceback 渗漏。`──` / `📋` / `🔧` / `✓` / `✗` / `↑↓` /
  `·` 等 Unicode 字符正常显示。

## 依赖一致性

- [ ] **D1 依赖列表精简（继承第一/二阶段）**
  验证：pyproject.toml dependencies 4 项；dev 依赖 2 项
  （pytest、pytest-asyncio）。

- [ ] **D2 Python 版本要求**
  验证：`requires-python = ">=3.10"`；当前运行 Python ≥ 3.10。
