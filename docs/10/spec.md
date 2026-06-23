# MewCode 第十阶段 Spec：斜杠命令系统

## 背景

前九阶段交付了 MewCode 的核心 Agent 能力：System Prompt 构建、多轮 Agent Loop、工具系统、Plan/Do 模式、权限、MCP、项目指令、上下文压缩、会话恢复与长期记忆。

命令系统在前几个阶段以 `mewcode/commands/registry.py` + `builtin.py` 的最小形态零散成长起来：12 条命令、纯字符串 `name + aliases + description + handler`、撞名静默覆盖、没有类型分组、没有用法说明、没有 Tab 补全、没有面向用户的统一仪表盘。

随着子系统增多，体验上暴露出三个问题：

1. **常用操作必须经过 LLM**——清屏、看会话、看权限、看记忆这种确定性查询，应该走本地命令秒回。
2. **`/help` 没有结构**——所有命令一锅端列出，看不出"哪些是查询""哪些会改状态""哪些会发起对话"。
3. **命令之间的入口分散**——查供应商用 `/providers`、查权限用 `/permissions show`、查记忆用 `/memory show`、查会话条数没地方查；用户记不住。

本阶段把命令系统重做一遍，让 MewCode 拥有一套结构化、可扩展、好用的斜杠命令体系。

本阶段不做：用户自定义命令、动态生成提示词、命令级权限控制、子命令 Tab 补全、空输入弹菜单、`/session delete`、`/memory delete`——这些留到 Skill 系统阶段或独立任务。

## 目标

- 一套命令注册中心管理元数据（名称、别名、描述、用法、类型、参数提示、是否隐藏、处理函数），启动阶段就检测别名冲突，撞名直接 panic 退出。
- 命令名解析支持斜杠前缀、第一个空格切分、命令名大小写不敏感、空输入早返回、未命中带 `/help` 引导。
- 命令按执行模式分三类：纯本地（LOCAL）、影响界面/会话状态（STATEFUL）、把预设提示词送进对话交给 AI（PROMPT）。
- 抽一层"界面控制接口"语义化方法，让命令实现不绑定具体渲染框架。
- 在用户回车的入口加分流器：是命令走本地分发，不是命令才送给 AI。
- 支持别名和 Tab 补全：单匹配直接补、多匹配弹菜单、隐藏命令不参与。
- 内置十条用户高频命令显式可见：`/help`、`/compact`、`/clear`、`/plan`、`/do`、`/session`、`/memory`、`/permission`、`/status`、`/review`，外加 `/exit` `/quit`。
- 老命令 `/think` `/provider` `/providers` `/instructions` `/permissions` 全部保留功能但设为隐藏，不在 `/help` 中露出。
- 第一至第九阶段功能不退化。

## 功能需求

### F1. 命令元数据扩展

`Command` dataclass 在第七阶段已有的 `name + aliases + description + handler` 之外，新增字段：

| 字段 | 类型 | 含义 | 默认值 |
|---|---|---|---|
| `usage` | `str` | 一行用法示例，例如 `"/session resume <id>"` | `""` |
| `type` | `CommandType` | 命令执行模式，三选一 | 必填 |
| `arg_hint` | `str` | 提示用户参数形式，用于未来菜单/补全 | `""` |
| `hidden` | `bool` | 是否在 `/help` 列表中隐藏 | `False` |

`CommandType` 是字符串枚举（用 `str` 常量类即可，不引入 `enum.StrEnum`）：

- `LOCAL` —— 纯本地命令；handler 跑完就结束，**不修改 session 历史，也不向 LLM 发请求**。
- `STATEFUL` —— 影响运行时状态的命令；handler 修改 session/policy/provider/mode 等，**仍不向 LLM 发请求**。
- `PROMPT` —— 提示词命令；handler **构造一段文本作为用户输入注入 `run_turn`**，走正常对话链路。

### F2. 严格的别名冲突检测

`register(cmd)` 注册时立即校验，违规时抛 `CommandRegistrationError`（导致 `register_builtins()` 失败，进而 `main()` 退出码 ≠ 0）。

撞名规则：

- 同一 `name` 已存在 → 报错。
- 任一 `alias` 已被其它命令的 `name` 或 `alias` 占用 → 报错。
- 一条命令的 `aliases` 元组内含其自身 `name` → 报错（自反检查）。
- 测试中如需重新注册，必须先 `COMMANDS.clear()`，没有"静默覆盖"的兜底。

错误消息形如：`命令注册冲突：'permissions' 已被 '/permission' 占用`。

### F3. 命令名大小写不敏感

`dispatch` 解析后把 `name.lower()` 作为查表 key；`COMMANDS` 注册时也按小写存储。未来若要支持大小写敏感，再回退此规则。

