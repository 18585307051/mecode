# MewCode 第四阶段 Tasks

> 基于已批准的 `docs/05/spec.md` 与 `docs/05/plan.md`。共 14 个任务，
> 覆盖 system_prompt 子模块化、reminder 注入、cache_control 协议改造、
> Usage 字段扩展、工具描述强化、单测与端到端验收。

## 文件清单

| 操作 | 文件 |
|------|------|
| 删除 | `mewcode/system_prompt.py`（改为目录） |
| 新建 | `mewcode/system_prompt/__init__.py` |
| 新建 | `mewcode/system_prompt/modules.py` |
| 新建 | `mewcode/system_prompt/env.py` |
| 新建 | `mewcode/system_prompt/reminders.py` |
| 新建 | `mewcode/system_prompt/builder.py` |
| 修改 | `mewcode/chat/session.py` |
| 修改 | `mewcode/chat/engine.py` |
| 修改 | `mewcode/providers/events.py` |
| 修改 | `mewcode/providers/anthropic.py` |
| 修改 | `mewcode/tools/registry.py` |
| 修改 | `mewcode/tools/read.py` |
| 修改 | `mewcode/tools/edit.py` |
| 修改 | `mewcode/tools/run.py` |
| 新建 | `tests/test_system_prompt_modules.py` |
| 新建 | `tests/test_reminders.py` |
| 新建 | `tests/test_anthropic_cache.py` |
| 新建 | `scripts/verify_cache_hit.py` |

共 18 个文件（10 新建 + 1 删除 + 7 修改）。

---

## 任务执行顺序图

```
T1 ──→ T2 ──→ T3 ──→ T4 ──→ T5 ─┐
                                 │
                  T6 (reminders)─┤
                                 ▼
                   T7 (test modules) + T8 (test reminders)
                                 │
                  T9 (Usage 字段) ──→ T10 (Anthropic cache) ──┐
                                                              │
                  T11 (Tool desc 强化) + T12 (registry cache)─┤
                                                              ▼
                                T13 (test_anthropic_cache + 单测合一)
                                                              │
                                              T14 (verify_cache_hit + 验收)
```

**关键路径**：T1→T2→T3→T5→T6→T9→T10→T13→T14（9 步主线）

**可并行**：
- T11（工具描述）独立，可与 T9-T10 并行
- T7/T8（modules + reminders 单测）可在 T6 完成后并行
- T12（registry 新方法）可与 T11 并行

---

## T1：删除旧的 system_prompt.py + 创建子模块目录

**文件：**
- 删除 `mewcode/system_prompt.py`
- 新建 `mewcode/system_prompt/`（空目录）

**依赖：** 无

**步骤：**

1. 备份当前 `system_prompt.py` 内容（_detect_shell_hint 函数会被复用到 env.py）
2. 删除 `mewcode/system_prompt.py`
3. 新建空目录 `mewcode/system_prompt/`

**验证：**
- `dir mewcode\system_prompt` 显示目录存在
- 删除后短暂期间 `import` 会失败——T2 后恢复

---

## T2：modules.py（7 固定模块）

**文件：**
- 新建 `mewcode/system_prompt/modules.py`

**依赖：** T1

**步骤：**

1. 按 plan 2.1 节定义 7 个常量：IDENTITY / CONSTRAINTS / TASK_MODE /
   ACTION / TOOL_USAGE / TONE / OUTPUT
2. 每个常量以 `## <名称>\n` 开头
3. 每个常量字数控制：标准型（100-250 字），按 plan 表格
4. 模块顶部 docstring 说明拼装顺序与扩展指南
5. 末尾定义 `FIXED_MODULES = [...]` 列表（按拼装顺序）

**验证：**
- `python -c "from mewcode.system_prompt.modules import FIXED_MODULES; print(len(FIXED_MODULES))"` 输出 `7`
- 每个常量非空，含 `##` 标题

---

## T3：env.py（环境信息）

**文件：**
- 新建 `mewcode/system_prompt/env.py`

**依赖：** T1

**步骤：**

1. 复用旧 system_prompt.py 的 `_detect_shell_hint` 函数
2. 新增 `build_env_section(cwd, tools) -> str`：
   - 含 `## 当前环境\n` 标题
   - 操作系统、Python 版本、工作目录、工具列表、shell 提示
3. 文件顶部 docstring 说明本阶段视为"相对稳定"

