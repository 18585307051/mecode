# MewCode 第四阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证，聚焦系统行为。
> 验证环境：Windows + Windows PowerShell 5.x，项目根
> `e:\AI\vscode_project\mecode`，启动命令 `python -m mewcode`。
> 全部通过后第四阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装** — `pip install -e .` 输出 "Successfully installed"
- [ ] **C2 包可导入** — `python -c "import mewcode; print(mewcode.__version__)"` → `0.1.0`
- [ ] **C3 单元测试全部通过** —
  `pytest tests/ -q`，第一阶段 31 + 第二阶段 65 + 第三阶段 16 + 第四阶段
  约 20 = **130+ 测试全过**
- [ ] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q` 无 error
- [ ] **C5 命令行入口可调用** — `python -m mewcode` 可启动 REPL

## 7 个固定模块结构化（spec F1）

- [ ] **AC1 7 个模块独立可获取**
  验证：
  ```python
  from mewcode.system_prompt.modules import (
      IDENTITY, CONSTRAINTS, TASK_MODE, ACTION,
      TOOL_USAGE, TONE, OUTPUT, FIXED_MODULES,
  )
  print(len(FIXED_MODULES))  # 7
  for m in FIXED_MODULES:
      assert m.startswith("## ") and len(m) > 30
  ```

- [ ] **AC2 拼接顺序稳定**
  验证：`build_system_prompt(cwd, tools)` 输出含全部 7 个 `## ...` 标题，
  顺序为：身份 → 系统约束 → 任务模式 → 动作执行 → 工具使用 → 语气风格 →
  文本输出 → 当前环境

- [ ] **AC3 环境信息位置**
  验证：build_system_prompt 输出中 `## 当前环境` 出现在所有 7 个固定
  模块之后

- [ ] **AC17 system 长度合理**
  验证：build_system_prompt 输出长度（中文按字数计算）在 800-2500 字
  区间，对应约 800-1500 tokens

## Anthropic cache_control（spec F4）

- [ ] **AC4 Anthropic 请求体含 cache_control**
  验证：通过单测：构造 AnthropicProvider 实例，stub stream_post 拦截
  请求体；
  - `body["system"]` 是列表形式，最后一项含
    `"cache_control": {"type": "ephemeral"}`
  - `body["tools"]` 最后一项含
    `"cache_control": {"type": "ephemeral"}`

- [ ] **AC11 ToolRegistry 新增方法**
  验证：
  - `registry.to_anthropic_format()` 行为不变（每项不含 cache_control）
  - `registry.to_anthropic_format_with_cache()` 最后一项含
    `cache_control={"type":"ephemeral"}`
  - 第一项不含 cache_control（仅最后一项是 breakpoint）

- [ ] **system 为空时不加 cache_control**
  验证：通过单测：当 system 参数为 None 或空字符串时，请求体中无
  system 字段或 system 不是列表形式（避免 API 拒绝）

## 缓存命中验证（spec F8/F9）

- [ ] **AC5 缓存命中端到端**
  验证：`python scripts/verify_cache_hit.py` 跑通：
  - 第一次：`cache_creation_input_tokens > 0`，
    `cache_read_input_tokens` 为 None / 0 / 很小
  - 第二次：`cache_read_input_tokens >= 第一次创建值的 80%`
  - 脚本最末打印 `✓ 缓存策略生效`

- [ ] **AC18 cache 命中比例**
  验证：第二次的 `cache_read_input_tokens / input_tokens >= 0.5`
  （大头确实走了缓存）

- [ ] **AC9 Usage 字段可构造**
  验证：
  ```python
  from mewcode.providers import Usage
  u1 = Usage(1, 2)
  assert u1.cache_creation_input_tokens is None
  assert u1.cache_read_input_tokens is None
  u2 = Usage(1, 2, cache_creation_input_tokens=10, cache_read_input_tokens=20)
  assert u2.cache_read_input_tokens == 20
  ```

- [ ] **AC10 AnthropicProvider 解析 cache 字段**
  验证：通过单测：模拟 SSE 帧 `data:{"type":"message_start","message":{...,
  "usage":{"input_tokens":10,"cache_creation_input_tokens":100,
  "cache_read_input_tokens":200}}}` → 流结束的 Usage 事件含
  `cache_creation_input_tokens=100, cache_read_input_tokens=200`

## <system-reminder> 注入（spec F6/F7）

- [ ] **AC6 reminder 注入位置**
  验证：通过单测：构造 mock chat 会话；
  - Plan Mode + 一条 user 消息 → 调 `_consume_round` 后传给 stream_chat
    的 messages 副本，最后一条 user 消息 content[0].text 开头含
    `<system-reminder>` 标签
  - Do Mode → 不含 reminder
  - session.messages 在两种情况下都未被修改（reminder 只在副本）