### F4. 空输入与未知命令

- 输入空白、纯 `/` → `dispatch` 返回 `CommandResult()`，不打印报错；空白由 REPL 上层早返回，纯 `/` 视作未知命令。
- 输入 `/foobar` 未注册 → 调 `renderer.print_unknown_command("foobar", available_visible)`，提示"未知命令: /foobar"和"可用命令"列表，列表只列**可见**命令（`hidden=False`）。
- 未知命令分支也要把 `/help` 写在引导里。

### F5. 三类命令的执行边界

- `LOCAL` handler 内**禁止**调用 `session.append_*`、`session.clear`、`session.switch_provider`、`run_turn`。
- `STATEFUL` handler 可以修改 session 状态、policy、provider、mode、注入 system_prompt 重建。
- `PROMPT` handler 必须返回 `CommandResult(prompt_text=...)`，REPL 上层捕获该字段后把文本作为用户消息送入 `run_turn`。

`CommandResult` 因此新增字段：

| 字段 | 类型 | 含义 | 默认值 |
|---|---|---|---|
| `should_exit` | `bool` | 已有 | `False` |
| `prompt_text` | `str \| None` | PROMPT 类命令构造好的"假装用户输入"文本 | `None` |

REPL 处理顺序：先看 `should_exit` 再看 `prompt_text`；两者互斥。

### F6. `/help` 按类型分组

`/help` 输出三段，按 `LOCAL → STATEFUL → PROMPT` 顺序排列，标题为：

```
查询命令（不影响对话状态）：
  /help                列出所有可用命令
  /status              查看当前会话和子系统状态
  ...

操作命令（修改状态）：
  /clear               清空当前会话历史
  /plan                进入 Plan Mode（只读工具）
  ...

对话命令（向 AI 发起请求）：
  /review [侧重点]      让 AI 审查本轮的所有改动
```

`hidden=True` 的命令不出现在 `/help` 中，但仍可正常调用、未知命令引导列表也仅列可见命令。

### F7. Tab 补全

REPL 接入 prompt_toolkit `Completer`：

- 仅当当前行以 `/` 开头、且光标位于第一个空格之前（即正在写命令名）才触发。
- 候选集 = 所有 `hidden=False` 的命令的 `name` 列表（**别名不参与补全**，避免列表噪音）。
- 单匹配：直接补全到完整命令名。
- 多匹配：弹下拉菜单，由 prompt_toolkit 默认 UI 渲染。
- 空输入按 Tab：不响应（让 prompt_toolkit 默认行为接管，等同于无候选）。
- 已经写到子命令位置（如 `/session re`）：不补全；本阶段不做子命令补全。

### F8. `/status` 仪表盘

`/status` 是 LOCAL 类命令，输出当前 6 个子系统的状态摘要，每节 1-3 行，整屏 ≤ 15 行：

1. **供应商**：`name / protocol / model`
2. **模式**：`[DEFAULT]` 或 `[PLAN]`，thinking on/off
3. **会话**：session id、消息条数、估算 tokens、距自动压缩阈值的差额（复用 `compactor.estimate_messages` 与配置阈值）
4. **权限**：当前 mode、规则总数（allow N + deny M），不展开规则细节
5. **记忆**：user notes 数量、project notes 数量、index.md 字节数与行数（user + project 合计）
6. **项目指令**：已加载层数、合计字节数（复用 `loader.loaded_layers()`）

某子系统未启用时（如 `compactor=None`），打印"未启用"并继续。

### F9. `/session` 命令族

LOCAL 子命令：

- `/session` 或 `/session list` —— 扫描 `<cwd>/.mewcode/sessions/*.jsonl`，列出最近 N=10 条会话：`session_id  消息数  最近更新  标题`，按 `updated_at` 倒序，当前会话标 `*`。
- `/session current` —— 打印当前会话 id、消息数、估算 tokens、起始时间、最近更新时间。

STATEFUL 子命令：

- `/session new` —— 立刻 rotate 到一个新会话，等同 `/clear` 但显式新建：清空内存历史 + 通过 `archive.rotate(session)` 换发新 session_id。
- `/session resume <id>` —— 切到指定历史会话：先在 `<cwd>/.mewcode/sessions/` 中精确或前缀匹配 `id`，命中后清空内存历史、调 `archive.load_by_id(id)` 复用第九阶段恢复管线（坏行跳过、孤儿截断、长间隔提醒），完成后切换 `session.archive.session_id` 并重建 system_prompt（如有 memory 注入）。`id` 缺失或多匹配时给出错误提示，不切换。

### F10. `/memory` 命令族

LOCAL 子命令：

