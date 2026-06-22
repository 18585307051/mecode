# MewCode 第八阶段 Spec

## 背景

第七阶段交付了项目指令文件加载（docs/08/）。MewCode 现在每个 turn 都
有完整的 system prompt + Agent Loop + 权限系统 + MCP 工具 + 项目规则
注入——能干长任务的完整能力。

但**长任务有个隐藏天敌：context window**。

实测场景：
- 模型 read 一个 5KB 的源文件 → tool_result 占 ~1500 token
- 模型 search "TODO" 拿到 30 个匹配 → tool_result 占 ~3000 token
- Agent Loop 跑 10 个 turn 解一个 bug → 累计 ~50K token
- 跑 30 个 turn 重构一个模块 → 累计接近 100K
- 大模型 deepseek-v4-pro 的 input window 是 128K——再跑下去就崩了

第六到第七阶段的 prompt cache 解决了**重复发送相同内容**的费用问题，
但解决不了**总 token 持续增长**的硬上限问题。

第八阶段为 MewCode 装上**两层上下文压缩**：

```
┌─────────────────────────────────┐
│  第一层：轻量预防                 │  每次请求前都跑
│  - 单工具结果 > 10KB → 存盘+预览  │  - 单消息和 > 25KB → 排序存盘     │
│  → 修改 session.messages 内的     │
│    ToolResultBlock content        │
└──────────────┬──────────────────┘
              │
               ▼
┌─────────────────────────────────┐
│  第二层：重量兜底                  │  接近 window 才跑│  - LLM 摘要早期消息              │
│  - 保留近期原文                  │  - 失败 3 次熔断                 │
│  → 替换 session.messages 早期部分 │
│    + 保留近期 + 加边界 reminder   │
└─────────────────────────────────┘
```

## 目标

- token 估算用"锚定 + 增量"策略：上次 API 实测值 + 本次新增的字符 / 3
  估算
- 第一层：每次请求前遍历最新一条 tool_results 消息，超阈值的工具结果
  存盘到 `<cwd>/.mewcode/transcripts/<session_id>/`，对话内容替换为
  `[已存盘 + 前后预览]`
- 第二层：估算总 token 达到 (window - 13K) 时自动触发摘要；
  `/compact` 命令可手动触发，余量收窄到 3K
- 摘要 prompt 强制：禁调工具 / 先草稿后正式 / 5 段中文结构
- 摘要后构造新 messages：摘要 user 消息（含 system-reminder）+ 近期
  保留区（尾部 10K token 或至少 5 条 + 完整 turn 边界扩展）
- 熔断：连续 3 次失败本会话停用，/clear 重置
- 用户原始 user 消息的 TextBlock 永远不被摘要改写——只可能被存盘+预览
  的是工具结果
- /compact [自定义指示] 命令支持
- 第一/二/三/四/五/六/七阶段功能不退化

## 功能需求

### F1. token 估算（spec Q1 / D1）

新增 `mewcode/compaction/tokens.py` 模块：

```python
def estimate_tokens(
    messages: list[Message],
    last_usage_input_tokens: int = 0,
    anchor_message_count: int = 0,
) -> int:
    """估算当前 messages 的 input_tokens。

    策略：
    - last_usage_input_tokens：上一次 API 响应实测值
    - anchor_message_count：上次响应时 messages 列表长度（锚点）
    - 锚点之后的新增消息按字符 / 3 估算

    Returns:
        估算 token 数（含 system_prompt 不计；调用方在 session 层面处理）
    """
```

- 无 last_usage 时（首次请求 / 切 provider 后）→ 全部 messages 走字符
  估算
- 有锚点时 → 锚点之前的部分相信 last_usage_input_tokens；之后的部分
  按字符估算
- 字符 / 3 是经验系数：英文偏保守，中文较准（中文 1 char ≈ 1.5-2
  token）

### F2. session 字段扩展

`mewcode/chat/session.py` 增加：