- [ ] **AC7 Plan Mode reminder 节奏**
  验证：通过单测：
  - `build_plan_reminder(0)` → `""`
  - `build_plan_reminder(1)` → 含 "Plan Mode"，长度 > 80 字（FULL）
  - `build_plan_reminder(6)` → FULL
  - `build_plan_reminder(11)` → FULL
  - `build_plan_reminder(2/3/4/5)` → 短版（≤ 50 字）
  - `build_plan_reminder(7/8/9/10)` → 短版

- [ ] **inject_into_user_text 逻辑**
  验证：通过单测：
  - `inject_into_user_text("REMINDER", "hi")` → `"REMINDER\n\nhi"`
  - `inject_into_user_text("", "hi")` → `"hi"`

- [ ] **plan_turn_count 状态推进**
  验证：通过单测：
  - 进入 Plan Mode → 初始 plan_turn_count=0
  - 每次 _consume_round 在 plan 模式下 → +=1
  - 切到 Do Mode → 重置为 0
  - /clear → 重置为 0
  - /provider 切换 → 重置为 0

## 双重强化关键规则（spec F5）

- [ ] **AC8 system + 工具描述双重强化**
  验证：
  - system 中 TOOL_USAGE 模块含"优先用专用工具"
  - `ReadTool.description` 含 "edit 之前必先用 read 确认原文"
  - `EditTool.description` 含 "调用前必须已经在本会话中 read 过此文件"
  - `RunTool.description` 含 "优先使用 read / glob / search"

## 模块集成（plan 层验证）

- [ ] **I1 模块边界清晰**
  验证：阅读代码确认：
  - `system_prompt/` 不依赖 chat / providers / tools 业务模块
  - `chat.engine` 通过 `build_system_prompt` + `build_plan_reminder` 调用
    （不直接 import modules.py）
  - AnthropicProvider 知道 cache_control 协议格式，不感知 system 内容
  - Renderer 不感知 cache 字段
  - Session 字段增加不破坏其他模块

- [ ] **I2 中文优先**
  验证：抽查 7 个模块文本、reminder 文本、错误提示均为中文；
  代码注释与 docstring 中文

- [ ] **I3 不引入新依赖**
  验证：`pyproject.toml` dependencies 仍仅
  `prompt_toolkit / rich / PyYAML / httpx` 四项

- [ ] **I4 cache_control 失败的降级**
  验证：阅读代码确认 N6 描述的降级逻辑（本阶段不实现自动重试，但
  错误信息要红字打印让用户能定位）

## 不退化（spec N5）

- [ ] **AC12 不退化——已有单测全过**
  验证：`pytest tests/ -q` 全过（112 已有 + 新增约 20）

- [ ] **AC13 不退化——已有端到端**
  验证：以下脚本全过：
  - `python scripts/verify_t9.py`（第一阶段纯对话）
  - `python scripts/verify_t18.py`（Anthropic 工具调用）
  - `python scripts/verify_t19.py`（OpenAI 工具调用）
  - `python scripts/verify_round_loop.py`（第二阶段单轮闭环）
  - `python scripts/verify_system_prompt.py`（环境感知）
  - `python scripts/verify_agent_loop.py`（Agent Loop 多轮）
  - `python scripts/verify_plan_mode.py`（Plan Mode 两段式）
  - `python scripts/verify_t18_config_errors.py`（配置错误退出码）

- [ ] **AC14 不退化——切到 Plan Mode 流程**
  验证：在 REPL 中 `/plan` 切换 → 输入 prompt → 模型只调只读工具；
  切到 `/do` → reminder 不再注入；行为与第三阶段一致

- [ ] **AC15 旧 API 兼容**
  验证：
  - `from mewcode.system_prompt import build_system_prompt` 仍可用
  - 旧 import 路径不变
  - 调用签名不变（cwd, tools 两个参数仍生效）

- [ ] **AC16 不引入新依赖**
  验证：`pyproject.toml` 检查（同 I3）

## 兼容性

- [ ] **Windows 终端兼容**
  verify_cache_hit.py + verify_round_loop.py 在 Windows PowerShell 5.x
  下运行无 `?[2K` 类乱码、无 traceback 渗漏

## 依赖一致性

- [ ] **D1 依赖列表精简（继承前阶段）**
  pyproject.toml dependencies 4 项

- [ ] **D2 Python 版本要求**
  `requires-python = ">=3.10"`，运行环境满足

---

## 自动可验证小计

预计 **约 28 项可自动验证**（含单测 + 端到端脚本 + 协议层 stub 测试）。

## 待手工验证项

- AC14 在 REPL 中交互式切换 /plan ↔ /do 验证 reminder 行为
- 在 Plan Mode 下连续多轮对话观察 reminder 节奏（第 1/6/11 轮长版）

## 失败处理

任何项失败 → 定位到对应 T 任务 → 修复 → 重跑 → 更新 acceptance-report.md。
全部通过后 close 第四阶段。
