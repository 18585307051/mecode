# MewCode 第一阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证，聚焦系统行为。
> 验证环境：Git Bash + tmux（Windows），项目根目录 `e:\AI\vscode_project\mecode`。
> 全部通过后第一阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装**
  验证：项目根执行 `pip install -e .`，输出 "Successfully installed mewcode-0.1.0"，无 error

- [ ] **C2 包可导入**
  验证：`python -c "import mewcode; print(mewcode.__version__)"` 输出 `0.1.0`

- [ ] **C3 单元测试全部通过**
  验证：`pytest tests/ -v`，所有 22 个测试用例通过，0 个失败/错误

- [ ] **C4 全部源文件语法合法**
  验证：`python -m compileall mewcode/`，输出无 error 行

- [ ] **C5 命令行入口可调用**
  验证：`where mewcode`（Windows）输出可执行文件路径；执行 `mewcode` 不报 "command not found"

## 启动与配置（spec F1/F2/F3）

- [ ] **AC1 合法配置启动成功**
  验证：在含合法 `mewcode.yaml` 的目录执行 `mewcode`；终端打印含
  当前供应商名、协议、模型的横幅；出现 `>` 提示符

- [ ] **AC2 缺失配置文件报错**
  验证：在不含 `mewcode.yaml` 的临时空目录执行 `mewcode`；红字错误含
  "配置文件不存在"；Git Bash 中 `echo $?` 输出 `1`

- [ ] **AC3 非法配置报错**
  验证：写一份 default 指向不存在供应商的 `mewcode.yaml`，执行
  `mewcode`；红字错误含 default 字段名；退出码 `1`

- [ ] **AC4 api_key 不回显**
  验证：在四种场景下检查终端任何输出，`grep "sk-" 终端日志` 无匹配：
  ① 正常启动横幅 ② 配置错误（如 protocol 非法）报错
  ③ token 用量行 ④ `/providers` 命令输出

## 多轮对话与流式（spec F4/F5/F6/N1/N2）

- [ ] **AC5 流式逐字打印**
  验证：在 REPL 输入 "用一段话介绍 Python"，肉眼观察 AI 回复**逐字
  逐 chunk 出现**而不是整段一次性出现

