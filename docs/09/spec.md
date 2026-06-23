# MewCode 第九阶段 Spec：会话恢复与长期记忆

## 背景

前八个阶段已经交付了 MewCode 的核心 Agent 能力：

- System Prompt 构建与环境感知
- 多轮 Agent Loop 与工具调用闭环
- Plan/Do 模式、权限系统、MCP、项目指令文件
- 上下文压缩与超大工具结果存盘

但当前体验仍有一个根本短板：**每次启动都像第一次见面**。

用户已经告诉过 Agent 的偏好、项目里的技术约定、之前做到哪一步、某个方案为什么被否决，这些信息如果只存在当前进程内，一旦重启或中断，Agent 就需要重新探索、重新询问、重新犯同样的错。

第九阶段的目标是让 MewCode 在新会话启动时自动恢复项目知识和用户偏好，让 Agent 从「每次失忆」变成「越用越懂你」。实现上分三层：

1. **项目指令文件增强**：支持更清晰的多层优先级与 `@include`。
2. **会话 JSONL 存档与恢复**：中断后能从最近会话继续。
3. **自动笔记与记忆索引**：把偏好、纠正、项目知识、参考资料沉淀为长期记忆。

本阶段不做向量数据库、RAG 检索、团队记忆同步。

## 目标

- 项目指令文件支持三层优先级，项目级高于用户级，高优先级内容排在前面。
- 项目指令文件支持 `@include <path>` 引用其他 Markdown 文件。
- `@include` 必须限制嵌套深度、用 visited 集合防环路、拦截跳出允许根目录的路径。
- 会话历史以 JSONL 追加写入项目会话目录；恢复时能跳过坏行。
- 不维护独立 meta 文件；会话 ID、标题、消息数、更新时间等需要时直接扫描 JSONL 计算。
- 会话恢复能处理坏行、孤儿工具调用、token 超限、长时间间隔提醒和 30 天过期清理。
- 自动笔记分四类：用户偏好、纠正反馈、项目知识、参考资料。
- 每轮 Agent Loop 自然停下后（模型最终回复无工具调用时）异步调用 LLM 更新笔记。
- 用户级记忆与项目级记忆分开存储。
- 处理请求前注入记忆索引，让 Agent 像已经读过长期记忆一样工作。
- 记忆索引文件控制在 200 行 / 25KB 以内，约 2-3K tokens。
- 第一至第八阶段功能不退化。

## 功能需求

### F1. 项目指令三层加载与优先级

项目指令文件仍使用 Markdown，加载三层：

| 优先级 | 层级 | 路径 | 说明 |
|---|---|---|---|
| 1 最高 | 项目本地级 | `<cwd>/.mewcode/AGENTS.local.md` 或 `CLAUDE.local.md` | 本机/当前项目私有规则 |
| 2 | 项目共享级 | `<cwd>/AGENTS.md` 或 `CLAUDE.md` 或 `.mewcoderc` | 项目团队共享规则 |
| 3 最低 | 用户全局级 | `~/.mewcode/AGENTS.md` 或 `CLAUDE.md` 或 `.mewcoderc` | 用户全局偏好 |

合并规则：

- 高优先级排在前面：项目本地级 → 项目共享级 → 用户全局级。
- 仍是拼接，不是覆盖；如果存在冲突，System Prompt 明确提示模型优先遵循靠前内容。
- 某层缺失则跳过，不输出空标题。
- 三层全空时返回 `None`，不注入自定义指令段。

### F2. 项目指令 `@include` 引用

指令文件支持行级 include：

```markdown
@include docs/ai-rules.md
@include .mewcode/local-extra.md
```

解析规则：

- 只识别独占一行的 `@include <path>`。
- `<path>` 相对当前指令文件所在目录解析。
- include 的文件内容在原位置展开，并用来源注释包裹：

```markdown
<!-- begin include: docs/ai-rules.md -->
...
<!-- end include: docs/ai-rules.md -->
```

安全规则：

- 最大嵌套深度为 3。
- 使用 resolved absolute path 的 visited 集合防止环路。
- 项目级文件的 include 只能指向 `<cwd>` 内部。
- 用户全局级文件的 include 只能指向 `~/.mewcode` 内部。
- include 指向不存在、目录、非 UTF-8 或越界路径时 warning 后跳过，不阻塞启动。
- include 文件同样受单文件 8KB 限制。

### F3. 会话 ID 与存储目录

会话存档放在项目目录：

