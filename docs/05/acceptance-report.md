# MewCode 第四阶段验收报告

> 按 `docs/05/checklist.md` 逐项验证。
> 验证日期：2026-06-17
> 环境：Windows + Windows PowerShell 5.x + Anaconda Python 3.13.9
> 凭据：DeepSeek（同一 key 复用 anthropic / openai 两条供应商）

---

## 一、自动验证部分

### 编译与测试基础

- [x] **C1 项目可安装** — 继承前阶段
- [x] **C2 包可导入** — `import mewcode` → `0.1.0`
- [x] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **145 passed**
      （第一阶段 31 + 第二阶段 65 + 第三阶段 16 + 第四阶段新增
      24（modules+reminders）+ 9（cache）= 145）
- [x] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q`
- [x] **C5 命令行入口可调用** — `python -m mewcode`

### 7 个固定模块结构化（spec F1）

- [x] **AC1 7 个模块独立可获取** —
      `tests/test_system_prompt_modules.py::test_7_个模块独立可获取` 通过
- [x] **AC2 拼接顺序稳定** —
      `test_FIXED_MODULES_顺序` + `test_build_含全部7个模块标题` +
      `test_build_顺序稳定` 三个测试通过
- [x] **AC3 环境信息位置** — `test_build_环境信息位置` 通过
- [x] **AC17 system 长度合理** —
      `test_build_长度合理` 通过：实测 2082 字符，落在 800-3000 区间

### Anthropic cache_control（spec F4）

- [x] **AC4 Anthropic 请求体含 cache_control** —
      `test_AnthropicProvider_system列表形式_含cache_control` +
      `test_AnthropicProvider_tools_format_透传` 两个测试通过：
      - body["system"] 是列表形式
      - 最后一项含 cache_control={"type":"ephemeral"}
      - tools 数组最后一项也含 cache_control（由 chat 层准备）
- [x] **AC11 ToolRegistry 新增方法** —
      `test_to_anthropic_format_with_cache_最后一项含cache_control` +
      `test_to_anthropic_format_with_cache_其他项不含` +
      `test_to_anthropic_format_不变` 三个测试通过
- [x] **system 为空时不加 cache_control** —
      `test_AnthropicProvider_system为空时不加cache_control` 通过

### 缓存命中验证（spec F8/F9）

- [x] **AC5 缓存命中端到端** —
      `scripts/verify_cache_hit.py` 真实 API 通过：
      ```
      第一次：input_tokens=2286, cache_creation=0, cache_read=0
      第二次：input_tokens=110,  cache_creation=0, cache_read=2176
      ```
      第二次实际计费 input 从 2286 → 110，**节省约 95%**；cache_read 部分
      按 10% 计费，**总体计费节省约 86%**。
- [x] **AC18 cache 命中比例** — 实测 cache_read 占总 input 比例为
      `2176 / (110 + 2176) ≈ 95.2%`，远超 spec 要求的 50%
- [x] **AC9 Usage 字段可构造** —
      `python -c` 测试通过；默认 None 不破坏向后兼容
- [x] **AC10 AnthropicProvider 解析 cache 字段** —
      `test_AnthropicProvider_解析cache字段_message_start` +
      `test_AnthropicProvider_无cache字段时为None` 两个测试通过

### <system-reminder> 注入（spec F6/F7）

- [x] **AC6 reminder 注入位置** —
      `tests/test_reminders.py::test_inject_拼接顺序` 通过
- [x] **AC7 Plan Mode reminder 节奏** —
      `test_count_1/6/11/16_完整版` + `test_count_2到5_精简版` +
      `test_count_7到10_精简版` 共 7 个测试通过
- [x] **inject_into_user_text 逻辑** —
      `test_inject_拼接顺序` + `test_inject_空reminder_直接返回原文` +
      `test_inject_user_text含换行` 三个测试通过
- [x] **plan_turn_count 状态推进** —
      在 chat.engine._inject_reminders 中实现：
      - Do Mode 下重置为 0
      - Plan Mode 下每次 _consume_round（针对真实 user 消息）递增
      - tool_results 消息不递增
      - /clear 与 /provider 切换重置为 0（已在 session.py 中实现）

### 双重强化关键规则（spec F5）

- [x] **AC8 system + 工具描述双重强化** —
      `test_build_双重强化_工具使用模块` 通过：
      - system 中含"优先用专用工具"
      - system 中含"edit 前必先 read"
      - ReadTool.description 含"edit 之前必先用 read 确认原文"
      - EditTool.description 含"调用前必须已经在本会话中 read 过此文件"
      - RunTool.description 含"优先使用 read / glob / search"

### 模块集成（plan 层验证）

- [x] **I1 模块边界清晰** — system_prompt/ 子模块只依赖 stdlib；
      chat.engine 通过 build_plan_reminder + inject_into_user_text 调用；
      Provider 不感知 reminder
- [x] **I2 中文优先** — 7 个模块、reminder、错误提示全中文
- [x] **I3 不引入新依赖** — pyproject.toml dependencies 4 项不变

### 不退化（spec N5）

- [x] **AC12 不退化——已有单测全过** —
      `pytest tests/ -q` 145 全过（112 已有 + 33 新增）
- [x] **AC13 不退化——已有端到端** —
      verify_t9 / verify_t18 / verify_t19 / verify_round_loop /
      verify_agent_loop / verify_plan_mode 全部仍通过
- [x] **AC14 不退化——切到 Plan Mode 流程** —
      verify_plan_mode.py 通过；**新发现**：第四阶段 system + reminder
      让模型在 Plan Mode 下不再尝试调 write，直接给出"请切换到 /do"
      的建议（双重强化效果）
- [x] **AC15 旧 API 兼容** —
      `from mewcode.system_prompt import build_system_prompt` 仍可用；
      第二阶段 verify_t9.py 中的旧调用路径不受影响
- [x] **AC16 不引入新依赖** — 同 I3

### 兼容性

- [x] **Windows 终端兼容** — 所有真实端到端脚本在 Windows PowerShell
      5.x 下运行无 traceback 渗漏

### 依赖一致性

- [x] **D1 依赖列表精简** — pyproject.toml 4 项 dependencies
- [x] **D2 Python 版本要求** — `>=3.10`，运行 3.13.9 满足

### 自动验证小计

**通过 27 项 / 共 27 项 ✅**

---

## 二、关键技术成果

### 1. 模块化 system prompt

旧版 30 行单段文本 → 新版 7 模块 + 环境信息：
```
## 身份                  (~80 字)
## 系统约束              (~150 字)
## 任务模式              (~120 字)
## 动作执行              (~200 字)
## 工具使用              (~280 字)
## 语气风格              (~80 字)
## 文本输出              (~120 字)
## 当前环境              (动态)
```
总长 2082 字符 ≈ 700 token（中文）。

### 2. Prompt cache 实测节省 86%

| | 第一次 | 第二次 |
|--|--|--|
| input_tokens | 2286 | 110 |
| cache_read_input_tokens | 0 | 2176 |
| 实际计费 input | 2286 全价 | 110 全价 + 2176×10% ≈ 328 等效 |
| **节省** | - | **86%** |

### 3. <system-reminder> 双重强化

不仅在 system 中写约定，每个工具的 description 也写对应提示——实测
让模型在 Plan Mode 下**主动**告诉用户"我需要 write 工具但 Plan Mode
不允许，请 /do 切换"，比第三阶段"模型尝试 write 后被运行时拦截"的体验
显著提升。

### 4. 节奏化 reminder 注入

Plan Mode 下：
- 第 1/6/11/... 轮：注入完整 reminder（约 100 字详细说明）
- 其他轮：注入精简 reminder（约 30 字状态提示）
- Do Mode：完全不注入

避免每轮都注入完整 reminder 浪费 token，又避免完全不重复让模型"忘记"
当前模式。

---

## 三、测试日志

```
$ pytest tests/ -q
145 passed in 11.29s

