# MewCode 第二阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证，聚焦系统行为。
> 验证环境：Windows + Windows PowerShell 5.x（继承第一阶段），项目根
> `e:\AI\vscode_project\mecode`，启动命令 `python -m mewcode`。
> 全部通过后第二阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装**
  验证：`pip install -e .` 输出 "Successfully installed mewcode-0.1.0"，
  无 error。

- [ ] **C2 包可导入**
  验证：`python -c "import mewcode; print(mewcode.__version__)"` 输出
  `0.1.0`。

- [ ] **C3 单元测试全部通过**
  验证：`pytest tests/ -q`，第一阶段 31 + 第二阶段约 55 个测试用例
  全部通过，0 个失败/错误。

- [ ] **C4 全部源文件语法合法**
  验证：`python -m compileall mewcode/ -q`，输出无 error。

- [ ] **C5 命令行入口可调用**
  验证：`python -m mewcode` 不报"找不到模块"或"command not found"。

## Tool 抽象与注册（spec F1/F2/F8）

- [ ] **AC1 Tool 抽象具备最小契约**
  验证：阅读 `mewcode/tools/base.py` 确认 Tool 抽象定义了 `name /
  description / parameters_schema / execute` 四项；6 个具体工具
  （read/write/edit/run/glob/search）均继承该抽象并实现 execute。

- [ ] **AC2 工具注册中心可按名查找**
  验证：
  ```python
  from mewcode.tools import ToolRegistry, register_builtins
  r = ToolRegistry(); register_builtins(r)
  print(sorted(t.name for t in r))
  print(r.get("read") is not None, r.get("nonexistent") is None)
  ```
  输出 `['edit', 'glob', 'read', 'run', 'search', 'write']` 与 `True True`。

- [ ] **AC3 工具注册中心按协议格式输出元信息**
  验证：
  ```python
  print(r.to_anthropic_format()[0])  # 含 name / description / input_schema
  print(r.to_openai_format()[0])     # 含 type:"function" 与 function:{name,description,parameters}
  ```

- [ ] **AC4 扩展性可见**（结构性 / 代码审查）
  验证：阅读 `mewcode/tools/registry.py` 中 `register_builtins`；
  确认新增工具只需 ① tools 目录加新文件 ② `register_builtins` 加一行；
  REPL、chat、Provider、render、commands 模块均无需修改。

## 6 个核心工具（spec F4-F9）

- [ ] **AC5 read 基础读取**
  验证：脚本调用 ReadTool({"path":"README.md"}, sandbox)，返回的 text
  含 README.md 的若干行内容；success=True。

- [ ] **AC6 read offset/limit 按行读取**
  验证：tmp 目录写 100 行；ReadTool({"path":..., "offset":10, "limit":5})
  返回的 text 仅含第 10~14 行（1-based 起始）。

- [ ] **AC7 read 大文件截断**
  验证：生成 500KB 文本文件；ReadTool 返回 text 字节数 ≤ 256KB+200
  且包含"已截断"提示。

- [ ] **AC8 write 新建文件**
  验证：在 REPL 中让模型写一个新文件 demo.txt；终端弹出确认提示展示
  路径与首若干行内容；输入 y；文件创建成功；后续 read demo.txt 能取
  到内容。

- [ ] **AC9 write 用户拒绝**
  验证：让模型写文件 + 确认提示输入 n；文件未创建；模型在 Round 2 中
  收到"用户拒绝执行此工具"反馈，最终答复体现这一点。

- [ ] **AC10 edit 唯一匹配替换**
  验证：在文件中放入唯一字符串 ALPHA；让模型把 ALPHA 改成 BETA；
  确认 y 后 read 文件能看到 BETA、找不到 ALPHA。

- [ ] **AC11 edit 匹配多次报错**
  验证：在文件中放入 3 个 ALPHA；让模型把 ALPHA 改成 BETA；工具返回
  结构化错误"匹配 3 次需更多上下文"，文件保持不变；模型在 Round 2
  中据此调整或说明。

- [ ] **AC12 edit 未匹配报错**
  验证：让模型把不存在的 GAMMA 改成 DELTA；工具返回结构化错误"未找到
  匹配"，文件保持不变。

- [ ] **AC13 run 命令成功**
  验证：让模型跑 `python --version`（或 `echo hello`）；确认 y 后
  工具返回含 stdout 与 exit_code=0；终端显示"退出码 0"。

- [ ] **AC14 run 命令失败**
  验证：让模型跑 `python -c "import sys; sys.exit(7)"`；确认 y 后
  返回 exit_code=7；模型在 Round 2 中体现这一信息。

