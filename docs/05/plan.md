# MewCode 第四阶段 Plan

> 基于已批准的 `docs/05/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第三阶段的兼容矩阵。

## 1. 架构概览

```
┌────────────────────────────────────────────────────────────────────┐
│  main.py 启动                                                      │
│    cwd + tools → build_system_prompt() → Session.system_prompt    │
└────────────────────────┬───────────────────────────────────────────┘
                         │
                         ▼
            ┌────────────────────────────┐
            │ system_prompt/  (新拆分)    │
            │   modules.py   (7 固定模块) │
            │   env.py       (环境信息)   │
            │   reminders.py (动态注入)   │
            │   builder.py   (拼装)       │
            └─────────┬──────────────────┘
                      │ 完整 system 字符串
                      ▼
        ┌──────────────────────────────────────┐
        │ chat/engine.py                        │
        │   _consume_round                      │
        │     ↓ 临时副本拼接 reminder           │
        │   provider.stream_chat(messages_temp, │
        │     system, tools_format)             │
        └─────────┬────────────────────────────┘
                  │
        ┌─────────┴────────┐
        ▼                  ▼
┌────────────────┐  ┌──────────────────┐
│ Anthropic      │  │ OpenAI/DeepSeek  │
│  system: list  │  │  system 拼为     │
│   含 cache_    │  │   messages 首条  │
│   control      │  │  (自动 cache)    │
│  tools: 末尾   │  │                  │
│   含 cache_    │  │                  │
│   control      │  │                  │
└────────────────┘  └──────────────────┘
        │                  │
        └──────┬───────────┘
               │ Usage 含 cache_creation/read 字段
               ▼
        ┌────────────────┐
        │ Renderer       │
        │ UsageTotal 仍按 │
        │ 第三阶段渲染    │
        └────────────────┘
```

### 层次关系

| 层 | 变化 |
|----|------|
| main | 不变（仍调 build_system_prompt） |
| system_prompt | **重写**：单文件 → 子模块 |
| chat.engine | _consume_round 增加 reminder 拼接 |
| chat.session | + plan_turn_count 字段 |
| providers | AnthropicProvider 改 system/tools 字段格式 |
| providers.events | Usage 增 2 个 cache 字段 |
| tools | + readonly 描述强化（双重强化） |
| tools.registry | + to_anthropic_format_with_cache() |
| renderer | 不变 |
| commands | 不变 |
| repl | 不变 |

## 2. 模块设计

### 2.1 system_prompt/ 子模块

#### modules.py（7 固定模块）

```python
"""7 个固定模块的文本常量。每个常量以 '## <名称>\n' 开头。"""

IDENTITY = """## 身份
你是 MewCode 的 AI 编程助手，运行在用户的终端中。MewCode 是一个命令行
Agent 框架，支持读写文件、执行命令、搜索代码等本地操作。你的角色是
帮助用户高效完成软件工程任务。"""

CONSTRAINTS = """## 系统约束
- 所有路径操作受工作目录沙盒约束，越界路径会被拒绝。
- 不要修改 mewcode.yaml（含 API 凭据，需要用户手动管理）。
- 不要在工具调用中暴露用户的 api_key。
- 仅使用注册过的工具；遇到不存在的工具名要承认错误而非硬编。
- 重要：执行可能影响系统状态的操作（删除、覆盖、网络请求）前要谨慎确认必要性。"""

TASK_MODE = """## 任务模式
你支持两种工作模式：
- do（默认）：可读写、可执行命令，全部 6 个工具可用
- plan：只可读（read / glob / search），用于先规划再执行的场景

模式由用户通过 /do 或 /plan 命令切换；模式状态会通过系统级补充消息
（含 <system-reminder> 标签）告知你。Plan Mode 下不要尝试调用写类
工具——会被运行时拦截并报错。"""