$ python scripts/verify_cache_hit.py
[provider] protocol=anthropic model=deepseek-v4-pro[1m]
[system prompt] 2082 字符
[tools] 6 个工具，含 cache_control

=== 第一次请求 ===
  input_tokens                 = 2286
  output_tokens                = 19
  cache_creation_input_tokens  = 0
  cache_read_input_tokens      = 0

=== 第二次请求（相同 system + tools） ===
  input_tokens                 = 110
  output_tokens                = 29
  cache_creation_input_tokens  = 0
  cache_read_input_tokens      = 2176

=== 分析 ===
✓ 第二次命中缓存：cache_read_input_tokens = 2176
✓ 缓存命中比例达标
✓ 缓存策略生效

$ python scripts/verify_plan_mode.py（新行为）
=== Phase 1: Plan Mode ===
▸ read(path=README.md)
  ✓ read: 读取 0 行
[模型主动说] 要写 test_plan.txt 需要用到 write 工具，但当前处于 Plan Mode
（只读）。请先用 /do 命令切换到 do 模式。
↑ 318 tokens · ↓ 343 tokens
[Plan Mode 下 test_plan.txt 是否被创建] False ✓

=== Phase 2: Do Mode ===
▸ read(path=README.md)
▸ write(path=test_plan.txt, 5 chars)  ✓ 已写入
[Do Mode 下 test_plan.txt 是否被创建] True ✓
```

stderr 全部干净。

---

## 四、整体结论

**第四阶段全部完成**：

- 自动可验证 27/27 项 PASSED
- 145 个单测全过（第一/二/三/四阶段累计）
- 真实 API 端到端：cache 命中节省 86% input 计费
- 第一/二/三阶段功能零退化
- Plan Mode 模型行为提升（主动建议 /do 切换而非"尝试 write 被拦截"）

第四阶段把 MewCode 从"能干活" → "干得好"：通过结构化 system prompt
+ prompt cache 优化 + 系统级补充消息注入，既省 token 又让模型行为
更稳定。下一阶段方向（建议）：

1. **项目级指令文件**（CLAUDE.md / .mewcoderc）—— 把可选模块的
   "自定义指令"接通真实加载逻辑
2. **上下文压缩** —— 长会话超 context window 时摘要早期消息
3. **MCP 协议适配** —— 接入 Claude Desktop / VSCode MCP server 工具生态
4. **跨会话长期记忆** —— 接通可选模块的"长期记忆"hook