- [ ] **AC15 run 超时**
  验证：让模型跑 `python -c "import time; time.sleep(90)"`；60 秒内
  子进程被终止，工具返回结构化错误"超时"。
  （可在单测中通过 monkey-patch 改超时为 2s 加速验证；端到端验收时
  保持 60s）

- [ ] **AC16 glob 基础匹配**
  验证：tmp 准备若干 .py 与 .md 文件；让模型 glob `**/*.py`；返回的
  匹配列表只包含 .py 文件、按字母序排列。

- [ ] **AC17 glob 自动排除噪声目录**
  验证：tmp 下准备 `__pycache__/x.py`、`.git/y.py`、
  `node_modules/z.py`；glob `**/*.py` 时这三类目录中的文件不出现
  在结果中。

- [ ] **AC18 search 基础匹配**
  验证：在多个 .py 文件中放入唯一标记 NEEDLE_XYZ；search 该字符串
  能返回所有命中的（文件、行号、行内容）三元组。

- [ ] **AC19 search 单行截断**
  验证：在文件中放入一个超过 500 字符的长行（含 NEEDLE_XYZ）；
  search 返回该匹配行内容长度不超过 500 字符。

## 路径边界（spec F10）

- [ ] **AC20 路径越界拒绝执行**
  验证：让模型 read `/etc/passwd`、`C:\Windows\System32\drivers\etc\hosts`
  或 `../../../some_file`；工具返回结构化错误"路径越界"，未真正读取，
  模型在 Round 2 中据此说明无法访问。

- [ ] **AC21 CWD 内绝对路径可访问**
  验证：当前 CWD 是 `e:\AI\vscode_project\mecode`；read 绝对路径
  `e:\AI\vscode_project\mecode\README.md` 工作正常。

## 单轮闭环（spec F11-F15）

- [ ] **AC22 Round 1 + 工具 + Round 2 完整闭环**
  验证：发起 prompt "读一下 README.md 然后告诉我项目叫什么"；
  终端显示：
  - Round 1 流式（可能很短的引言或直接进入工具调用）
  - `▸ read(path=README.md)` 提示
  - 工具简略反馈（"读取 N 行"）
  - Round 2 流式最终答复（含 README 中的项目名）

- [ ] **AC23 Round 1 无工具调用直接答复**
  验证：发起 prompt "你好"；模型 Round 1 直接给文本回复；不进入
  Round 2；行为与第一阶段一致（流式 + 用量行）。

- [ ] **AC24 单 turn 多个 tool_use 串行执行**
  验证：发起 prompt "读 README.md 和 mewcode.yaml.example 这两个
  文件，告诉我各自的第一行"；Round 1 产出 2 个 read 调用；终端按
  顺序执行两个 read 并展示两条简略反馈；Round 2 答复中包含两个
  文件的第一行。

- [ ] **AC25 Round 2 中再含 tool_use 硬停**
  验证：通过 stub Provider 单测验证（端到端难以稳定复现）：构造
  Round 2 流含 ToolUseStart/End；run_turn 后历史末尾的 assistant(R2)
  消息中应不含 ToolUseBlock（已被剥离）；Renderer 收到含"模型在
  最终答复中还想调用工具"的 print_info 调用；下一轮 prompt 发送
  时不报"孤儿 tool_use"协议错误。

## 协议双支持（spec F11/F18）

- [ ] **AC26a Anthropic 协议工具调用（thinking off）**
  验证：默认供应商（deepseek-anthropic）+ /think off 状态下完成
  AC22 完整闭环。

- [ ] **AC26b Anthropic 协议工具调用（thinking on）**
  验证：default 供应商 + /think on，发起 prompt "读 README.md 然后
  分析项目结构"；Round 1 出现思考流 + 工具调用；Round 2 输出最终
  答复；闭环完整。

- [ ] **AC27 OpenAI 协议工具调用**
  验证：`/provider deepseek-openai` 切换后完成 AC22 完整闭环；
  终端可见 Round 1 → tool 执行 → Round 2 流程。

## 用户体验（spec F19/F20）

- [ ] **AC28 调用前简略提示**
  验证：任意工具调用前终端打印一行 "▸ <tool_name>(<参数概要>)"；
  参数过长时截断到约 80 字符。

- [ ] **AC29 调用后简略反馈**
  验证：read 显示"读取 N 行"；glob/search 显示"匹配 N 项"；
  run 显示"退出码 N"；write/edit 显示"成功"或错误类别；
  与 AC28 同样为单行灰字。

- [ ] **AC30 完整工具结果不刷屏**
  验证：让模型 read 一个 200 行文件；终端只显示"读取 200 行"简略
  反馈，不原样打印 200 行内容；Round 2 答复中模型的总结里能体现
  文件内容。