ACTION = """## 动作执行
你按 ReAct 模式工作（Reasoning + Acting 交替）：
1. 先想清楚下一步该做什么、需要什么信息
2. 调用工具收集信息或执行操作
3. 看到工具结果后再决定下一步
4. 任务完成时给出文本答复，不再调工具

执行原则：
- 不假设：拿不准就用工具确认（如读文件、列目录）
- 不啰嗦：能用一轮工具调用解决的就别拆成多轮
- 出错就调整：工具返回错误时基于错误信息修正策略
- 知道完成：达到用户目标后停下，不要为了"显得勤奋"继续调工具"""

TOOL_USAGE = """## 工具使用
关键约定（必须遵守）：
- 优先用专用工具：要读文件用 read（不是 `run cat`）；要列文件用 glob
  （不是 `run dir`）；要搜代码用 search（不是 `run grep`）
- edit 前必先 read：使用 edit 工具前，必须已经在本会话中 read 过目标
  文件，确认原文片段；否则替换可能匹配错位置
- 路径用相对：除非必要，路径参数用相对路径（相对工作目录）
- 一次拿够：read 时一次给出合适的 offset/limit 而非反复读小段
- 命令配 shell：run 工具的命令必须匹配当前 shell（Windows cmd 用
  cd/echo %cd%/dir/type；Linux/Mac 用 pwd/ls/cat 等 POSIX 命令）

工具失败：
- 失败的 tool_result 含错误类别与详细描述，根据错误调整下一步
- 路径越界、未找到匹配等结构化错误是正常的，不是系统故障"""

TONE = """## 语气风格
- 中文优先：用户用中文交流，你也用中文回答
- 简洁直接：不绕弯子、不重复用户问题、不空泛承诺
- 不啰嗦：能用一句话说清楚的不写三句
- 不夸赞：少用"您"和过度礼貌；少用"非常好的问题"等套话"""

OUTPUT = """## 文本输出
- 默认 Markdown 格式
- 代码块标注语言：```python / ```bash / ```yaml
- 长输出按结构组织：标题、列表、表格
- 不主动重复刚刚展示过的工具结果（用户已经看到了）
- 简短回答用一两句，复杂答复才上结构"""

# 模块拼装顺序（spec F1）
FIXED_MODULES = [
    IDENTITY,
    CONSTRAINTS,
    TASK_MODE,
    ACTION,
    TOOL_USAGE,
    TONE,
    OUTPUT,
]
```

#### env.py（环境信息）

```python
"""动态环境信息生成。

继承第二阶段 system_prompt.py 的 _detect_shell_hint，但格式化为
统一的 ## 当前环境 段落。
"""

import platform
import sys
from pathlib import Path


def build_env_section(cwd: Path, tools: list[str]) -> str:
    """生成环境信息段落。本阶段视为'相对稳定'，仍走缓存（spec F2）。"""
    plat = platform.system()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    shell_hint = _detect_shell_hint()
    tools_str = " / ".join(tools)

    return (
        "## 当前环境\n"
        f"- 操作系统：{plat}（{platform.platform()}）\n"
        f"- Python 版本：{py_ver}\n"
        f"- 工作目录：{cwd}\n"
        f"- 已注册工具：{tools_str}\n"
        f"- {shell_hint}"
    )


def _detect_shell_hint() -> str:
    if sys.platform == "win32":
        return (
            "默认 shell 是 Windows cmd.exe（不是 bash/PowerShell）。"
            "常用命令对照：查看当前目录用 `cd` 或 `echo %cd%`；"
            "列出文件用 `dir`；查看文件内容用 `type`；"
            "环境变量语法是 `%VAR%`。"
        )
    elif sys.platform == "darwin":
        return "默认 shell 是 macOS 上的 zsh / bash，使用标准 POSIX 命令。"
    else:
        return "默认 shell 是 Linux 上的 bash，使用标准 POSIX 命令。"
```

#### builder.py（拼装入口）

```python
"""把 7 固定模块 + 环境信息 + 可选模块按顺序拼成完整 system 字符串。"""