```python
@dataclass
class Session:
    ...
    # 第八阶段
    last_usage_input_tokens: int = 0      # 上次 API 响应实测
    last_anchor_message_count: int = 0    # 上次响应时 messages 长度
    compaction_failures: int = 0          # 连续失败计数
    compaction_disabled: bool = False     # 熔断标志
    session_id: str = ""                  # 启动时间戳，存盘目录用
```

session_id 由 main.py 启动时构造（`datetime.now().strftime("%Y%m%d_%H%M%S")`）。

`clear()` 与 `switch_provider()` 重置全部 compaction 状态（含
session_id 重新生成？否——session_id 表示启动时间，不变；但
failures / disabled 重置）。

`stream_chat` 完成后，chat.engine 把本次 Usage 的 input_tokens 写入
`last_usage_input_tokens`，并把当时的 messages 长度写入
`last_anchor_message_count`。

### F3. 第一层：单工具结果存盘（spec F1 第一层 / Q2 / Q3 / Q4）

每次请求前，遍历 session.messages 中**最新一条 tool_results 消息**
（role=user 含 ToolResultBlock）的所有 ToolResultBlock：

```
对每个 ToolResultBlock：
  if len(block.content) > 10240:    # 10KB
      存盘到 .mewcode/transcripts/<session_id>/tool_<turn>_<tool_use_id>.txt
      block 替换为 [前 20 行 + ... + 后 5 行 + 提示重新读取]
```

### F4. 第一层：单消息总和限制（spec F3 触发条件 2）

如果上述步骤后**该消息所有 ToolResultBlock 总字节** > 25KB：

```
按 content 字节数从大到小排序
依次取最大的，存盘 + 替换为预览
直到剩余总字节 <= 25KB 或所有都已存盘
```

注：单工具 ≤ 10KB 但和 > 25KB 的场景才会走这条路（如 5 个 7KB 的工具
结果在同一消息内）。

### F5. 存盘文件格式

文件路径：`<cwd>/.mewcode/transcripts/<session_id>/tool_<msg_idx>_<tool_use_id>.txt`

文件内容：原始 ToolResultBlock.content 的纯字符串（不加前缀）。

### F6. 替换后的预览格式（spec Q4 / D4）

```
[工具结果已存盘到 .mewcode/transcripts/<session_id>/tool_<msg_idx>_<tool_use_id>.txt (12.3KB)]

—— 前 20 行 ——
<前 20 行>

—— 后 5 行 ——
<后 5 行>

完整内容请用 read 工具读取上述文件路径。
```

如果原内容总行数 ≤ 25 行（前 20 + 后 5 = 25），不截取直接保留全文+
路径标注（避免反向放大）。

### F7. session_id 与目录管理

启动时 main.py 构造：
```python
session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
session.session_id = session_id
```

存盘目录由第一层在首次需要存盘时按需创建：
```python
target_dir = cwd / ".mewcode" / "transcripts" / session_id
target_dir.mkdir(parents=True, exist_ok=True)
```

不在启动时创建空目录（避免 .mewcode/ 多无用文件夹）。

退出时**不清理**——transcripts 目录由用户手动维护（可加 .gitignore）。

### F8. 第二层：估算阈值（spec Q5 / D5）

provider 增加 `context_window: int` 字段（部分已知模型给默认值）：

| Provider/Model | window |
|----------------|--------|
| anthropic claude-3-5-sonnet | 200000 |
| anthropic claude-3-7-sonnet | 200000 |
| openai gpt-4o | 128000 |
| openai gpt-4-turbo | 128000 |
| deepseek-v4-pro | 128000 |
| 未知 | 128000 (保守默认) |

session 字段：
- `auto_compact_threshold = window - 13000`
- `manual_compact_threshold = window - 3000`

启动时 main 用 provider.context_window 计算填入 session。

### F9. 第二层：触发判定

每次 run_turn 入口（第一层之后）：