**验证：**
- `python -c "from pathlib import Path; from mewcode.system_prompt.env import build_env_section; print(build_env_section(Path.cwd(), ['read','glob']))"`
- 输出含 `## 当前环境`、`已注册工具：read / glob`、shell 提示

---

## T4：builder.py（拼装入口）

**文件：**
- 新建 `mewcode/system_prompt/builder.py`

**依赖：** T2、T3

**步骤：**

1. 实现 `build_system_prompt(cwd, tools, custom_instructions=None,
   skills=None, memory=None) -> str`
2. 按 spec F1 顺序：FIXED_MODULES + build_env_section + 可选模块
3. 模块之间用 `\n\n` 分隔
4. 可选模块为 None 时跳过

**验证：**
- `python -c "from pathlib import Path; from mewcode.system_prompt.builder import build_system_prompt; s = build_system_prompt(Path.cwd(), ['read','write']); print(len(s)); print('## 身份' in s); print('## 当前环境' in s)"`
- 输出长度约 1000-1500，所有模块标题都存在

---

## T5：reminders.py（动态注入）

**文件：**
- 新建 `mewcode/system_prompt/reminders.py`

**依赖：** T1

**步骤：**

1. 定义 `PLAN_REMINDER_FULL`（约 100 字，含 `<system-reminder>` 标签）
2. 定义 `PLAN_REMINDER_SHORT`（约 30 字）
3. 实现 `build_plan_reminder(plan_turn_count: int) -> str`：
   - count <= 0 → ""
   - count == 1 或 (count - 1) % 5 == 0 → FULL
   - 其他 → SHORT
4. 实现 `inject_into_user_text(reminder: str, user_text: str) -> str`：
   - reminder 为空时直接返回 user_text
   - 否则 `f"{reminder}\n\n{user_text}"`

**验证：**
- `python -c "from mewcode.system_prompt.reminders import build_plan_reminder; print(build_plan_reminder(1)); print(build_plan_reminder(2)); print(build_plan_reminder(6))"`
- 第 1 / 6 轮输出长 reminder，第 2 轮输出短 reminder

---

## T6：__init__.py（兼容旧 API）

**文件：**
- 新建 `mewcode/system_prompt/__init__.py`

**依赖：** T2、T3、T4、T5

**步骤：**

1. import build_system_prompt from builder
2. import build_plan_reminder, inject_into_user_text from reminders
3. 写入 `__all__`
4. 文件 docstring 说明兼容旧 import 路径

**验证：**
- `python -c "from mewcode.system_prompt import build_system_prompt, build_plan_reminder, inject_into_user_text; print('ok')"`
- 旧路径仍可用：`python -c "from mewcode.system_prompt import build_system_prompt; print('compat ok')"`

---

## T7：tests/test_system_prompt_modules.py

**文件：**
- 新建 `tests/test_system_prompt_modules.py`

**依赖：** T2-T6

**步骤：**

1. 测试每个模块独立可获取（IDENTITY 等 7 个常量）
2. 测试每个模块以 `## <名称>\n` 开头
3. 测试每个模块非空且字数在 30-500 字之间（中文字符）
4. 测试 FIXED_MODULES 长度为 7
5. 测试 build_system_prompt 输出含 7 个 `## ...` 标题 + `## 当前环境`
6. 测试 build_system_prompt 顺序稳定（连续两次调用结果完全相同）
7. 测试 build_system_prompt 长度在 800-2500 字符（中文字符宽松上限）

**验证：**
- `pytest tests/test_system_prompt_modules.py -v` 全过
- 单测数量约 7 个

---

## T8：tests/test_reminders.py

**文件：**
- 新建 `tests/test_reminders.py`

**依赖：** T5、T6

**步骤：**

1. 测试 build_plan_reminder(0) 返回 ""
2. 测试 build_plan_reminder(1) 返回 FULL（含 "Plan Mode" 三个字 + 详细规则）
3. 测试 build_plan_reminder(6) 返回 FULL
4. 测试 build_plan_reminder(11) 返回 FULL
5. 测试 build_plan_reminder(2/3/4/5) 返回 SHORT（≤ 50 字）
6. 测试 build_plan_reminder(7/8/9/10) 返回 SHORT
7. 测试 inject_into_user_text 拼接顺序：reminder + "\n\n" + user_text
8. 测试 inject_into_user_text("", "hi") 返回 "hi"

**验证：**
- `pytest tests/test_reminders.py -v` 全过
- 单测数量约 8 个

