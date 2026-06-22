# MewCode 第八阶段验收报告

> 按 `docs/09/checklist.md` 逐项验证。
> 验证日期：2026-06-17
> 环境：Windows + PowerShell + Anaconda Python 3.13.9

---

## 一、自动验证结果

### 编译与测试基础

- [x] **C1 项目可安装** — 继承前阶段
- [x] **C2 包可导入** — `import mewcode` → `0.1.0`
- [x] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **365 passed**
      （320 已有 + 45 第八阶段新增）
- [x] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q` 通过
- [x] **C5 命令行入口可调用** — `python -m mewcode` 继续可启动
- [x] **C6 mewcode.compaction 可导入** — `from mewcode.compaction import Compactor` 正常

### token 估算（spec F1）

- [x] **AC1 锚点估算** — `test_estimate_with_anchor` 通过：
      last_usage=1000，anchor=2，新增消息按字符/3 增量估算
- [x] **AC2 无锚点全字符** — `test_estimate_no_anchor_全字符` 通过
- [x] **锚点失效回退** — `test_estimate_anchor_失效回退` 通过
- [x] **serialize_message** — TextBlock / ToolUseBlock / ToolResultBlock 全覆盖

### 第一层轻量预防（spec F3/F4/F5/F6）

- [x] **AC3 单工具存盘** — `test_单工具_12KB_存盘` 通过：
      12KB ToolResultBlock → 写入 `.mewcode/transcripts/<session_id>/...` + content 替换为预览
- [x] **AC4 单消息排序存盘** — `test_单消息_总和_排序存盘` 通过：
      单工具未超过 10KB 但总和超过 25KB → 按 size 从大到小存盘直到 ≤ 25KB
- [x] **AC5 < 阈值不动** — `test_单工具_5KB_不动` 通过
- [x] **AC6 预览前 20 + 后 5** — `test_预览_前20后5` 通过
- [x] **AC7 ≤ 25 行不截** — `test_预览_短内容不截` 通过
- [x] **只处理最新 tool_results** — `test_仅最后一条tool_results被处理` 通过

### 第二层重量兜底（spec F8-F14）

- [x] **AC8 估算未达不触发** — `test_未达阈值不触发` 通过
- [x] **AC9 估算达阈值触发** — `test_达阈值触发并替换messages` 通过：
      stub provider 返回 summary 后，session.messages 替换为 boundary + recent
- [x] **AC10 prompt 含 5 段** — `test_summarize_async_含5段` 通过
- [x] **AC11 prompt 禁工具** — `test_summarize_async_禁工具` 通过：tools_format=None
- [x] **AC12 解析 <summary> 标签** — `test_extract_summary_正常` 通过
- [x] **AC13 解析失败** — 无标签 / 空标签 / 段不足三种失败均返回 None
- [x] **AC14 近期保留区计算** — `test_keep_boundary_扩展真实user边界` 通过
- [x] **AC15 至少 5 条** — `test_keep_boundary_至少5条` 通过
- [x] **AC16 边界 reminder** — `test_build_boundary_message_含system_reminder` 通过：
      含 `<system-reminder>` / `[Context Compacted]` / 时间戳 / 压缩消息数

### 熔断（spec F15）

- [x] **AC17 3 次失败 → disabled** — `test_3次失败_disabled` 通过
- [x] **AC18 disabled 跳过自动** — `test_disabled跳过自动` 通过
- [x] **AC19 reset_state 重置** — `test_reset_state` 通过；Session.clear / switch_provider 同步重置字段
- [x] **第一层不受熔断影响** — `test_第一层始终跑_即便disabled` 通过

### /compact 命令（spec F16）

- [x] **AC20 /compact 命令** — `test_compact_默认` 通过：调用 compactor.compact_now
- [x] **AC21 自定义指示** — `test_compact_带自定义指示` 通过
- [x] **AC22 失败不熔断** — `test_compact_失败不熔断` 通过
- [x] **未启用时提示** — `test_compact_未启用` 通过

### 集成

- [x] **session_id 生成** — main.py 启动时 `datetime.now().strftime("%Y%m%d_%H%M%S")`
- [x] **transcripts 目录** — `verify_compaction.py` 实测写入临时目录 `.mewcode/transcripts/verify_session/tool_32_big1.txt`
- [x] **after_response 更新锚点** — `test_after_response_更新锚点` + verify 脚本通过
- [x] **.gitignore** — 添加 `.mewcode/transcripts/`

### 端到端（verify_compaction.py）

- [x] `python scripts/verify_compaction.py` 通过：

```text
[1] 构造历史 + 超大工具结果...
[2] 第一层 + 第二层 before_request...
    存盘数量: 1
    压缩消息数: 20