```
estimated = estimate_tokens(...)

if 用户调 /compact:
    if estimated >= manual_threshold:
        触发摘要
    else:
        触发摘要（用户手动要压，必触发，但 prompt 提示"当前 token 不高，
                确认压缩？"——简化：直接触发不询问）

elif estimated >= auto_compact_threshold:
    if not compaction_disabled:
        触发摘要

else:
    跳过
```

**简化**：手动触发时不做"低 token 警告"——用户既然主动调就执行。

### F10. 第二层：近期保留区计算（spec Q6 / Q13 / D6 / D13）

```python
def compute_keep_boundary(messages, last_usage, anchor_count) -> int:
    """从尾部往回数 10K token，至少 5 条；扩展到完整 turn 边界。

    Returns:
        keep_start_index：[keep_start_index:] 是保留区
    """
    # 1. 从尾部向前累加 token，达到 10K 或至少 5 条
    # 2. 扩展边界：往前移动 keep_start_index 直到指向一个真实用户消息
    #    （role=user 且 content 不含 ToolResultBlock）
    # 3. 不允许 keep_start_index == 0（至少要压缩一些东西）；
    #    如果整个历史都不够 10K，跳过摘要直接给警告
```

### F11. 第二层：摘要 LLM 调用

调当前 session.provider.stream_chat：

```python
# 临时构造 messages：[user 消息（含历史文本 + 摘要要求）]
# 不传 tools_format（等价于 tools=[]）→ 强制模型不调工具
# 不传 system 字段（用单独的摘要专用 system）

await provider.stream_chat(
    [Message(role="user", content=[TextBlock(text=user_prompt)])],
    thinking=False,
    system=COMPACTION_SYSTEM_PROMPT,
    tools_format=None,
)
```

收集所有 TextDelta → 完整摘要文本 → 解析 `<summary>` 标签提取正式
摘要部分。

### F12. 摘要 prompt 内容（spec Q7 / Q14 / D7）

**system prompt**（英文约束 + 中文结构）：

```
You are conducting a conversation summarization task. Your output will
REPLACE the early portion of a conversation history.

CRITICAL CONSTRAINTS:
- DO NOT call any tools. This is a summarization task only.
- DO NOT generate code. Describe what was done, not how.
- First write your <analysis> draft (free thinking), then write the
  final <summary>.

The <analysis> section is for your reasoning—it will be discarded.
The <summary> section is what gets retained. Use these EXACT 5
subsections in Chinese:

<summary>
## 会话目标
（用户最初想完成什么？1-2 句话）

## 关键决策
（重要的技术选择、约定、推翻的方案）

## 代码变更
（已修改的文件 + 大致改了什么；不要贴代码）

## 未完成事项
（TODO、失败的尝试、待验证的假设）

## 当前状态
（走到哪一步，下一步应当做什么）
</summary>
```

**user prompt**：

```
以下是需要摘要的对话历史（早期部分）：

[messages 序列化为可读文本，按 turn 分段]

[如果 /compact 带了用户指示]
额外要求：
{user_instruction}
```

### F13. 摘要解析

```python
def extract_summary(llm_output: str) -> str | None:
    """提取 <summary>...</summary> 之间的内容。

    失败条件：
    - 找不到 <summary> 标签
    - <summary> 内容为空
    - 5 段标题至少缺 3 个

    Returns:
        摘要文本 or None（失败）
    """
```

失败 → 返回 None → 触发熔断计数 +1。

### F14. 摘要后的 messages 重组（spec Q8 / D8）

```python
new_messages = [
    Message(role="user", content=[TextBlock(text=BOUNDARY_REMINDER)]),
    *messages[keep_start_index:],
]
```

`BOUNDARY_REMINDER` 内容：

```
<system-reminder>
[Context Compacted]
上面是早期对话的摘要（从 X 条消息压缩而来）。
重要：完整文件内容请重新用 read 工具读取，不要从摘要中脑补具体代码。
压缩时间：YYYY-MM-DD HH:MM:SS

{summary_text}
</system-reminder>
```

