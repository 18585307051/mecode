# MewCode 第八阶段 Checklist

> 验证环境：Windows + PowerShell + Anaconda Python 3.13.9。
> 启动命令 `python -m mewcode`。全部通过后第八阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装** — 继承前阶段
- [ ] **C2 包可导入** — `import mewcode` → `0.1.0`
- [ ] **C3 单元测试** — `pytest tests/ -q` 输出 ~342 passed
- [ ] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q`
- [ ] **C5 命令行入口可调用** — `python -m mewcode`
- [ ] **C6 mewcode.compaction 可导入** —
      `python -c "from mewcode.compaction import Compactor; print('ok')"`

## token 估算（spec F1）

- [ ] **AC1 锚点估算** — last_usage=1000 + 锚点之后字符增量 / 3
- [ ] **AC2 无锚点全字符** — last_usage=0 → 全 messages 字符 / 3
- [ ] **锚点失效回退** — anchor > len(messages) → 全字符估算

## 第一层预防（spec F3 / F4 / F5 / F6）

- [ ] **AC3 单工具存盘** — 12KB ToolResultBlock → 写文件 + 替换预览
- [ ] **AC4 单消息排序存盘** — 多个 ToolResultBlock 总和 > 25KB → 按
      size 排序依次存盘到 ≤ 25KB
- [ ] **AC5 < 阈值不动** — 单工具 5KB → content 不变
- [ ] **AC6 预览前 20 + 后 5** — 30 行内容 → 截取
- [ ] **AC7 ≤ 25 行不截** — 20 行内容 → 完整保留

## 第二层兜底（spec F8 / F9 / F10 / F11 / F12 / F13 / F14）

- [ ] **AC8 估算未达不触发** — estimated < auto_threshold → 跳过
- [ ] **AC9 估算达阈值触发** — stub provider → summary 生成 → 替换
      messages
- [ ] **AC10 prompt 含 5 段** — system_prompt 含 5 段中文小标题
- [ ] **AC11 prompt 禁工具** — stream_chat 调用时 tools_format=None
- [ ] **AC12 解析 <summary> 标签** — extract_summary 提取 body
- [ ] **AC13 解析失败** — 无标签 / 标签空 / 段不足 → None
- [ ] **AC14 近期保留区计算** — 20 条 30K → keep_start 在 10K 边界
- [ ] **AC15 至少 5 条** — 短历史也保留 5 条
- [ ] **AC16 边界 reminder** — 第 0 条 message 含 `<system-reminder>` +
      `[Context Compacted]` + 时间戳

## 熔断（spec F15）

- [ ] **AC17 3 次失败 → disabled** — failures=3 → disabled=True
- [ ] **AC18 disabled 跳过** — 跳过自动压缩
- [ ] **AC19 /clear 重置** — disabled=False, failures=0

## /compact 命令（spec F16）

- [ ] **AC20 /compact 命令** — 调 compactor.compact_now
- [ ] **AC21 自定义指示** — `/compact 重点保留架构` → 拼接到 prompt
- [ ] **AC22 失败不熔断** — /compact 失败 → failures 不增加

## 不退化（spec N5 / AC23）

- [ ] **AC23a 已有单测** — 320 已有 + ~22 新增 = ~342 全过
- [ ] **AC23b 已有端到端**：
  - verify_t9 / verify_t18 / verify_t19
  - verify_round_loop / verify_agent_loop / verify_plan_mode
  - verify_cache_hit / verify_permissions / verify_mcp / verify_instructions

## 集成

- [ ] **session_id 生成** — 启动后 session.session_id 是时间戳
- [ ] **transcripts 目录** — 存盘后 `.mewcode/transcripts/<session_id>/` 存在
- [ ] **after_response 更新锚点** — Usage 含 input_tokens > 0 时更新

## 模块边界

- [ ] **I1 单向依赖** — `mewcode/compaction/` 不依赖 chat/commands/render
- [ ] **I2 中文优先** — 用户提示中文
- [ ] **I3 不引入新依赖** — pyproject.toml dependencies 仍 4 项
- [ ] **I4 .gitignore** — 含 `.mewcode/transcripts/`

## Windows 兼容

- [ ] Windows 控制台中文 / emoji 正常
- [ ] 文件路径用 pathlib，session_id 时间戳跨平台

## 端到端

- [ ] **verify_compaction.py 通过** — 第一层 + 第二层 stub 端到端

## 待手工验证

- [ ] 跑长会话（强制累计 ~120K tokens 的对话）观察自动触发
- [ ] /compact 手动触发并附自定义指示

## 自动验证小计

约 30 项可自动验证。

## 失败处理

任一项失败 → 定位 T 任务 → 修复 → 重跑 → 更新 acceptance-report.md。