```text
<cwd>/.mewcode/sessions/<session_id>.jsonl
```

会话 ID 格式：

```text
YYYYMMDD-HHMMSS-xxxx
```

其中：

- 时间戳精确到秒。
- `xxxx` 是 4 位随机十六进制或 base36 字符串，用于防止同秒撞车。
- 新启动如果没有可恢复会话，则生成新 ID。
- 恢复已有会话时保留原 ID，继续向同一个 JSONL 文件追加。

### F4. JSONL 追加写会话消息

每条会话消息追加为一行 JSON：

```json
{"type":"message","ts":"2026-06-23T10:15:30+08:00","role":"user","content":[...]}
```

要求：

- 每次 `Session.append_user_text` / `append_assistant` / `append_tool_results` 成功后追加一行。
- 使用 append 模式写入，写完 flush；不要求每行 fsync。
- 崩溃最多丢最后一行或产生最后一行坏行。
- 只记录消息本身，不维护额外 meta 文件。
- Message.content 使用现有 ContentBlock 结构可逆序列化。

### F5. 不维护 meta 文件，扫描 JSONL 计算摘要

需要展示或选择会话时，通过扫描 JSONL 计算：

- `session_id`：文件名去掉 `.jsonl`。
- `title`：第一条真实 user 文本的前 40 个字符；没有则使用「未命名会话」。
- `message_count`：成功解析并保留的 message 行数。
- `created_at`：第一条有效 message 的 ts；没有则文件 mtime。
- `updated_at`：最后一条有效 message 的 ts；没有则文件 mtime。

禁止新增 `sessions/meta.json`、`index.json` 等需要同步维护的状态文件。

### F6. 启动自动恢复最近有效会话

启动时执行：

1. 清理超过 30 天的过期会话文件。
2. 扫描 `<cwd>/.mewcode/sessions/*.jsonl`。
3. 选择最近更新且未过期的会话。
4. 尝试恢复其 messages。
5. 如果没有可恢复会话或恢复后为空，则创建新会话。

恢复成功时打印横幅：

```text
💾 已恢复会话: 20260623-101530-a3f9（23 条消息，标题：修复 MCP 超时）
```

新会话时不打印恢复横幅或打印简短提示均可，但不得制造噪音。

### F7. 恢复时跳过坏行

读取 JSONL 时：

- 单行 JSON 解析失败 → 跳过该行并计数。
- 行内缺少必要字段或 ContentBlock 无法反序列化 → 跳过该行并计数。
- 坏行不得导致整个会话恢复失败。
- 恢复结束后如有坏行，打印 warning：

```text
⚠️ 会话恢复跳过 2 行损坏记录
```

### F8. 恢复时处理孤儿工具调用

恢复完成后必须保证消息历史满足工具调用配对约束：

- assistant 消息中如果包含 ToolUseBlock，后续必须存在紧跟的 user tool_results 消息，且 tool_use_id 全部覆盖。
- 如果结尾出现 assistant tool_use 但没有匹配 tool_results，截断到该 assistant 之前。
- 如果 tool_results 缺少对应 assistant tool_use，截断到该 tool_results 之前。
- 截断时打印 warning：

```text
⚠️ 会话恢复检测到未配对工具调用，已截断到上一条完整消息
```

### F9. 恢复后 token 超限先压一次

恢复会话后、第一次发送请求前：

- 使用第八阶段 `Compactor` 的估算逻辑计算消息 token。
- 如果估算超过自动压缩阈值，则先触发一次 compaction。
- 压缩成功后再进入正常对话。
- 压缩失败时不阻塞启动，但应打印 warning，并允许用户 `/clear` 或继续尝试。

### F10. 长时间间隔提醒

如果恢复的会话距离最后一条消息已经超过 24 小时，则在 messages 尾部追加一条 user `TextBlock` 系统提醒：

```text
<system-reminder>
距离上次会话已过去 3 天 4 小时。请先根据已恢复的上下文继续，不确定的信息应重新读取文件确认。
</system-reminder>
```

要求：

- 该提醒也写入 JSONL，避免同一次恢复重复插入。
- 如果最后一条消息已经是同类时间跨度提醒，则不重复插入。

### F11. 30 天以上过期会话清理

启动时清理：

- `updated_at` 距当前时间超过 30 天的 `.jsonl` 文件。
- 清理只删除 JSONL 会话文件，不删除 transcripts 或 memory。
- 删除失败 warning 后继续启动。