注意：BOUNDARY_REMINDER 是 user 消息的 TextBlock，会进入正常的对话流。
不是 system_prompt 的一部分（保持 system_prompt 稳定，cache 不失效）。

session.messages 在原地替换：
```python
session.messages = new_messages
```

last_usage_input_tokens 与 last_anchor_message_count 重置（下次请求重
新锚定）。

### F15. 熔断机制（spec Q10 / D10）

每次第二层失败：
- `session.compaction_failures += 1`
- 失败原因：LLM 错误 / 解析失败 / 网络错

达到 3 次：
- `session.compaction_disabled = True`
- 打印警告：`⚠️ 压缩连续失败 3 次，已停用本会话压缩功能。请 /clear 清空历史或重启 mewcode。`

`/clear` 与 `switch_provider`：重置 failures = 0、disabled = False。

成功一次后：failures 重置为 0。

### F16. /compact 命令

```
/compact                     默认压缩
/compact <自定义指示>         压缩，附加用户指示到 prompt
```

实现：
- 读 ctx.args 拼成 user_instruction
- 调 compactor.compact_now(session, instruction)
- 成功 → 打印 `已压缩。压缩前 X 条消息 / Y tokens → 压缩后 Z 条 / 估算 W tokens。`
- 失败 → 打印失败原因（不计入熔断；用户手动调用的失败不熔断）

注：熔断计数仅在**自动触发**失败时累加；手动触发失败不计入（用户已经
明确决定要压，重试是用户的选择）。

### F17. 不做的事

明确不做：
- 精确 tokenizer（tiktoken / sentencepiece）
- 自定义压缩策略配置文件
- 摘要风格的 ML 优化
- 摘要 cache（每次都是全新调用）
- 跨会话保留摘要历史
- 工具结果存盘的清理策略（用户手动管）
- 第二层触发后再跑第一层（一次请求只跑一次第二层）
- 主动重新读取存盘文件并合并回历史（让模型自己 read）
- /compact threshold 配置命令（保持简单）
- 用单独 provider 做摘要

## 非功能需求

### N1. 模块边界

- 新模块 `mewcode/compaction/`：
  - `__init__.py`：暴露公共 API
  - `tokens.py`：estimate_tokens + serialize_message
  - `lightweight.py`：第一层预防（单工具/单消息）
  - `summarizer.py`：第二层重量摘要 LLM 调用 + prompt 构造
  - `compactor.py`：组合两层 + 状态管理（Compactor 类）
- chat.engine 在 run_turn 入口调 compactor.before_request(session)
- chat.engine 在 stream_chat 完成后调 compactor.update_anchor(session, usage)
- commands/builtin.py 新增 /compact handler
- 不动的模块：
  - providers / render / permissions / mcp / instructions / system_prompt
    全部零修改
  - tools 模块零修改
- session.py 增加字段（不破坏现有签名）

### N2. 不引入新依赖

仍仅 prompt_toolkit / rich / PyYAML / httpx 4 项。
token 估算用纯字符；存盘用 stdlib pathlib + datetime。

### N3. 中文优先

错误提示、warning、命令文档、5 段摘要标题中文。
摘要 system prompt 关键约束用英文（语气更严，避免模型软化执行）。

### N4. 单测覆盖（spec Q15）

约 22 个新测试：
1. token 估算（3）
2. 第一层预防（5）
3. 第二层兜底（6）
4. 熔断（3）
5. /compact 命令（3）
6. 集成（2）

### N5. 不退化

- 320 个已有单测全过
- 9 个端到端脚本仍通过
- run_turn / Provider / ToolRegistry / Sandbox / PermissionPolicy /
  MCP / InstructionsLoader 接口不变
- 无需要压缩的场景（短对话）下 mewcode 行为完全等同第七阶段
- prompt cache 命中：第一层不破坏 cache（system 不变）；第二层会破
  cache 一次（messages 大改），这是预期成本

### N6. Windows 兼容

- session_id 时间戳跨平台
- 文件路径用 pathlib
- 中文摘要标题在 Windows GBK 控制台不崩（已通过 _fix_windows_console
  保证）