- [ ] **AC6 Markdown 渲染**
  验证：输入 "用 Markdown 列 3 个 Python 数据结构，每个配代码示例"；
  终端中标题加粗、代码块带框/底色；终端中**未**出现原始 `#`、`` ``` ``
  字符堆积

- [ ] **AC7 多轮上下文记忆**
  验证：第 1 轮输入 "我叫小明"；第 2 轮输入 "今天天气怎么样"；第 3
  轮输入 "我刚才告诉你我叫什么？"；AI 第 3 轮回复中含 "小明"

- [ ] **AC8 输入历史回溯**
  验证：连续提交三条不同 prompt 后，在空白提示符下按 "上方向键"，
  能依次调出最近三条 prompt

- [ ] **N2 流式不阻塞 / Ctrl+C 响应及时**
  验证：发送让 AI 长回复的 prompt（如 "写 1000 字关于猫的文章"）；
  在流式输出过程中按 Ctrl+C；秒级（< 2 秒）内停止输出并回到 `>`

## Provider 抽象与协议切换（spec F7/F8/F12）

- [ ] **AC9 Anthropic 协议可用**
  验证：`mewcode.yaml` 默认设为 `deepseek-anthropic`；启动后输入
  "你好"；流式收到回复 + Usage 行；启动横幅协议显示 `anthropic`

- [ ] **AC10 OpenAI 协议可用**
  验证：执行 `/provider deepseek-openai` 切换；输入 "你好"；流式
  收到回复 + Usage 行；切换回显含 `openai`

- [ ] **AC11a 运行时切换供应商**
  验证：先 `/providers` 列出含两条供应商，标记当前生效；执行
  `/provider deepseek-openai`；终端打印切换结果；之后再 `/providers`
  当前标记移到 deepseek-openai

- [ ] **AC11b 切换清空历史**
  验证：先告诉 AI "我叫小明"；执行 `/provider deepseek-openai` 切换；
  问 "我刚才说我叫什么？"；AI 表明不知道

- [ ] **AC24 协议扩展性可见**（结构性 / 代码审查）
  验证：阅读 `mewcode/providers/registry.py` 与
  `mewcode/providers/__init__.py`；确认新增协议只需 ① 在 providers
  目录加新 Provider 文件 ② 在 `__init__.py` 追加
  `import + PROVIDER_REGISTRY[<name>] = <cls>` 一行；REPL、配置加载、
  命令分发、对话状态四个模块的代码无需改动

## Extended Thinking（spec F9/F10）

- [ ] **AC12 默认关闭，回复中无思考块**
  验证：启动后直接发起对话；终端**无**灰色斜体思考、**无**
  `▎思考中…` 标记，仅有正常样式的 AI 回复

- [ ] **AC13 开启后思考流式展示（Anthropic）**
  验证：协议为 anthropic 时执行 `/think on`；提复杂问题（如
  "证明素数有无穷多个"）；终端先出现 `▎思考中…` + 灰色斜体思考流；
  思考结束后空行分隔；再以正常样式输出最终回复

- [ ] **AC14 OpenAI 协议下开启思考给出提示**
  验证：当前协议为 openai 时执行 `/think on`；终端打印明确提示
  （含 "不支持"、"协议" 等关键字）；后续对话不出现思考块

## 内置斜杠命令（spec F11/F12）

- [ ] **AC15 /help 列出全部命令**
  验证：执行 `/help`；输出含全部 7 个命令名（`exit`、`quit`、`help`、
  `clear`、`think`、`provider`、`providers`）及简短说明

- [ ] **AC16a /clear 清空消息历史**
  验证：先告诉 AI "我叫小明"；执行 `/clear`；问 "我刚才说我叫什么？"；
  AI 表明不知道

- [ ] **AC16b /clear 不影响输入历史**
  验证：执行 `/clear` 后，按上方向键仍能调出之前提交过的 prompt

- [ ] **AC17 未知命令提示**
  验证：输入 `/foobar`；终端打印 "未知命令" 提示并列出可用命令；
  **未**向大模型发起请求（观察：无流式输出、无 token 用量行）

- [ ] **AC18 /exit 与 /quit 退出**
  验证：分别启动 mewcode → 输入 `/exit`，Git Bash `echo $?` 为 0；
  再次启动 → 输入 `/quit`，`echo $?` 为 0

## 用量展示（spec F13）

- [ ] **AC19a 回复后展示 token 用量**
  验证：完成一次对话后，AI 回复下方出现一行灰色文字，**包含**输入和
  输出 token 数字；thinking 关闭时**不**显示思考项

- [ ] **AC19b thinking 开启时显示思考 token**
  验证：协议 anthropic + `/think on` 后发起对话；用量行**包含**思考
  token 数字（若 backend 不返回则该项缺省，记录实际现象）

## 错误处理（spec F14/N3）

- [ ] **AC20 错误以红字明确报告**
  验证：编辑 `mewcode.yaml` 把 `deepseek-anthropic` 的 api_key 改为
  `sk-invalid`；启动 mewcode；发起一条对话；终端以**红字**打印错误，
  错误信息含 "鉴权失败" 或 "HTTP 错误" 等具体类别；**不**自动重试；
  之后回到 `>` 提示符可继续输入

- [ ] **N3 错误信息含具体原因**
  验证：上面 AC20 测试中的错误信息**不**只是 "出错了"，而是含状态码、
  类别、原始错误描述等可定位信息

## 中断语义（spec N5）

- [ ] **AC21 流式中断不进历史**
  验证：发起长回复 prompt；流式输出中按 Ctrl+C；流式立即停止；终端
  上已打印的字符**保留**显示；下一轮发送 "你刚才说了什么？"；AI 表
  明不记得（中断的回复不在历史中）

- [ ] **AC22 空输入下连按两次 Ctrl+C 退出**
  验证：在空白 `>` 提示符下按 Ctrl+C；提示 "再按一次..."；再按一次
  Ctrl+C；进程退出；Git Bash `echo $?` 为 0

## 终端兼容性与端到端（spec N4）

- [ ] **AC23 tmux 完整端到端场景**
  在新 tmux 会话中按以下顺序完整跑通，每一步无异常：
  1. `cd /e/AI/vscode_project/mecode && mewcode`
  2. 看到横幅 → 输入 "用一段话介绍 Python，含一个代码示例"
  3. 观察流式 + Markdown + 灰字用量行
  4. `/think on` → 提 "如何证明圆周率是无理数"
  5. 观察灰色斜体思考 + 空行 + 正文 + 含思考 token 的用量行
  6. `/clear` → 问 "我刚才问了什么？" → AI 表明不知道
  7. `/provider deepseek-openai` → 看到切换信息
  8. `/think on` → 看到 "不支持" 提示
  9. 输入 "你好" → 收到回复 + 用量行
  10. `/exit` → 退出码 0

  全程无控制字符泄漏、无渲染错乱、颜色样式正常

## 模块集成（plan 层验证）

- [ ] **I1 配置层与 Provider 层正确集成**
  验证：通过启动横幅显示的 protocol/model 与 `mewcode.yaml` 中
  default 指向的供应商一致

- [ ] **I2 Renderer 单点封装**（结构性 / 代码审查）
  验证：阅读代码确认 `chat/engine.py`、`commands/builtin.py`、
  `repl/main_loop.py`、`main.py` 中**没有**直接 import 或调用 rich
  的 `Console.print`、`Live` 等 API；所有终端输出经 Renderer

- [ ] **I3 中文注释与文案**
  验证：随机抽查 5 个源文件，函数 docstring 与用户可见提示均为中文；
  无中英混杂或英文残留（除变量名、技术术语外）

- [ ] **I4 不引入官方 SDK**
  验证：`pip list | grep -i -E "anthropic|openai"` 输出为空（除非
  系统已装但本项目不依赖）；`pyproject.toml` 的 dependencies 不含
  anthropic-sdk-python 或 openai 字眼

- [ ] **I5 配置文件未进 git**
  验证：`git status` 输出中**不**出现 `mewcode.yaml`；
  `git check-ignore mewcode.yaml` 输出 `mewcode.yaml`

## 依赖一致性

- [ ] **D1 依赖列表精简**
  验证：`pyproject.toml` 的 `dependencies` 仅含 `prompt_toolkit`、
  `rich`、`PyYAML`、`httpx` 四个；dev 仅含 `pytest`、`pytest-asyncio`

- [ ] **D2 Python 版本要求**
  验证：`pyproject.toml` 的 `requires-python` 为 `>=3.10`；当前
  python 版本 `python --version` ≥ 3.10