### F12. 自动笔记分类

自动笔记分四类：

| category | 中文名 | 例子 | 默认存储范围 |
|---|---|---|---|
| `preference` | 用户偏好 | 用户喜欢中文回答、偏好先给结论 | 用户级 |
| `correction` | 纠正反馈 | 用户指出某做法错误、命名习惯不对 | 用户级 |
| `project_knowledge` | 项目知识 | 架构约定、模块职责、测试命令 | 项目级 |
| `reference` | 参考资料 | 用户给的链接、文档路径、外部 API 说明 | 项目级，除非明显是用户全局资料 |

### F13. 自动笔记文件格式

用户级笔记目录：

```text
~/.mewcode/memory/notes/*.md
~/.mewcode/memory/index.md
```

项目级笔记目录：

```text
<cwd>/.mewcode/memory/notes/*.md
<cwd>/.mewcode/memory/index.md
```

每条笔记一个带 frontmatter 的 Markdown：

```markdown
---
id: mem_20260623_101530_a3f9
scope: project
category: project_knowledge
created_at: 2026-06-23T10:15:30+08:00
updated_at: 2026-06-23T10:15:30+08:00
source_session: 20260623-101530-a3f9
tags: [testing, mcp]
---

项目的 MCP 验证脚本是 `python scripts/verify_mcp.py`，通过后才能认为 MCP 集成不退化。
```

### F14. 自动笔记更新时机

每轮 Agent Loop 自然停下后触发异步更新：

- 条件：模型最终回复没有 tool_use，即 `Stopped(reason="natural")`。
- 不在工具调用中间更新，避免记录半成品。
- 使用 `asyncio.create_task(...)` 后台执行，不阻塞下一次用户输入。
- 只给 LLM 最近一轮关键上下文、现有 memory index、必要的 session 摘要，不把全量历史塞给记忆更新。
- 失败只 warning，不影响主对话。

### F15. 自动笔记去重交给 LLM 判断

更新记忆时让 LLM 输出操作建议：

- `create`：新增笔记。
- `update`：更新已有笔记。
- `delete`：删除明显错误或被用户否定的笔记。
- `noop`：无需更新。

去重策略交给 LLM：

- 如果新事实与已有笔记等价，应 update 或 noop，而不是 create。
- 如果用户纠正了旧记忆，应 update 原笔记并保留 updated_at。
- 程序只负责校验输出格式、路径安全和文件写入。

### F16. 记忆索引构建与限制

`index.md` 是注入上下文的唯一长期记忆入口。

要求：

- 每次笔记变更后重建对应 scope 的 index。
- index 控制在 200 行以内。
- index 控制在 25KB 以内。
- 超限时按优先级保留：纠正反馈 > 用户偏好 > 项目知识 > 参考资料；同类按 updated_at 倒序。
- index 内容应简洁，按分类组织，包含笔记 ID 便于后续 update。

### F17. 请求前注入记忆索引

每次处理用户请求前读取：

1. 用户级 `~/.mewcode/memory/index.md`
2. 项目级 `<cwd>/.mewcode/memory/index.md`

拼接后注入上下文：

```markdown
## 长期记忆
以下是已记录的用户偏好和项目知识。项目级记忆优先于用户级记忆；如与当前用户明确指示冲突，以当前用户指示为准。

### 项目记忆
...

### 用户记忆
...
```

实现上可复用 `build_system_prompt(..., memory=...)` 的预留参数。为减少 prompt cache 破坏，只有 memory hash 变化时才重建 `session.system_prompt`。

### F18. 用户级与项目级记忆分开存

- 用户偏好和纠正反馈默认写用户级。
- 项目知识默认写项目级。
- 参考资料默认写项目级。
- 如果 LLM 输出的 scope 与 category 明显冲突，程序可按默认规则纠正。
- 项目级文件不得写到 `<cwd>` 之外；用户级文件不得写到 `~/.mewcode` 之外。

### F19. 不做的事

本阶段明确不做：

- 向量数据库。
- RAG 检索。
- embedding。
- 团队记忆同步。
- 云端记忆同步。
- 记忆 Web UI。
- 复杂权限审批流。
- 自动总结所有历史会话生成知识库。
- 对 transcripts 做生命周期清理。
- 精确 tokenizer。

## 非功能需求

### N1. 模块边界

新增模块建议：