---

## T9：providers/events.py 增加 cache 字段

**文件：**
- 修改 `mewcode/providers/events.py`

**依赖：** 无

**步骤：**

1. Usage dataclass 增加：
   ```python
   cache_creation_input_tokens: int | None = None
   cache_read_input_tokens: int | None = None
   ```
2. 字段顺序：放在 thinking_tokens 之后（保持向后兼容）
3. docstring 补充说明：
   - 仅 Anthropic 协议解析；OpenAI 协议保持 None
   - None 表示"未知/不适用"，0 表示"明确无缓存"

**验证：**
- `python -c "from mewcode.providers import Usage; u = Usage(1,2); print(u.cache_creation_input_tokens, u.cache_read_input_tokens)"` 输出 `None None`
- `python -c "from mewcode.providers import Usage; u = Usage(1,2,cache_creation_input_tokens=10,cache_read_input_tokens=20); print(u)"` 不报错

---

## T10：AnthropicProvider 改造（system 列表形式 + cache 字段解析）

**文件：**
- 修改 `mewcode/providers/anthropic.py`

**依赖：** T9

**步骤：**

1. **system 字段升级为列表形式**（plan 2.5 节）：
   ```python
   if system:
       body["system"] = [
           {
               "type": "text",
               "text": system,
               "cache_control": {"type": "ephemeral"},
           }
       ]
   ```
2. **解析 cache 字段**：在 message_start 与 message_delta 的 usage 解析处
   提取：
   ```python
   cache_creation = usage.get("cache_creation_input_tokens")
   cache_read = usage.get("cache_read_input_tokens")
   ```
3. **Usage 事件构造时附带 cache 字段**：
   ```python
   yield Usage(
       input_tokens=input_tokens,
       output_tokens=output_tokens,
       thinking_tokens=thinking_tokens,
       cache_creation_input_tokens=cache_creation,
       cache_read_input_tokens=cache_read,
   )
   ```
4. 文件顶部 docstring 更新：增加 cache 字段映射说明

**验证：**
- `python -m py_compile mewcode/providers/anthropic.py`
- 已有单测全过（旧的 system 字符串语义升级为列表语义后，旧测试若有
  字符串断言需确认——预期没有）

---

## T11：3 个工具的 description 强化

**文件：**
- 修改 `mewcode/tools/read.py`
- 修改 `mewcode/tools/edit.py`
- 修改 `mewcode/tools/run.py`

**依赖：** 无

**步骤：**

1. **ReadTool.description** 末尾追加：
   `"使用 edit 之前必先用 read 确认原文片段，避免按错位置替换。"`
2. **EditTool.description** 末尾追加：
   `"调用前必须已经在本会话中 read 过此文件以确认原文。"`
