# MewCode 第四阶段 Spec

## 背景

MewCode 第三阶段已交付 Agent Loop（docs/04/）：模型可以多轮自主调用工具
完成复杂任务，并支持 Plan/Do 两段式工作流。但当前的 system prompt 仍是
第二阶段的简单文本（约 30 行），存在三个核心问题：

1. **指令未结构化**：身份、规则、风格全部塞在一段连续文本里，新增/修改
   规则时容易破坏既有内容；模型对"哪段是核心约束"也难以分级理解
2. **缓存浪费**：稳定的指令（不会变的身份、规则）和动态的环境信息
   （cwd、Python 版本）混在 system 字段，每次 process 启动整个 system
   都重新计费——本应走缓存通道的稳定段没用上 prompt cache
3. **运行时无法注入动态指令**：Plan Mode 切换、思考模式、token 紧张等
   会话级状态无法用"系统级补充消息"形式提醒模型，只能依赖一次性的
   静态 system prompt

本阶段（第四阶段）让 MewCode 从"能干活"演化为"干得好"——把系统提示
工程化：分模块、分缓存通道、动态注入。

## 目标

- 把全局指令按职责拆成 7 个固定模块 + 1 个环境信息段 + 3 个可选模块，
  按优先级拼装，新增模块只需在拼装顺序里加一行
- 区分稳定内容与动态内容：稳定指令与工具描述走 prompt cache（Anthropic
  显式 cache_control，OpenAI 自动），动态环境信息走消息通道
- 在工具描述与全局指令里双重强化关键规则（如"优先用专用工具"、
  "编辑前必先读"），提高模型对约定的遵守率
- 引入 `<system-reminder>` 标签的系统级补充消息，运行时拼接到当前 turn
  的 user 消息开头，既不污染缓存，也不会被模型当作用户输入回复
- Plan Mode 提醒按"首轮完整、间隔 5 轮重复、其余精简"节奏注入，控制
  注入频率
- 通过 Provider 层暴露 `cache_creation_input_tokens` 与
  `cache_read_input_tokens`，验证缓存策略真生效
- 第一/二/三阶段所有已通过的功能不退化

## 功能需求

### F1. 7 个固定模块的结构化拆分

新增 `mewcode/system_prompt/` 子模块，把单文件 `system_prompt.py` 升级为
分模块体系：

```
mewcode/system_prompt/
├── __init__.py        # 暴露 build_system_prompt(...)
├── modules.py          # 7 个固定模块的文本常量
├── env.py              # 环境信息生成（cwd / Python / shell / 工具列表）
├── reminders.py        # <system-reminder> 注入逻辑
└── builder.py          # 把模块按顺序拼装为最终 system 字符串
```

7 个固定模块（按拼装顺序）：

| # | 模块名 | 职责 | 中文字数量级 |
|---|--------|------|------|
| 1 | 身份 | "你是 MewCode 的 AI 编程助手，运行在用户终端" | ~80 |
| 2 | 系统约束 | 沙盒边界、不可越权、不修改 mewcode.yaml | ~150 |
| 3 | 任务模式 | do（默认可读写）/ plan（只读规划）两种模式 | ~120 |
| 4 | 动作执行 | 调工具前先想、不假设、出错即调整、ReAct 节奏 | ~200 |
| 5 | 工具使用 | 优先专用工具、edit 前必先 read、参数规范 | ~250 |
| 6 | 语气风格 | 中文优先、简洁、不啰嗦、避免空话 | ~80 |
| 7 | 文本输出 | Markdown、代码块语言、长度控制 | ~120 |

总计约 1000 字。每个模块以 `## <模块名>\n` 二级标题开头，模块之间用空行
分隔。后续章节扩展时按职责增删内容，不改变拼装顺序。

### F2. 环境信息段

环境信息在 7 个固定模块之后追加，含：
- 操作系统名 + 版本（platform.platform()）
- Python 版本（sys.version_info）
- 工作目录绝对路径
- shell 提示（继承第二阶段：win 用 cd/echo %cd%，nix 用 POSIX）
- 已注册工具的名称列表（按字母序）

环境信息以 `## 当前环境\n` 开头。本阶段视为"相对稳定"——cwd 不变、
工具列表启动后不变，所以仍放在 system 字段中（一并走缓存）。如果未来
支持运行时 chdir，再拆出去走消息通道（spec Q7 D 决策）。

### F3. 可选模块预留

可选模块在 7 个固定模块 + 环境信息之后追加：