from pathlib import Path
from mewcode.system_prompt.env import build_env_section
from mewcode.system_prompt.modules import FIXED_MODULES


def build_system_prompt(
    cwd: Path,
    tools: list[str],
    custom_instructions: str | None = None,
    skills: list[str] | None = None,
    memory: str | None = None,
) -> str:
    """构造完整 system 字符串（spec F1 + F2 + F3）。

    顺序：7 固定模块 → 环境信息 → 自定义指令(可选) → Skill(可选) → 记忆(可选)
    模块之间双换行分隔。

    Args:
        cwd: 工作目录绝对路径。
        tools: 已注册工具名列表。
        custom_instructions/skills/memory: 后续章节使用，本阶段不传。
    """
    parts = list(FIXED_MODULES)
    parts.append(build_env_section(cwd, tools))

    # 可选模块（本阶段三个全部为 None）
    if custom_instructions:
        parts.append(f"## 自定义指令\n{custom_instructions}")
    if skills:
        parts.append(f"## 已激活的 Skill\n" + "\n".join(skills))
    if memory:
        parts.append(f"## 长期记忆\n{memory}")

    return "\n\n".join(parts)
```

#### reminders.py（系统级补充消息）

```python
"""<system-reminder> 标签的注入逻辑。"""

PLAN_REMINDER_FULL = (
    "<system-reminder>\n"
    "[Plan Mode] 当前处于 Plan Mode（计划模式）。仅可使用只读工具"
    "（read / glob / search）。不要尝试修改文件、执行命令；如需写操作，"
    "请告诉用户切换到 /do 模式后再试。\n"
    "</system-reminder>"
)

PLAN_REMINDER_SHORT = (
    "<system-reminder>[Plan Mode 仍然激活]</system-reminder>"
)


def build_plan_reminder(plan_turn_count: int) -> str:
    """根据 plan_turn_count 选择完整或精简 reminder（spec F7）。

    plan_turn_count 含义：自切换到 plan 后的第几轮（1-based）。
    - 1 / 6 / 11 / ... 注入完整
    - 其余轮注入精简

    Args:
        plan_turn_count: 第几轮 plan（>=1）。

    Returns:
        包含 <system-reminder> 标签的字符串。
    """
    if plan_turn_count <= 0:
        return ""
    # 第 1 轮，以及之后每隔 5 轮
    if plan_turn_count == 1 or (plan_turn_count - 1) % 5 == 0:
        return PLAN_REMINDER_FULL
    return PLAN_REMINDER_SHORT


def inject_into_user_text(reminder: str, user_text: str) -> str:
    """把 reminder 拼接到 user 消息开头（spec F6）。"""
    if not reminder:
        return user_text
    return f"{reminder}\n\n{user_text}"
```

#### __init__.py（兼容旧 API）

```python
"""system_prompt 子模块出口。

为兼容前几阶段的 `from mewcode.system_prompt import build_system_prompt`
导入路径（旧的 system_prompt.py 单文件），这里继续暴露同名函数。
"""

from mewcode.system_prompt.builder import build_system_prompt
from mewcode.system_prompt.reminders import (
    build_plan_reminder,
    inject_into_user_text,
)

__all__ = [
    "build_plan_reminder",
    "build_system_prompt",
    "inject_into_user_text",
]
```

### 2.2 chat/session.py 修改

新增字段 `plan_turn_count`：

```python
@dataclass
class Session:
    ...
    plan_turn_count: int = 0  # 自切到 plan 的轮数（spec F7）