[3] 验证 transcripts 文件存在...
    ...\.mewcode\transcripts\verify_session\tool_32_big1.txt
[4] 验证 messages 被替换为 boundary + recent...
    boundary message: ✓
[5] after_response 更新锚点...
    anchor updated: ✓

✓ 上下文压缩端到端通过
```

### 不退化（spec N5 / AC23）

- [x] **AC23a 已有单测全过** — 365 passed
- [x] **AC23b 旧端到端抽样不退化**：
  - `verify_t9.py` 通过
  - `verify_t18.py` 通过
  - `verify_t19.py` 通过
  - `verify_round_loop.py` 通过
  - `verify_mcp.py` 通过
  - `verify_instructions.py` 通过
- [x] **命令不退化** — 旧 `/permissions`、`/instructions` 测试仍全过

### 模块边界

- [x] **I1 单向依赖** — `mewcode/compaction/` 不依赖 chat/commands/render
- [x] **I2 中文优先** — 用户可见提示中文；摘要结构中文标题
- [x] **I3 不引入新依赖** — pyproject.toml dependencies 仍 4 项
- [x] **I4 .gitignore** — 含 `.mewcode/transcripts/`

---

## 二、关键技术成果

### 1. 两层上下文压缩链路

```text
run_turn 入口
  ↓
第一层 lightweight
  - 单工具结果 > 10KB → 存盘 + 预览
  - 单消息总和 > 25KB → 大结果依次存盘
  ↓
第二层 summarizer
  - token 估算逼近窗口 → LLM 摘要早期历史
  - 近期 10K token / 至少 5 条原文保留
  - 边界 user 消息提示模型不要脑补代码
  ↓
正常 stream_chat
```

### 2. 锚点式 token 估算

用上一次 API 的真实 `usage.input_tokens` 做锚点，只估算新增消息：

```python
estimated = last_usage_input_tokens + chars_after_anchor // 3
```

相比全字符估算更稳定，且不引入 tokenizer 依赖。

### 3. 工具结果存盘

工具结果是 token 大头，第八阶段优先处理：

```text
.mewcode/transcripts/<session_id>/tool_<msg_idx>_<tool_use_id>.txt
```

对话里只留预览和路径，模型如需完整细节会重新用 read 工具读取。

### 4. 摘要边界消息

压缩后第一条消息是：

```text
<system-reminder>
[Context Compacted]
上面是早期对话的摘要（从 N 条消息压缩而来）。
重要：完整文件内容请重新用 read 工具读取，不要从摘要中脑补具体代码。
...
</system-reminder>
```

这保持 system_prompt 稳定，不破坏 prompt cache。

### 5. 熔断机制

自动摘要连续失败 3 次后本会话停用自动压缩，避免死循环浪费 token。
`/clear` 和 `switch_provider` 会重置状态。

---

## 三、测试统计

```text
pytest tests/ -q
365 passed in 14.03s
```

第八阶段新增 45 个测试：

- test_compaction_tokens.py: 7
- test_compaction_lightweight.py: 8
- test_compaction_summarizer.py: 16
- test_compaction_compactor.py: 10
- test_compact_command.py: 4

---

## 四、待手工验证

- [ ] 真实长会话累计到接近模型窗口，观察自动触发摘要
- [ ] REPL 中手动 `/compact 重点保留架构决策`，观察提示与 messages 后续表现
- [ ] 大文件 read/search 后观察 `.mewcode/transcripts/<session_id>/` 文件生成

---

## 五、整体结论

**第八阶段自动验收通过**：

- 23 个 AC 全部有自动或端到端验证
- 365 单测全过
- `verify_compaction.py` 端到端通过
- 前七阶段功能零退化
- 不引入新依赖

MewCode 现在具备了长会话上下文管理能力：大工具结果自动存盘，累积历史接近窗口时自动摘要，用户也可以 `/compact` 手动压缩。至此，MewCode 从“能长时间干活”进一步升级为“能在有限 token 预算里持续干活”。

下一阶段建议：
1. `/mcp` 命令族（show/reload/disable/enable）
2. 审计日志（记录工具调用、权限、压缩事件）
3. 长期记忆（接通第四阶段 memory hook）