- `/memory` 或 `/memory show` —— 打印 user 级 + project 级 `index.md` 拼接后的最终注入文本（与请求时实际用的一致，复用 `memory_manager.get_combined_index_text()`）。
- `/memory list [user|project]` —— 列出 notes 目录下所有笔记：`id  scope  category  updated_at  标题首行`，默认列两层全部，加 `user` / `project` 参数过滤。

STATEFUL 子命令：

- `/memory refresh` —— 强制重读 notes 目录、重建两层 index.md、按 hash 决定是否重建 system_prompt（复用 `memory_manager.refresh()`），打印重建结果。

### F11. `/permission` 主名 + 旧名作为别名

把现有 `/permissions` 命令重命名：`name="permission"`、`aliases=("permissions",)`。子命令族保持原样（show/allow/deny/mode/reload/init），不做语义改动。

### F12. `/review` 提示词命令

PROMPT 类命令，组装一段预设的"自检"prompt 作为用户输入送入 `run_turn`。

预设 prompt：

```
请回顾本轮对话里你做的所有改动和操作，逐项检查：
1. 修改是否完成了用户要求的目标？有没有偏题？
2. 改动是否引入了潜在 bug、边界情况遗漏、错误处理缺失？
3. 是否有应该写但没写的测试？
4. 代码风格、命名、注释是否与项目既有风格一致？
5. 是否破坏了现有功能、接口契约或测试？

按上面五点逐条给出结论；如果某点没问题，明确说"无"。最后用一句话总结整体风险等级（低 / 中 / 高）和建议的下一步。
```

参数行为：

- 无参数 → 直接发预设 prompt。
- 有参数（如 `/review 重点看 SQL 注入`）→ 在预设 prompt 末尾追加一段：

  ```

  本次额外重点关注：<用户参数原文>
  ```

校验：当前 session.messages 为空时，打印"当前会话尚无内容可回顾。先发起一些对话再用 /review。"，**不**调 `run_turn`，返回 `CommandResult()`（`prompt_text=None`）。

### F13. 老命令降级为 hidden

以下命令保留全部功能，仅设 `hidden=True`，不在 `/help` 列出：

- `/think on|off`
- `/provider <name>`
- `/providers`
- `/instructions [show|reload]`

`/exit` 和 `/quit` 仍 `hidden=False`，列在 STATEFUL 段中。

### F14. PLAN 模式 prompt 前缀

REPL 在每行读取输入之前，根据当前 `session.mode` 动态构造 PROMPT 字符串：

- 当 `session.mode == "plan"` 时，PROMPT 改为 `[PLAN] > `（中间一个空格，提示前缀）。
- 其它任何模式（包括 `default`）保持原 `> `，**不**显示前缀。
- 实现上把 `PROMPT = "> "` 字符串常量替换为函数 `_make_prompt(session) -> str`，在 `pt_session.prompt_async(...)` 调用前每次重新求值。
- 仅 `mode` 一项参与前缀；thinking on/off、permission yolo/strict 等状态不在 prompt 上显示，避免视觉噪音——这些状态用户可随时 `/status` 查看。

设计动机：PLAN 模式下只读工具被锁，若用户忘记自己在 PLAN 模式直接发起改文件请求会很挫败；用持久可见的前缀解决这个唯一高风险场景，其它低风险状态走 `/status`。

### F15. 不做的事

- 用户自定义命令注册（运行时 `/cmd add` 之类）。
- 动态生成提示词模板。
- 命令级权限控制（`/clear` 是否需要确认、`/plan` 是否需要权限）。
- 子命令 Tab 补全（`/session re<TAB>`）。
- 空输入按 Tab 弹完整菜单。
- `/session delete <id>` 和 `/memory delete <id>`。
- 历史回放、命令搜索、命令历史。
- 完整状态栏（终端底部常驻多字段标记）——本阶段仅做 F14 的 PLAN 模式前缀，其它状态走 `/status`。

## 非功能需求

### N1. 模块边界

- 命令注册与分发：`mewcode/commands/registry.py`（扩展）。
- 内置命令实现：`mewcode/commands/builtin.py`（扩展）+ 视代码量拆分新文件 `mewcode/commands/views.py`（`/status` `/session list` 等只读视图聚合，可选）。
- Tab 补全：`mewcode/repl/completer.py`（新增）。
- Renderer 新增方法：`mewcode/render/renderer.py` 增加若干语义化方法（status 仪表盘、session 列表、memory 列表、命令分组帮助等）。

### N2. 不引入新依赖

继续只用现有 `prompt_toolkit + rich + pyyaml`。Tab 补全用 prompt_toolkit 自带 `Completer` 接口。

### N3. 中文优先

所有用户可见提示、`/help` 标题、`/status` 各节标签均使用中文。

### N4. 启动期 panic 不带堆栈污染