| # | 模块名 | 用途 | 本阶段是否实现 |
|---|--------|------|---------------|
| 8 | 自定义指令 | 用户的项目级指令文件（如 CLAUDE.md） | 不做（后续章节） |
| 9 | 已激活的 Skill | mew-spec 等 skill 的内容 | 不做（后续章节） |
| 10 | 长期记忆 | 跨会话记忆 | 不做（后续章节） |

本阶段在 builder 中预留 hook 但不实际拼接——后续章节加内容时只需调用
`builder.append_optional("custom_instructions", text)` 即可。

### F4. Anthropic 协议显式 cache_control

Anthropic API 的 `system` 与 `tools` 字段都支持 `cache_control` 字段：

```json
{
  "system": [
    {"type": "text", "text": "<完整 system 文本>",
     "cache_control": {"type": "ephemeral"}}
  ],
  "tools": [
    {"name": "read", "description": ..., "input_schema": ...},
    {"name": "write", ..., "cache_control": {"type": "ephemeral"}}  // 最后一项
  ],
  "messages": [...]
}
```

实现：
- AnthropicProvider 把 system 字符串包装为带 cache_control 的列表形式
- ToolRegistry 新增方法 `to_anthropic_format_with_cache()` 在最后一个工具
  上加 cache_control（不修改原 `to_anthropic_format()`，保留兼容）
- 当 system 为空时，不加 cache_control（避免无效请求）

OpenAI/DeepSeek 协议：依赖**自动 prompt cache**——只要请求的 messages
前缀（system + 历史消息）字节级一致，后端会自动命中。本阶段不做显式
cache_control 标记。

### F5. system 与 tools 双重强化关键规则

在 7 个固定模块的"工具使用"中明确：
- "优先用专用工具：read/glob/search 而非 `run cat/dir`"
- "edit 前必先 read 目标文件"
- "路径操作限定在工作目录内，不要尝试访问 cwd 外的文件"

同时在每个 Tool 的 description 中加入对应提示：
- `ReadTool.description`：补 "edit 之前必先用 read 确认原文"
- `EditTool.description`：补 "调用前必须已经 read 过此文件"
- `RunTool.description`：补 "优先用 read/glob/search 而非 cat/dir"

双重强化的好处：模型在生成 tool_use 前会先看到工具描述，再次提醒；
即使 system 被压缩，工具描述里的提示仍生效。

### F6. <system-reminder> 系统级补充消息

定义一种特殊形式的"伪 user 消息"：内容用 `<system-reminder>` 标签包裹。
模型行业惯例：识别此标签为系统补充信息，不当作用户输入回复。

格式：
```
<system-reminder>
[Plan Mode] 当前处于 Plan Mode（计划模式）。仅可使用只读工具
（read / glob / search）。不要尝试修改文件、执行命令；如需写操作，
请告诉用户切换到 /do 模式。
</system-reminder>

<用户实际输入>
```

注入位置：
- 拼接到**当前 turn 的 user 消息开头**（spec Q13 / D5）
- chat.engine._consume_round 在调 `stream_chat` 前构造临时 messages 副本
  做拼接，不污染 `session.messages`
- 临时副本只对当前请求生效；下一轮迭代重新构造

### F7. Plan Mode 提醒的注入节奏

Plan Mode 下按以下节奏注入 reminder：
- **首轮**（用户切换到 plan 后的第一个 user prompt）：注入完整 reminder
  （约 100 字，详细说明规则）
- **间隔重复**（之后每 5 轮的第 1 轮，即第 6/11/16... 轮）：注入完整
  reminder
- **其余轮**：注入精简 reminder（约 30 字，仅 "[Plan Mode 仍然激活]"）

切回 Do Mode 后所有注入停止。

记忆机制：Session 新增字段 `plan_turn_count: int = 0`——只在 Plan Mode
下递增；切回 Do 时重置为 0。

### F8. 缓存命中字段暴露

修改 Provider 层让 Usage 类型增加可选字段：
```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None = None
    cache_creation_input_tokens: int | None = None  # 新增
    cache_read_input_tokens: int | None = None      # 新增
```

AnthropicProvider 在解析 `message_start` 与 `message_delta` 的 usage 时
提取这两个字段。OpenAI 协议下保持 None。

UsageTotal 也增加对应累计字段。Renderer 默认不显示（避免噪音），后续
章节如需可加 verbose 选项。

### F9. 缓存命中验证脚本