3. **RunTool.description** 末尾追加：
   `"优先使用 read / glob / search 等专用工具读取信息，"
   `"而非通过 run 调用 cat / dir / grep 等命令。"`

**验证：**
- `python -c "from mewcode.tools.read import ReadTool; assert 'edit 之前必先用 read' in ReadTool.description"`
- 同样检查 EditTool 与 RunTool

---

## T12：ToolRegistry 新增 to_anthropic_format_with_cache

**文件：**
- 修改 `mewcode/tools/registry.py`

**依赖：** 无

**步骤：**

1. 新增方法：
   ```python
   def to_anthropic_format_with_cache(self) -> list[dict]:
       items = self.to_anthropic_format()
       if items:
           items[-1] = {
               **items[-1],
               "cache_control": {"type": "ephemeral"},
           }
       return items
   ```
2. docstring 说明：与 to_anthropic_format 行为相同，仅最后一项加
   cache_control（spec F4）

**验证：**
- `python -c "from mewcode.tools import ToolRegistry, register_builtins; r = ToolRegistry(); register_builtins(r); items = r.to_anthropic_format_with_cache(); print('cache_control' in items[-1]); print('cache_control' in items[0])"` 输出 `True False`

---

## T13：chat 层接入（session + engine 改造）

**文件：**
- 修改 `mewcode/chat/session.py`
- 修改 `mewcode/chat/engine.py`

**依赖：** T6、T9、T10、T12

**步骤：**

1. **session.py 增加字段**：
   ```python
   plan_turn_count: int = 0
   ```
   - clear() 中重置为 0
   - switch_provider() 中重置为 0
   - docstring 说明语义（spec F7）

2. **engine.py 修改 _consume_round**：
   - 在调 stream_chat 前构造临时 messages 副本（plan 2.3 节伪代码）
   - 仅 plan 模式拼接 reminder
   - do 模式重置 plan_turn_count
   - 用 messages_to_send 替代 session.messages 调 stream_chat
   - 不修改 session.messages（避免污染历史）

3. **engine.py 修改 _get_tools_format**：
   - anthropic 协议改用 to_anthropic_format_with_cache()
   - openai 协议保持原样

4. import 顺序与已有风格一致

**验证：**
- `pytest tests/ -q` 全套回归（112 + 新增 T7/T8 测试 ≈ 127）
- `python scripts/verify_round_loop.py` 端到端正常

---

## T14：tests/test_anthropic_cache.py + verify_cache_hit.py + 全量验收

**文件：**
- 新建 `tests/test_anthropic_cache.py`
- 新建 `scripts/verify_cache_hit.py`
- 产出 `docs/05/acceptance-report.md`

**依赖：** T9、T10、T12、T13

**步骤：**

1. **test_anthropic_cache.py 单测**：
   - 测试 AnthropicProvider 构造请求体含 system 列表形式 + cache_control
   - 测试 tools 字段最后一项含 cache_control（来自 registry 新方法）
   - 测试 system 为空时不加 cache_control
   - 测试 SSE 帧含 cache 字段时 Usage 正确传递
   - 用 stub stream_post 拦截请求体，断言其结构

2. **verify_cache_hit.py 真实 API 端到端**：
   - 启动一次进程
   - 发送相同 prompt 两次（如"统计 README.md 的行数"）
   - 解析两次 Usage：
     - 第一次：cache_creation_input_tokens > 0
     - 第二次：cache_read_input_tokens >= 第一次创建的 80%
   - 打印两次的 input/output/cache_creation/cache_read 对比
   - 通过则打印 "✓ 缓存策略生效"

3. **acceptance-report.md**：
   - 按 docs/05/checklist.md 逐项验证
   - 自动可验证项跑脚本
   - 列出待手工验证项

**验证：**
- `pytest tests/test_anthropic_cache.py -v` 全过
- `python scripts/verify_cache_hit.py` 真实 API 通过
- `pytest tests/ -q` 全套通过
- 第一/二/三阶段端到端脚本不退化

---

## 任务汇总

| #   | 任务                                       | 依赖             | 文件数 | 测试   |
|-----|--------------------------------------------|------------------|--------|--------|
| T1  | 删除旧 system_prompt.py + 建目录            | 无               | 1 删除 | -      |
| T2  | modules.py 7 固定模块                       | T1               | 1      | -      |
| T3  | env.py 环境信息                             | T1               | 1      | -      |
| T4  | builder.py 拼装入口                         | T2/T3            | 1      | -      |
| T5  | reminders.py 动态注入                       | T1               | 1      | -      |
| T6  | __init__.py 兼容旧 API                      | T2-T5            | 1      | -      |
| T7  | test_system_prompt_modules.py              | T2-T6            | 1      | ✅ 7   |
| T8  | test_reminders.py                          | T5/T6            | 1      | ✅ 8   |
| T9  | events.py Usage 增 cache 字段              | 无               | 1 修   | -      |
| T10 | AnthropicProvider system 列表 + cache 解析  | T9               | 1 修   | -      |
| T11 | 3 个工具 description 强化                   | 无               | 3 修   | -      |
| T12 | ToolRegistry 新增 with_cache 方法           | 无               | 1 修   | -      |
| T13 | chat session + engine 接入                  | T6/T9/T10/T12    | 2 修   | 跑回归 |
| T14 | test_anthropic_cache + verify_cache_hit + 验收 | T9/T10/T12/T13 | 2 + 1  | ✅ 5+ |

**单测累计**：约 20 个新增（7 + 8 + 5+）+ 112 个已有 = **132+**

---

## 自检结论

- ✅ **plan 覆盖**：plan.md 所有模块设计都有任务对应
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落
- ✅ **依赖链**：执行图有合法拓扑序，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（FIXED_MODULES /
  build_plan_reminder / inject_into_user_text /
  to_anthropic_format_with_cache / plan_turn_count 等）
- ✅ **不退化覆盖**：T13 跑全套回归，T14 端到端覆盖第三阶段脚本
- ✅ **API 兼容**：T1+T6 共同保证 `from mewcode.system_prompt import
  build_system_prompt` 仍可用