- `mewcode/sessions/`
  - `archive.py`：JSONL 追加写、扫描、恢复、清理。
  - `codec.py`：Message/ContentBlock 序列化与反序列化。
- `mewcode/memory/`
  - `notes.py`：Note 数据结构、frontmatter 读写。
  - `index.py`：index.md 重建与限制。
  - `manager.py`：请求前注入、后台更新调度。
  - `updater.py`：LLM 更新记忆的 prompt 与输出解析。
- `mewcode/instructions/loader.py` 扩展 include 与新优先级。

### N2. 不引入新依赖

继续只使用现有依赖。JSONL、frontmatter、路径处理、时间处理均用 Python stdlib 实现。

### N3. 中文优先

用户可见 warning、横幅、命令说明、记忆更新提示均使用中文。

### N4. 崩溃安全

- JSONL 追加写保证崩溃最多影响最后一行。
- 恢复时坏行跳过。
- 记忆笔记写入使用临时文件 + replace，避免半写文件破坏笔记。

### N5. Windows 兼容

- 路径使用 `pathlib.Path`。
- 文件统一 UTF-8。
- 会话 ID 不使用 Windows 非法字符。
- JSONL 与 Markdown 在 Windows PowerShell / CMD 下可读写。

### N6. 性能

- 启动扫描 30 天内 JSONL 文件，默认项目规模下 < 100ms。
- index.md 控制在 25KB 内，请求前读取开销很小。
- 自动笔记更新后台执行，不阻塞 Agent Loop。

### N7. 不退化

- 没有 sessions / memory 文件时，启动行为接近第八阶段。
- compaction、instructions、permissions、MCP、tools 接口不破坏。
- 短会话不触发恢复压缩时不增加明显延迟。

## 验收标准

### AC1. 指令优先级

项目本地级、项目共享级、用户全局级都有内容时，拼接顺序为项目本地 → 项目共享 → 用户全局。

### AC2. include 展开

项目指令中 `@include docs/rules.md` 能展开文件内容，并带 begin/end include 标记。

### AC3. include 防环

A include B、B include A 时不会死循环，warning 后跳过重复路径。

### AC4. include 深度限制

嵌套深度超过 3 时跳过更深层 include 并 warning。

### AC5. include 越界拦截

项目指令 include `../outside.md` 被拒绝，不读取项目目录外文件。

### AC6. 会话 JSONL 追加

append user / assistant / tool_results 后，对应 JSONL 文件新增 message 行。

### AC7. 坏行跳过

JSONL 中插入非法 JSON 行，恢复时跳过坏行且保留其他有效消息。

### AC8. 孤儿工具调用截断

JSONL 结尾存在 assistant tool_use 但没有 tool_result，恢复后截断到该 assistant 之前。

### AC9. 不维护 meta 文件

会话标题、消息数、更新时间通过扫描 JSONL 得到；目录中不出现 meta/index 状态文件。

### AC10. 自动恢复最近会话

多个会话文件存在时，启动恢复 updated_at 最新且未过期的会话。

### AC11. 过期清理

updated_at 超过 30 天的 JSONL 文件在启动时被清理。

### AC12. 长间隔提醒

恢复超过 24 小时未更新的会话时，尾部插入一次 system-reminder；重复恢复不重复插入。

### AC13. 恢复后超限压缩

恢复消息估算超过自动压缩阈值时，第一次请求前调用 compactor 压缩一次。

### AC14. 自动笔记触发

Agent Loop 自然停下且最终回复无工具调用时，调度后台记忆更新任务。

### AC15. 非自然停止不更新笔记

用户取消、Provider 错误、max_iterations、仍有工具调用时，不触发自动笔记更新。

### AC16. 笔记 frontmatter

新增笔记文件包含 id、scope、category、created_at、updated_at、source_session、tags。

### AC17. 用户级/项目级分开存

preference/correction 写到用户级 memory；project_knowledge/reference 写到项目级 memory。

### AC18. index 限制

重建 index 后行数 ≤ 200，大小 ≤ 25KB。

### AC19. 请求前注入记忆

存在用户级和项目级 index.md 时，下一次请求的 system_prompt 含 `## 长期记忆` 与两类索引内容。

### AC20. memory hash 不变不重建 system_prompt

index 内容不变时，连续请求不重复重建 system_prompt。

### AC21. 不退化

现有单测和 verify 脚本全过；无 sessions / memory / include 时行为与第八阶段一致。

## 依赖与约束

- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台
- 继承前八阶段所有接口契约