### N7. 性能

- 第一层每次请求前跑：实测 < 5ms（仅遍历 + 字符串切片）
- 第二层只在 token 接近上限时跑：3-10s（取决于模型速度）
- 估算函数 < 1ms

### N8. 模块依赖单向

```
mewcode/compaction/
  ↓ 依赖 stdlib + mewcode.providers (Message/TextBlock/Usage)
不依赖：chat / commands / render / permissions / mcp / instructions / tools
```

chat.engine 单方面调 compactor。

## 验收标准

### AC1. token 估算锚定
通过单测：last_usage=1000，anchor=2，messages 加到 5 条 → 估算
= 1000 + (后 3 条字符 / 3)。

### AC2. token 估算无锚点
通过单测：last_usage=0 → 全部 messages 走字符估算。

### AC3. 第一层：单工具存盘
通过单测：构造 ToolResultBlock content 长度 12KB → 第一层处理后该
block.content 替换为预览 + 文件落盘。

### AC4. 第一层：单消息排序存盘
通过单测：3 个 ToolResultBlock 各 8KB / 9KB / 10KB（总 27KB > 25KB）→
存盘最大的 10KB 一个 → 剩余 17KB 即可。

### AC5. 第一层：< 阈值不动
通过单测：单工具 5KB → 不存盘，content 不变。

### AC6. 预览格式：前 20 + 后 5
通过单测：30 行内容存盘后 → 预览含前 20 行 + "—— 后 5 行 ——" + 后 5 行。

### AC7. 预览格式：≤ 25 行不截
通过单测：20 行内容（但字节数超阈值）→ 完整保留。

### AC8. 第二层：估算未达不触发
通过单测：estimated < auto_threshold → before_request 不调摘要。

### AC9. 第二层：估算达阈值触发
通过 stub LLM 单测：estimated >= auto_threshold → 调摘要 prompt → 收
到模拟摘要 → messages 已替换。

### AC10. 摘要 prompt 含 5 段
通过单测：构造的 system prompt 含 5 段中文小标题。

### AC11. 摘要 prompt 禁工具
通过单测：调 stream_chat 时 tools_format=None。

### AC12. 摘要解析 <summary> 标签
通过单测：模拟 LLM 输出含 `<analysis>...</analysis><summary>...</summary>`
→ extract_summary 仅返回 summary 部分。

### AC13. 摘要解析失败
通过单测：模拟 LLM 输出无 `<summary>` → extract_summary 返回 None。

### AC14. 近期保留区计算
通过单测：构造 20 条 messages 共 30K token → keep_start 落在 10K 边界
+ 真实用户消息边界。

### AC15. 至少 5 条
通过单测：20 条总 5K token → keep_start 至少使后 5 条保留。

### AC16. 边界 reminder 含 system-reminder
通过单测：摘要后第 0 条 message.content[0].text 含 `<system-reminder>`
+ `[Context Compacted]`。

### AC17. 熔断 3 次后停用
通过 stub 单测：连续 3 次摘要失败 → session.compaction_disabled = True。

### AC18. 熔断后跳过
通过单测：disabled=True → before_request 跳过第二层。

### AC19. /clear 重置熔断
通过单测：disabled=True → /clear → disabled=False, failures=0。

### AC20. /compact 命令
通过单测：/compact → 调 compactor.compact_now（即便 token 未达阈值）。

### AC21. /compact 自定义指示
通过单测：/compact 重点保留架构 → user_instruction 拼接到 prompt。

### AC22. /compact 失败不熔断
通过单测：/compact 失败 → failures 不增加。

### AC23. 不退化
- 320 已有单测全过
- 端到端脚本（verify_t9 / verify_t18 / verify_t19 / verify_round_loop /
  verify_agent_loop / verify_plan_mode / verify_cache_hit /
  verify_permissions / verify_mcp / verify_instructions）全过

## 依赖与约束

- 继承前七阶段全部接口契约
- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台