新建 `scripts/verify_cache_hit.py`：
1. 启动一次进程
2. 发送相同的 prompt 两次（确保第一次已 build cache）
3. 解析两次的 Usage：
   - 第一次：`cache_creation_input_tokens > 0`，`cache_read_input_tokens == 0`
   - 第二次：`cache_creation_input_tokens == 0`（或很小），
     `cache_read_input_tokens > 0`（≈ system + tools 的 token 数）
4. 断言通过则打印"✓ 缓存策略生效"

### F10. 与第三阶段的兼容

- Provider 接口不变（只新增 Usage 字段，向后兼容）
- ToolRegistry 接口不变（新增方法不删旧方法）
- chat.engine 内部修改透明，run_turn 签名不变
- Session 新增字段（plan_turn_count），不破坏已有字段
- system_prompt.py 旧 API 保留兼容包装：旧的
  `from mewcode.system_prompt import build_system_prompt` 仍能 import
  并工作

### F11. 不做的事

本阶段明确不做：
- 项目指令文件加载（CLAUDE.md / .mewcoderc 等）——后续章节
- 自动记忆（跨会话状态）——后续章节
- 真实 MCP 协议适配——后续章节
- 自动化评估（基准测试集 + 打分）——后续章节
- 多轮历史压缩——后续章节
- 用户配置 system prompt 内容——后续章节
- 配置化迭代上限/并发数（仍硬编码）——后续章节

## 非功能需求

### N1. 模块边界

- `system_prompt/` 子模块不依赖 chat / providers / tools 业务模块
  （只依赖 stdlib + 通过参数注入工具列表）
- chat.engine 通过 builder.build 拿到完整 system 字符串，不感知模块拆分
- AnthropicProvider 知道 cache_control 的协议格式，不感知 system 内容
- Renderer 不感知 cache 字段（Usage 字段对其透明）
- Session 增加字段不影响其他模块

### N2. 不引入新依赖

`pyproject.toml` 的 dependencies 仍仅 prompt_toolkit / rich / PyYAML /
httpx 四项。

### N3. 中文优先

所有 7 个固定模块的文本、reminder 内容、错误提示均为中文。代码注释与
docstring 也用中文。

### N4. 单测覆盖

新增单测覆盖：
- 7 个模块独立可获取（不为空字符串）
- builder.build 拼接顺序稳定（snapshot 测试）
- Plan Mode reminder 节奏（第 1/6/11 轮完整，第 2/3/4/5 轮精简）
- AnthropicProvider 请求体含 system 与 tools 的 cache_control 字段
- ToolRegistry.to_anthropic_format_with_cache 最后一项含 cache_control

预计新增单测约 12-15 个。

### N5. 第一/二/三阶段不退化

- 112 个已有单测全过
- 已有端到端脚本（verify_t9/t18/t19/round_loop/system_prompt/agent_loop/
  plan_mode）全部仍通过
- REPL 命令行为不变
- 工具行为不变
- Agent Loop 行为不变（reminder 注入对模型透明，不影响 Loop 编排）

### N6. cache_control 失败时降级

如果 Anthropic 后端返回 400（不支持 cache_control），AnthropicProvider
应降级为不带 cache_control 的请求重试一次。本阶段简化：先尝试一次，
失败则把错误打到红字，不实现自动重试（避免逻辑复杂度爆炸）。

DeepSeek 的 Anthropic 端点测试支持 cache_control 字段（已验证）。

### N7. system_prompt 长度上限

build_system_prompt 返回的字符串长度本阶段约 1000-1500 字（约 2500-3500
tokens 中文密度）。模块化后每个模块 100-250 字，整体可控。后续若超过
3000 字考虑拆 cache breakpoint。

### N8. <system-reminder> 标签兼容性

- Anthropic 协议：模型行业惯例已经识别此标签为系统补充信息
- OpenAI 协议（DeepSeek）：实测 deepseek-chat 也能识别（不会回复"为啥
  你给我贴了一个 reminder 标签"），但效果可能弱于 Anthropic
- 退路：如果某后端识别异常，可改为 `[SYSTEM REMINDER]...[/SYSTEM REMINDER]`
  纯文本前缀。本阶段先用标准标签，验证后再调整

### N9. api_key 不回显

cache_control 字段不会含 api_key；Usage 增加的 cache 字段也不会含敏感
信息。继承前阶段防线。

### N10. Windows 终端兼容

新增 UI 元素（暂无）；reminder 注入是协议层动作，不影响终端输出。

## 验收标准

### AC1. 7 个固定模块结构化