```

`clear()` 与 `switch_provider()` 时重置为 0；切到 do 时也重置。

### 2.3 chat/engine.py 修改

`_consume_round` 在调 `stream_chat` 前构造临时 messages 副本，把 reminder
拼到当前 turn 的 user 消息开头：

```python
async def _consume_round(session, renderer, registry, allow_tools=True):
    # 构造临时 messages 副本
    messages_to_send = list(session.messages)

    # Plan Mode reminder 注入（spec F7）
    if session.mode == "plan" and messages_to_send:
        last = messages_to_send[-1]
        if last.role == "user":
            session.plan_turn_count += 1
            reminder = build_plan_reminder(session.plan_turn_count)
            if reminder:
                # 找到 last.content 中的第一个 TextBlock 修改其文本
                # 注：要构造新 Message 对象（frozen dataclass）
                new_blocks = []
                injected = False
                for b in last.content:
                    if not injected and isinstance(b, TextBlock):
                        new_blocks.append(TextBlock(
                            text=inject_into_user_text(reminder, b.text)
                        ))
                        injected = True
                    else:
                        new_blocks.append(b)
                # 用新 Message 替换最后一条
                messages_to_send[-1] = Message(role="user", content=new_blocks)

    # 切到 do 时重置 plan_turn_count
    if session.mode == "do":
        session.plan_turn_count = 0

    # 后续与第三阶段一致，但用 messages_to_send 而非 session.messages
    stream = session.provider.stream_chat(
        messages_to_send,
        session.thinking_enabled,
        tools_format=tools_format,
        system=session.system_prompt or None,
    )
    ...
```

### 2.4 providers/events.py 修改

Usage 增加两个可选字段：

```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None = None
    cache_creation_input_tokens: int | None = None  # 新增
    cache_read_input_tokens: int | None = None      # 新增