- [ ] **AC31 write/edit/run 确认提示**
  验证：这三类工具调用前终端显示参数概要并停顿，等待 y/N 输入；
  输入 y 执行、输入 n 或回车默认拒绝。

## 中断与错误（spec N2/N5）

- [ ] **AC32 工具执行中 Ctrl+C**
  验证：让模型跑 `python -c "import time; time.sleep(30)"`；确认 y
  后命令开始执行；按 Ctrl+C；当前命令被终止；不进入 Round 2；
  回到 `>` 提示符；无 traceback 渗漏。

- [ ] **AC33 确认提示中 Ctrl+C**
  验证：write/edit/run 确认提示等待 y/N 时按 Ctrl+C；当前 turn
  整体取消、剩余 tool_use 跳过、不进入 Round 2、回到 `>` 提示符；
  无 traceback。

- [ ] **AC34 工具失败模型可调整**
  验证：让模型 edit 一个文件中"匹配 3 次"的字符串；Round 2 模型
  应能据错误反馈说明"该字符串出现多次，请告诉我具体哪一处"或
  自行调整。

## 用量、安全（spec F13/N12）

- [ ] **AC35 用量行覆盖 Round 1 + Round 2**
  验证：完整闭环结束后，终端显示一行用量；该行 token 计数为两次
  LLM 请求的累计（input + output 各自累计）。

- [ ] **AC36 api_key 不回显**
  验证：在工具相关的所有终端输出（调用提示、确认提示、简略反馈、
  错误信息）中，api_key 的实际值不出现。
  自动检查：
  ```bash
  python -m mewcode 2>&1 | grep "sk-"
  ```
  对几次工具调用场景下输出无 `sk-` 字符串。

## 兼容性

- [ ] **AC37 第一阶段功能不退化**
  验证：纯对话场景的全部第一阶段验收行为保持通过：
  - 流式逐字打印（AC5）
  - 多轮上下文记忆（AC7）
  - 输入历史回溯（AC8）
  - /clear / /think on/off / /provider 切换 / /providers / /help
  - /exit / /quit 退出码 0
  - 空白下双击 Ctrl+C 退出
  - 长输出 Ctrl+C 中断
  - 配置错误退出码 1

- [ ] **AC38 Windows 终端兼容**
  验证：在 Windows PowerShell 5.x 下完成 AC22 闭环，无 `?[2K` 类
  乱码、无 traceback 渗漏；Markdown 字符按朴素文本显示
  （继承第一阶段降级）。

## 模块集成（plan 层验证）

- [ ] **I1 工具系统始终启用**
  验证：启动后 `python -c` 模拟检查 main 注入的 ToolRegistry 含 6
  工具；`stream_chat` 请求体始终携带 tools 字段（通过临时 print 或
  抓包验证 Round 1 / Round 2 都带 tools）。

- [ ] **I2 模块边界清晰**（结构性 / 代码审查）
  验证：阅读代码确认：
  - `tools/` 模块不依赖 `providers / chat / render / repl`
  - `chat/engine.py` 通过参数注入 `registry / sandbox / confirmer`
  - REPL 不感知 Tool 细节（只透传）
  - Provider 不持有 ToolRegistry（D6）

- [ ] **I3 中文注释与文案**
  验证：随机抽查 5 个新源文件（tools/base.py、tools/read.py、
  tools/sandbox.py、chat/engine.py、render/renderer.py），函数
  docstring 与用户可见提示均为中文。

- [ ] **I4 不引入新依赖**
  验证：`pyproject.toml` 的 dependencies 仍仅含
  prompt_toolkit / rich / PyYAML / httpx 四项；无 ripgrep / pathspec /
  pydantic / jsonschema 等新依赖。

- [ ] **I5 历史合法性**
  验证：通过 stub Provider 单测确认：
  - 工具调用闭环结束后，最近 4 条消息为 user / assistant(R1) /
    user(tool_results) / assistant(R2)
  - assistant(R1) 中每个 ToolUseBlock 在 user(tool_results) 中都
    有对应 tool_use_id 的 ToolResultBlock
  - 用户中断后，messages 末尾不存在 R1 assistant（已 pop）

## 依赖一致性

- [ ] **D1 依赖列表精简（继承第一阶段）**
  验证：pyproject.toml dependencies 4 项；dev 依赖 2 项
  （pytest、pytest-asyncio）。

- [ ] **D2 Python 版本要求**
  验证：`requires-python = ">=3.10"`；当前运行 Python ≥ 3.10。