`from mewcode.system_prompt.modules import IDENTITY, CONSTRAINTS, ...`
能独立 import 7 个模块的文本常量；每个常量是非空字符串、以 `## <名称>\n`
开头。

### AC2. 拼接顺序稳定

`build_system_prompt(cwd, tools)` 返回的字符串：
- 含全部 7 个模块的标题（`## 身份` / `## 系统约束` / ...）
- 模块顺序符合 spec F1 表格
- 模块之间空行分隔

### AC3. 环境信息位置

build_system_prompt 输出的字符串中，`## 当前环境` 出现在所有 7 个固定
模块之后。

### AC4. Anthropic 请求体含 cache_control

通过单测验证：AnthropicProvider 在 `system` 非空时构造的请求体：
- `body["system"]` 是列表形式（不是字符串），最后一项含
  `"cache_control": {"type": "ephemeral"}`
- `body["tools"]` 的最后一项含 `"cache_control": {"type": "ephemeral"}`

### AC5. 缓存命中端到端

`scripts/verify_cache_hit.py` 跑通：连续两次相同 prompt，第二次的
`cache_read_input_tokens > 0`，且至少是第一次 `cache_creation_input_tokens`
的 80%（容忍少量浮动）。

### AC6. <system-reminder> 注入

通过单测验证：
- Plan Mode 下当前 turn 的 user 消息开头含 `<system-reminder>` 标签
- Do Mode 下不含
- session.messages 不被修改（reminder 只在临时副本中存在）

### AC7. Plan Mode reminder 节奏

通过单测验证 plan_turn_count 推进：
- 第 1 / 6 / 11 轮注入完整 reminder（含 "Plan Mode" 三个字 + 详细规则）
- 第 2 / 3 / 4 / 5 轮注入精简 reminder（≤ 50 字，仅含状态提示）
- 第 7 / 8 / 9 / 10 轮注入精简 reminder

### AC8. 双重强化

通过单测验证：
- system 中"工具使用"模块包含 "优先用专用工具" 字样
- ReadTool.description 包含 "edit 之前必先用 read 确认原文"
- EditTool.description 包含 "调用前必须已经 read 过此文件"
- RunTool.description 包含 "优先用 read/glob/search"

### AC9. Usage 增加 cache 字段

通过单测验证：构造 Usage(input_tokens=1, output_tokens=2,
cache_creation_input_tokens=10, cache_read_input_tokens=20) 不抛异常；
默认值为 None。

### AC10. AnthropicProvider 解析 cache 字段

通过单测验证：模拟 SSE 帧含 `usage.cache_creation_input_tokens=100,
cache_read_input_tokens=200` → 产生的 Usage 事件这两个字段对应有值。

### AC11. ToolRegistry 新增方法

通过单测验证：
- `registry.to_anthropic_format()` 行为不变（每项不含 cache_control）
- `registry.to_anthropic_format_with_cache()` 最后一项含
  `cache_control={"type":"ephemeral"}`

### AC12. AC13 不退化——所有已有单测

`pytest tests/ -q` 全过（112 已有 + 新增约 12-15）。

### AC13 不退化——已有端到端

verify_t9/t18/t19/round_loop/system_prompt/agent_loop/plan_mode 全部
仍通过。

### AC14. 不退化——切到 Plan Mode 流程

`/plan` 切换 → 输入 prompt → 模型只调只读工具；切到 `/do` → reminder
不再注入；行为与第三阶段一致。

### AC15. 旧 API 兼容

`from mewcode.system_prompt import build_system_prompt` 仍可 import；
传入 `cwd, tools` 参数仍返回字符串（即使内容已升级为 7 模块结构）。

### AC16. 不引入新依赖

`pyproject.toml` 的 dependencies 4 项不变。

### AC17. system 长度

build_system_prompt 输出长度（按 `len(s.encode('utf-8')) / 3` 估算
中文 token 数）在 800-1500 tokens 区间。

### AC18. cache 命中比例

verify_cache_hit.py 第二次 cache_read 占 input_tokens 的比例 ≥ 50%
（说明大头确实走了缓存）。

## 依赖与约束

- 继承前三阶段全部模块结构与接口契约
- 工具集不变（read/write/edit/run/glob/search）
- Provider 接口不变（仅 Usage 增字段）
- ToolRegistry 接口不变（仅新增方法）
- Sandbox / Confirmer 接口不变
- chat.engine.run_turn 签名不变
- 不引入新依赖
- Python 3.10+