```

### 2.5 providers/anthropic.py 修改

#### system 字段升级为列表形式

```python
# 修改前：body["system"] = system_str  (字符串形式)
# 修改后：
if system:
    body["system"] = [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
```

#### tools 字段最后一项加 cache_control

```python
# tools_format 在 ToolRegistry 那里已经准备好（含 cache_control）
if tools_format:
    body["tools"] = tools_format
```

#### message_start / message_delta 解析新增 cache 字段

```python
# 在 message_start 分支中：
cache_creation = usage.get("cache_creation_input_tokens")
cache_read = usage.get("cache_read_input_tokens")

# 在 message_stop 时构造 Usage：
yield Usage(
    input_tokens=input_tokens,
    output_tokens=output_tokens,
    thinking_tokens=thinking_tokens,
    cache_creation_input_tokens=cache_creation,
    cache_read_input_tokens=cache_read,
)
```

### 2.6 tools/registry.py 修改

新增 `to_anthropic_format_with_cache()`：

```python
def to_anthropic_format_with_cache(self) -> list[dict]:
    """与 to_anthropic_format 相同，但最后一项含 cache_control（spec F4）。"""
    items = self.to_anthropic_format()
    if items:
        items[-1] = {**items[-1], "cache_control": {"type": "ephemeral"}}
    return items
```

chat.engine 中 `_get_tools_format` 在 anthropic 协议时改用此方法。

### 2.7 工具描述强化（spec F5 双重强化）

修改 3 个工具的 description：

- `ReadTool.description`：末尾追加 "edit 之前必先用 read 确认原文。"
- `EditTool.description`：末尾追加 "调用前必须已经 read 过此文件以确认原文。"
- `RunTool.description`：末尾追加 "优先用 read/glob/search 等专用工具读取信息，"
  "而非 `run cat/dir/grep`。"

## 3. 技术决策

### D1. 为什么 system 升级为列表形式

**决策**：Anthropic 协议下 `body["system"]` 改为列表
`[{"type": "text", "text": ..., "cache_control": {...}}]`。

**理由**：
- Anthropic 官方文档：`system` 支持字符串或列表两种形式；列表形式可以
  对每段独立标记 cache_control
- 字符串形式不能加 cache_control
- DeepSeek 的 Anthropic 端点测试支持列表形式
- 列表形式可以未来轻松拆分多段（如稳定段+动态段）

### D2. 为什么 tools 末尾而非每个工具都加 cache_control

**决策**：只在 tools 列表的**最后一项**加 cache_control。

**理由**：
- Anthropic 文档：cache_control 是"breakpoint"，标记到此位置以前的所有
  内容（含此项）都进入缓存；之前的项不需要重复标记
- Anthropic 限制最多 4 个 breakpoint；tools 6 项各加一个会超
- 最后一项加一个 breakpoint 已能让整个 tools 数组都进缓存

### D3. 为什么不在 OpenAI 协议显式标记 cache

**决策**：OpenAI/DeepSeek 协议依赖自动 prompt cache，不显式标记。

**理由**：
- OpenAI 协议规范没有 cache_control 字段（不同后端有不同实验性扩展）
- DeepSeek 后端内部已实现自动 prefix cache：只要 messages 前缀字节
  完全相同，就自动命中
- 显式标记带来兼容性问题（不同后端语义不一致）
- 自动 cache 已经覆盖核心场景：相同 system + 相同对话历史前缀 = 自动命中

### D4. 为什么 reminder 拼接到 user 消息开头而非追加新消息

**决策**：reminder 与原 user 文本合并为同一个 TextBlock，存在同一个
user message 中。

**理由**：
- 不破坏 messages 数组的角色交替结构（user/assistant/user/...）
- 不增加 token 开销（避免一个独立 Message 的 wrap 开销）
- 缓存友好：reminder 内容相对稳定（长版本+短版本两种），与对话历史
  分离展示
- 业界惯例：Cursor / Claude Code 都用此方式

### D5. 为什么 reminder 节奏是 1/6/11 而非每轮都注入

**决策**：第 1 / 6 / 11 / 16 ... 轮注入完整 reminder，其余轮注入精简版。

**理由**：
- 每轮都注入完整 reminder（约 100 字）→ 每 5 轮就浪费约 500 token
- 完全不重复 → 模型可能在长 plan 会话中"忘记"自己处于 plan mode
- 妥协方案：首轮详细让模型完整理解；之后每 5 轮一次完整提醒，让 attention
  里始终有"plan mode"信号
- 精简版（约 30 字）保持 attention 但不浪费

### D6. 为什么 reminder 在 chat.engine 注入而非 Provider 层

**决策**：chat.engine._consume_round 在临时 messages 副本上拼接。

**理由**：
- Provider 不应该感知"reminder"业务概念（spec N1 模块边界）
- session.messages 应该保持"真实对话历史"语义；reminder 是渲染层
  增强，不属于历史
- 临时副本只在请求构造时存在，不污染状态
- 切换协议（anthropic ↔ openai）时 reminder 行为一致

### D7. 为什么 plan_turn_count 在 Session 里而非 chat.engine 局部变量

**决策**：Session 增加 `plan_turn_count: int = 0` 字段。

**理由**：
- chat.engine.run_turn 的执行是按"turn"为单位的（每次 run_turn 一次
  Loop），跨 turn 状态需要持久化在 Session
- 用户可能 plan 几轮后切到 do 又切回 plan——计数器要能重置
- /clear 与 /provider 时也要重置（继承 mode 字段语义）

### D8. 为什么 Usage 字段用 None 表示"无 cache 信息"

**决策**：`cache_creation_input_tokens` 与 `cache_read_input_tokens` 默认 None。

**理由**：
- OpenAI 协议本身不返回这两个字段，None 表示"协议不支持"
- Anthropic 协议在缓存未命中时也可能不返回（早期版本），None 表示
  "后端未提供"
- 0 与 None 语义不同：0 表示"明确告知没有缓存"，None 表示"未知/不适用"
- Renderer 默认不显示这两个字段，避免噪音；后续 verbose 选项时按 None
  跳过

### D9. 为什么 modules.py 用模块级常量而非函数

**决策**：7 个模块的文本是模块级 `IDENTITY = """..."""` 常量。

**理由**：
- 静态文本不需要参数化——内容稳定才能进缓存
- 常量可以被 import 后单独测试（AC1：每个模块独立可获取）
- 修改时改字符串即可，不需要改函数签名
- 减少运行时拼接成本（已经是字符串了）

### D10. 为什么环境信息仍走 system 字段而非动态消息

**决策**：环境信息追加到 system 字符串末尾，与 7 模块一起进缓存。

**理由**：
- cwd 启动后不变（mewcode 不运行时 chdir）
- 工具列表启动后不变（registry 启动时一次注册）
- 平台/Python 版本是常量
- 走 system 缓存命中率最高
- 后续如果支持运行时 chdir，再拆出去走消息通道（spec Q7 D 决策）

### D11. 为什么不暴露 cache 字段到 UI

**决策**：Renderer 默认不显示 cache_creation/read 字段。

**理由**：
- 用户场景下不关心"这次有没有命中缓存"——只关心总用量
- 显示会让用量行变得啰嗦（`↑ X · ↓ Y · cache_create N · cache_read M`）
- 调试需要时通过 `verify_cache_hit.py` 脚本即可
- 字段对外暴露（在 Usage 类型上），但 Renderer 选择不渲染

### D12. 为什么 description 强化在工具自身而非 system

**决策**：除 system 中的 TOOL_USAGE 模块，每个工具的 description 也加
对应提示。

**理由**：
- 模型在生成 tool_use 前会再看一遍工具描述（attention 集中在描述上）
- 双重强化提升约定遵守率（spec F5）
- 工具描述本身就在 cache 里，不增加 token 成本
- 后续如果某些工具描述变化，单独改即可，不影响 system 缓存

## 4. 时序图

### 4.1 启动时构造 system prompt

```
main.py                  system_prompt
  │                        │
  │ build_system_prompt(   │
  │   cwd, tools)          │
  ├──────────────────────►│
  │                        │ FIXED_MODULES (7 个)
  │                        │ + build_env_section(cwd, tools)
  │                        │ "\n\n".join(parts)
  │◄──────────────────────┤ <完整 system 字符串>
  │                        │
  │ Session(system_prompt=...)
```

### 4.2 Plan Mode 第 1 轮请求

```
chat.engine    session              reminders         provider
  │              │                     │                 │
  │ _consume_round                     │                 │
  │              │                     │                 │
  │ messages_to_send = list(session.messages)            │
  │              │                     │                 │
  │ session.mode == "plan"             │                 │
  │ session.plan_turn_count += 1  (=1) │                 │
  │              │                     │                 │
  │ build_plan_reminder(1)             │                 │
  ├────────────────────────────────────►│                 │
  │◄────────────────────────────────── PLAN_REMINDER_FULL │
  │              │                     │                 │
  │ inject_into_user_text(reminder, last.text)           │
  │   → "<system-reminder>...</system-reminder>\n\n<原始>"  │
  │              │                     │                 │
  │ messages_to_send[-1] = Message(user, [TextBlock(<合并>)])
  │              │                     │                 │
  │ stream_chat(messages_to_send, system, tools_format)   │
  ├──────────────────────────────────────────────────────►│
```

### 4.3 Anthropic 请求体含 cache_control

```
provider.stream_chat                          API
  │                                            │
  │ body = {                                   │
  │   "model": "...",                          │
  │   "system": [{                             │
  │     "type": "text",                        │
  │     "text": system,                        │
  │     "cache_control": {"type":"ephemeral"}  │
  │   }],                                      │
  │   "tools": [                               │
  │     {"name": "read", ...},                 │
  │     ...                                    │
  │     {"name": "search", ...,                │
  │      "cache_control": {"type":"ephemeral"} │  ← 最后一项
  │     }                                      │
  │   ],                                       │
  │   "messages": [...]                        │
  │ }                                          │
  ├────────────────────────────────────────────►│
  │                                            │ 命中缓存
  │ <SSE: message_start>                       │
  │   usage.input_tokens=10                    │
  │   usage.cache_creation_input_tokens=2000   │  (第一次)
  │   usage.cache_read_input_tokens=0          │
  │   或：                                     │
  │   usage.cache_creation_input_tokens=0      │  (第二次)
  │   usage.cache_read_input_tokens=2000       │
```

### 4.4 切换到 Do Mode

```
用户   commands       session       chat.engine
 │       │             │              │
 │ /do   │             │              │
 ├──────►│ _handle_do  │              │
 │       │             │              │
 │       ├ session.mode = "do"        │
 │       ├ session.plan_turn_count = 0│
 │       │             │              │
 │ 下一条 prompt        │              │
 ├─────────────────────────────────────►│
 │       │             │              │ session.mode == "do"
 │       │             │              │ → 不注入 reminder
 │       │             │              │ → 直接发送 messages
```

## 5. 文件清单

| 操作 | 文件 | 行数估计 |
|------|------|----------|
| 删除 | `mewcode/system_prompt.py` | 旧文件 |
| 新建 | `mewcode/system_prompt/__init__.py` | ~25 |
| 新建 | `mewcode/system_prompt/modules.py` | ~120 |
| 新建 | `mewcode/system_prompt/env.py` | ~50 |
| 新建 | `mewcode/system_prompt/reminders.py` | ~60 |
| 新建 | `mewcode/system_prompt/builder.py` | ~50 |
| 修改 | `mewcode/chat/session.py` | + plan_turn_count 字段 + 重置 |
| 修改 | `mewcode/chat/engine.py` | + reminder 注入 |
| 修改 | `mewcode/providers/events.py` | Usage + 2 个 cache 字段 |
| 修改 | `mewcode/providers/anthropic.py` | system 列表形式 + cache 字段解析 |
| 修改 | `mewcode/tools/registry.py` | + to_anthropic_format_with_cache |
| 修改 | `mewcode/tools/read.py` | description 强化 |
| 修改 | `mewcode/tools/edit.py` | description 强化 |
| 修改 | `mewcode/tools/run.py` | description 强化 |
| 新建 | `tests/test_system_prompt_modules.py` | ~80 |
| 新建 | `tests/test_reminders.py` | ~80 |
| 新建 | `tests/test_anthropic_cache.py` | ~100 |
| 新建 | `scripts/verify_cache_hit.py` | ~80 |

共 18 个文件（10 新建/重构 + 8 修改）。

## 6. 与第三阶段的兼容矩阵

| 第三阶段行为 | 第四阶段是否保留 | 说明 |
|-------------|----------------|------|
| run_turn 签名 | ✅ 不变 | REPL 调用方零改动 |
| Provider stream_chat 签名 | ✅ 不变 | 仅 Usage 增字段 |
| ToolRegistry 接口 | ✅ 不变 | 新增方法不删旧 |
| Sandbox / Confirmer | ✅ 不变 | |
| AgentEvent 7 种 | ✅ 不变 | reminder 注入对 Renderer 透明 |
| Plan Mode tools_format 物理隔离 | ✅ 保留 | _get_tools_format 不变 |
| Plan Mode 运行时拦截 | ✅ 保留 | _execute_tool_batch 不变 |
| Agent Loop 多轮 | ✅ 保留 | reminder 注入对 Loop 透明 |
| 5 种停止条件 | ✅ 保留 | |
| 工具调用 SSE 解析 | ✅ 保留 | + cache 字段提取 |
| /clear /provider 重置 mode | ✅ 保留 | + 重置 plan_turn_count |
| `from mewcode.system_prompt import build_system_prompt` | ✅ 保留 | 旧 API 兼容 |
| 112 个已有单测 | ✅ 全过 | 增加测试不删旧 |

### 需要适配的已有测试

无——本阶段所有改动都是**新增**或**向后兼容**：
- Usage 增字段（默认 None，旧代码不感知）
- ToolRegistry 增方法（旧 to_anthropic_format 不变）
- system_prompt 重构但旧 import 路径仍可用
- Session 增字段（旧代码不访问也无影响）

唯一需要注意的：AnthropicProvider 的 system 字段改为列表形式后，
单测需要确认请求体格式正确——但这不属于"已有测试需要适配"，而是
"新功能的测试"。