撞名 panic 时通过 `main()` 的 `try/except` 捕获 `CommandRegistrationError`，打印红字单行错误后退出码 1。**不打印整段堆栈**——这是配置/编码错误，用户不需要 traceback。

### N5. Windows 兼容

prompt_toolkit Completer 在 Windows cmd / PowerShell 5 / Windows Terminal 三种终端下行为一致。下拉菜单显示乱码时降级为单行提示也可接受。

### N6. 性能

- 注册表是 dict 查找，O(1)；启动期校验 O(总命令数²) 在常量量级。
- `/status` 单次输出 ≤ 15 行，所有数据来自已加载的运行时对象，零 IO。
- `/session list` 复用第九阶段 `archive.scan_summaries()`，已经在启动时验证 < 100ms。

### N7. 不退化

- 现有 12 条命令的功能、触发方式、行为完全保留。
- 现有 `tests/test_command_dispatch.py` 全部通过（必要时按新接口微调断言）。
- `verify_*.py` 全部通过。

## 验收标准

### AC1. 命令元数据扩展

`Command` dataclass 包含 `name / aliases / description / handler / usage / type / arg_hint / hidden` 八个字段；`CommandType` 提供 `LOCAL / STATEFUL / PROMPT` 三个常量。

### AC2. 别名冲突 panic

注册两条命令使用相同 name，`register_builtins()` 抛 `CommandRegistrationError`。

### AC3. 自反 alias 检测

注册命令的 `aliases` 含有其自身 `name` 时，注册立即抛 `CommandRegistrationError`。

### AC4. 大小写不敏感

`/HELP`、`/Help`、`/help` 均能命中 `/help` 命令。

### AC5. 空输入与未知命令

输入纯 `/` 触发未知命令分支；输入 `/foobar` 触发未知命令分支并附 `/help` 引导。

### AC6. 三类命令边界

- LOCAL handler（如 `/help` `/status` `/session list`）执行后 `session.messages` 不变。
- STATEFUL handler（如 `/clear` `/plan`）按预期修改运行时状态。
- PROMPT handler（`/review`）返回 `CommandResult(prompt_text=...)`，REPL 把该文本作为用户输入送入 `run_turn`。

### AC7. `/help` 分组

`/help` 输出按"查询命令 / 操作命令 / 对话命令"三段分组；隐藏命令不出现。

### AC8. Tab 补全

`/se<TAB>` 单匹配补全为 `/session`；`/p<TAB>` 多匹配弹菜单（候选含 `permission` `plan` 等）。

### AC9. `/status` 六节输出

`/status` 输出包含"供应商""模式""会话""权限""记忆""项目指令"六节标题。

### AC10. `/session` 子命令

- `/session list` 扫描 JSONL 输出列表。
- `/session current` 输出当前 id 与计数。
- `/session new` 切换到新 session_id，messages 清空。
- `/session resume <id>` 加载指定会话并重建消息历史。

### AC11. `/memory` 子命令

- `/memory show` 输出 user + project index 拼接文本。
- `/memory list user` 仅列出用户级笔记。
- `/memory refresh` 调用 `memory_manager.refresh()` 并打印重建结果。

### AC12. `/permission` 主名与别名

`/permission show` 与 `/permissions show` 行为一致；`/help` 仅列出主名 `/permission`。

### AC13. `/review` 行为

- 当前 session 有消息时，`/review` 让下一次 `run_turn` 接收的用户消息含预设 prompt 五条要点。
- `/review 关注 SQL` 时用户消息末尾含 "本次额外重点关注：关注 SQL"。
- 当前 session.messages 为空时，`/review` 不调用 LLM，仅打印提示。

### AC14. 老命令隐藏

`/help` 输出**不**包含 `/think` `/provider` `/providers` `/instructions` `/permissions`；但直接调用这些名字仍能工作。

### AC15. 启动 panic 友好

人为造一条与 `/help` 同名的内置命令，`main()` 启动失败、退出码 1、stderr 单行红字提示，无 traceback。

### AC16. PLAN 模式 prompt 前缀

`session.mode = "plan"` 后，下一次读取输入的 PROMPT 字符串等于 `[PLAN] > `；切回 `do` 后 PROMPT 恢复 `> `；其他模式或 thinking/permission 状态不影响 PROMPT。

### AC17. 不退化

- `pytest tests/ -q` 全绿。
- `scripts/verify_*.py` 已有 9 个脚本全部通过。
- 新增 `scripts/verify_commands.py` 端到端验证本阶段。

## 依赖与约束

- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台
- 继承前九阶段所有接口契约（特别是 `Compactor`、`SessionArchive`、`MemoryManager`、`InstructionsLoader`、`PermissionPolicy`）
