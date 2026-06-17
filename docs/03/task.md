# MewCode 第二阶段 Tasks

> 基于已批准的 `docs/03/spec.md` 与 `docs/03/plan.md`。共 24 个任务，
> 分 8 个阶段，覆盖内容块体系升级、工具系统、协议层改造、chat 重构、
> Renderer 增强、装配与端到端验收。

## 文件清单

| 操作 | 文件                                       | 职责                                |
|------|--------------------------------------------|-------------------------------------|
| 修改 | `mewcode/providers/base.py`                | Message.content → list[ContentBlock]|
| 新建 | `mewcode/providers/blocks.py`              | ContentBlock 系列                    |
| 修改 | `mewcode/providers/events.py`              | + ToolUseStart/InputDelta/End       |
| 修改 | `mewcode/providers/anthropic.py`           | tools 字段 + SSE 工具事件 + 历史序列化|
| 修改 | `mewcode/providers/openai.py`              | 同上                                 |
| 修改 | `mewcode/providers/__init__.py`            | 暴露新类型                          |
| 新建 | `mewcode/tools/__init__.py`                | 模块出口                            |
| 新建 | `mewcode/tools/base.py`                    | Tool / ToolResult / DangerLevel     |
| 新建 | `mewcode/tools/errors.py`                  | ToolError 系列                      |
| 新建 | `mewcode/tools/sandbox.py`                 | Sandbox                             |
| 新建 | `mewcode/tools/confirmer.py`               | Confirmer                           |
| 新建 | `mewcode/tools/registry.py`                | ToolRegistry + register_builtins    |
| 新建 | `mewcode/tools/_noise.py`                  | 噪声目录列表                        |
| 新建 | `mewcode/tools/read.py`                    | ReadTool                            |
| 新建 | `mewcode/tools/write.py`                   | WriteTool                           |
| 新建 | `mewcode/tools/edit.py`                    | EditTool                            |
| 新建 | `mewcode/tools/run.py`                     | RunTool                             |
| 新建 | `mewcode/tools/glob.py`                    | GlobTool                            |
| 新建 | `mewcode/tools/search.py`                  | SearchTool                          |
| 修改 | `mewcode/chat/session.py`                  | Session 升级                        |
| 修改 | `mewcode/chat/engine.py`                   | run_turn 重构（R1 + 工具 + R2）      |
| 修改 | `mewcode/render/renderer.py`               | + print_tool_* 方法                  |
| 修改 | `mewcode/repl/main_loop.py`                | 透传 registry/sandbox/confirmer     |
| 修改 | `mewcode/main.py`                          | 装配新对象                          |
| 新建 | `tests/test_blocks.py`                     | ContentBlock + Message 工厂          |
| 新建 | `tests/test_sandbox.py`                    | 路径越界                            |
| 新建 | `tests/test_tool_registry.py`              | 注册查找 + 协议格式输出              |
| 新建 | `tests/test_tools_read.py`                 | read 各场景                         |
| 新建 | `tests/test_tools_write.py`                | write 各场景                        |
| 新建 | `tests/test_tools_edit.py`                 | edit 三态                           |
| 新建 | `tests/test_tools_run.py`                  | run 成功/失败/超时                  |
| 新建 | `tests/test_tools_glob.py`                 | glob 噪声目录排除                   |
| 新建 | `tests/test_tools_search.py`               | search 单行截断 + file_glob          |
| 新建 | `tests/test_chat_round_loop.py`            | stub Provider 验证 R1 + R2 编排     |

共 34 个文件（21 新建 + 13 修改）。

---

## 任务执行顺序图

```
T1 → T2 → T3 ──────────────────────────────────────────────┐
                                                           │
T4 → T5 → T6 → T9 → T10/T11/T12/T13/T14 → T15 → T16 ────┐  │
              │                                          │  │
              T7 ─┘                                      │  │
T8  ─────────────────────────────────────────────────┐   │  │
                                                     │   │  │
       T17 → T18 ─┐                                  │   │  │
              T19 ┤                                  │   │  │
                  ↓                                  │   │  │
                  T20 ←──────────────────────────────┴───┴──┘
                  ↓
                  T22 ─→ T21 (T22 先于 T21，因 T21 调 Renderer 新方法)
                          ↓
                          T23 → T24
```

**关键路径（最长串行）**：
T1 → T2 → T3 → T20 → T22 → T21 → T23 → T24（8 步）

**可并行机会**：
- T4-T9 在 T1 后即可启动
- T10-T15 6 个工具任务可并行
- T17 / T18 / T19 协议改造与 tools 模块开发可并行
- T22 与 T17-T19 可并行

---

## T1：ContentBlock 数据类

**文件：**
- 新建 `mewcode/providers/blocks.py`

**依赖：** 无

**步骤：**

1. 文件顶部加中文 docstring：本模块定义消息内容块体系，是 spec F16 历史结构升级的核心。
2. 定义 4 个 frozen dataclass：
   - `TextBlock`：字段 `text: str`
   - `ThinkingBlock`：字段 `text: str`、`signature: str = ""`
   - `ToolUseBlock`：字段 `id: str`、`name: str`、`input: dict`
   - `ToolResultBlock`：字段 `tool_use_id: str`、`content: str`、`is_error: bool = False`
3. 定义类型别名 `ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock`
4. 每个类前加中文 docstring 说明用途与触发场景

**验证：**
- `python -m py_compile mewcode/providers/blocks.py`
- 内联：构造每种 block 实例不报错

---

## T2：Message 升级 + 块单测

**文件：**
- 修改 `mewcode/providers/base.py`
- 修改 `mewcode/providers/__init__.py`
- 新建 `tests/test_blocks.py`

**依赖：** T1

**步骤：**

1. 修改 `providers/base.py`：
   - import ContentBlock 与 4 个 Block 类型
   - 把 `Message.content` 从 `str` 改为 `list[ContentBlock]`
   - 添加 `@classmethod text(cls, role, content)` 工厂方法
   - 添加 `@classmethod tool_results(cls, results)` 工厂方法
2. 修改 `providers/__init__.py`：暴露 `TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock / ContentBlock`，写入 `__all__`
3. 在 `tests/test_blocks.py` 中写测试：
   - `test_message_text_工厂`
   - `test_message_tool_results_工厂`
   - `test_message_含混合块`
   - `test_blocks_frozen`

**验证：**
- `pytest tests/test_blocks.py -v` 全过
- `python -c "from mewcode.providers import Message, TextBlock; m = Message.text('user', 'hi'); print(m.content[0].text)"` 输出 `hi`

---

## T3：适配第一阶段 Message 用法

**文件：** 跨文件修改

**依赖：** T2

**步骤：**

1. 全文搜索 `Message(role=` 与 `Message(`，找出所有第一阶段构造点：
   - `mewcode/chat/session.py` 中 `append_user / append_assistant` 直接构造 `Message(role, content_str)`——本任务先打上"待 T20 重写"注释，不影响调用方
   - `scripts/verify_t9.py / verify_t10.py / verify_t11.py / verify_t15.py` 中的 `Message("user", "...")` 改为 `Message.text("user", "...")`
2. 在 `providers/anthropic.py` 与 `openai.py` 增加桥接函数 `_serialize_messages_legacy`：
   - 把 `list[ContentBlock]` 中所有 `TextBlock.text` 拼接还原为 str
   - 临时保留旧的 `[{"role": m.role, "content": <拼接>}]` 序列化路径，让第一阶段纯对话场景仍可用
   - 该函数会在 T18/T19 被替换为正式的协议序列化
3. 跑第一阶段单测 + 端到端确认无 regression

**验证：**
- 第一阶段 31 单测全过
- `python scripts/verify_t9.py / verify_t11.py / verify_t15.py` 仍输出正常对话
- `python -c "from mewcode.providers import Message; m = Message.text('user','hi'); print(type(m.content), len(m.content))"` 输出 `<class 'list'> 1`

---

## T4：Tool 抽象基类 + ToolResult + DangerLevel

**文件：**
- 新建 `mewcode/tools/__init__.py`（先空占位）
- 新建 `mewcode/tools/base.py`

**依赖：** 无（Sandbox 用 string forward ref）

**步骤：**

1. 创建 `mewcode/tools/` 目录与空 `__init__.py`
2. 在 `tools/base.py` 中定义：
   - `@dataclass(frozen=True) class ToolResult`：`success: bool`、`text: str`、`error_category: str | None = None`
   - `class DangerLevel`：类属性 `SAFE = "safe"`、`DANGEROUS = "dangerous"`
   - `class Tool(ABC)`：
     - 类属性 `name / description / parameters_schema / danger_level` 占位
     - `@abstractmethod async def execute(self, params, sandbox) -> ToolResult`
     - 三个非抽象渲染方法 `render_call_summary` / `render_confirm_detail` / `render_result_summary`，提供合理默认实现
3. Sandbox 在 base.py 中用字符串 forward ref（`"Sandbox"`），避免循环导入
4. 文件顶部 docstring 说明 spec F1 / F3

**验证：**
- `python -m py_compile mewcode/tools/base.py`
- `python -c "from mewcode.tools.base import Tool, ToolResult, DangerLevel; print(Tool.__abstractmethods__)"` 输出 `frozenset({'execute'})`

---

## T5：ToolError 异常体系

**文件：**
- 新建 `mewcode/tools/errors.py`

**依赖：** 无

**步骤：**

1. 定义 8 个异常类：
   - `ToolError(Exception)`，`category = "工具错误"`
   - `PathOutOfSandboxError`（路径越界）
   - `FileTooLargeError`（文件过大）
   - `FileDecodeError`（解码失败）
   - `EditNotFoundError`（未找到匹配）
   - `EditAmbiguousError`（匹配多次需更多上下文）
   - `CommandTimeoutError`（超时）
   - `ToolInterruptedError`（用户中断）
2. 每个类前加中文 docstring 说明触发场景

**验证：**
- `python -c "from mewcode.tools.errors import PathOutOfSandboxError; e = PathOutOfSandboxError('xxx'); print(e.category, str(e))"` 输出 `路径越界 xxx`

---

## T6：Sandbox + 单测

**文件：**
- 新建 `mewcode/tools/sandbox.py`
- 新建 `tests/test_sandbox.py`

**依赖：** T5

**步骤：**

1. 在 `sandbox.py` 中定义：
   - `@dataclass(frozen=True) class Sandbox`：字段 `cwd: Path`
   - `def resolve(self, raw_path: str) -> Path`：
     - 把 raw_path 拼到 cwd（绝对路径用绝对路径；相对路径相对 cwd）
     - 调 `Path.resolve()` 规范化
     - 用 `try: rel = resolved.relative_to(self.cwd.resolve())` 兜底校验，越界（ValueError）抛 `PathOutOfSandboxError(f"路径越界：{raw_path} 不在工作目录 {self.cwd} 内")`
2. 在 `tests/test_sandbox.py` 中写 6 个测试用例（用 `tmp_path` fixture）

**验证：**
- `pytest tests/test_sandbox.py -v` 全过

---

## T7：噪声目录常量

**文件：**
- 新建 `mewcode/tools/_noise.py`

**依赖：** 无

**步骤：**

1. 定义 `NOISE_DIRS = frozenset({...})`，含 `.git / __pycache__ / node_modules / .venv / venv / dist / build / .pytest_cache / .mypy_cache / .tox`
2. 定义 `def has_noise_part(p: Path, base: Path) -> bool`：
   - 计算 `p.relative_to(base).parts`
   - 任一段在 NOISE_DIRS 或以 `.egg-info` 结尾 → True

**验证：**
- 内联：`has_noise_part(Path('a/.git/b.py'), Path('a'))` 为 True；`has_noise_part(Path('a/src/b.py'), Path('a'))` 为 False

---

## T8：Confirmer

**文件：**
- 新建 `mewcode/tools/confirmer.py`

**依赖：** 无（运行期依赖 prompt_toolkit）

**步骤：**

1. 定义 `class ConfirmCancelled(Exception)`
2. 定义 `class Confirmer`：
   - `__init__`：内部初始化 `prompt_toolkit.PromptSession`
   - `async def ask(self, tool_name: str) -> bool`：
     - 调 `pt_session.prompt_async(f"执行 {tool_name}？[y/N] ")`
     - 捕获 `KeyboardInterrupt` → 抛 `ConfirmCancelled`
     - 捕获 `EOFError` → 视为拒绝，返回 False
     - 输入 strip().lower()：`"y"` 或 `"yes"` → True；否则 False

**验证：**
- 编译通过；交互验证留给 T23 REPL 测试

---

## T9：ToolRegistry + 协议格式输出 + 单测

**文件：**
- 新建 `mewcode/tools/registry.py`
- 新建 `tests/test_tool_registry.py`
- 修改 `mewcode/tools/__init__.py`

**依赖：** T4

**步骤：**

1. 在 `registry.py` 定义 `class ToolRegistry`：
   - `register / get / __getitem__ / __iter__ / all` 基础方法
   - `to_anthropic_format()` → 每项 `{"name", "description", "input_schema"}`
   - `to_openai_format()` → 每项 `{"type":"function", "function":{"name", "description", "parameters"}}`
   - `register_builtins(registry)` 函数留 placeholder 注释（T16 实现）
2. 修改 `tools/__init__.py` 暴露 Tool/ToolResult/DangerLevel/ToolRegistry/Sandbox/Confirmer/ConfirmCancelled 与 ToolError 系列
3. 在 `tests/test_tool_registry.py` 中（用 stub Tool）写 6 个测试用例

**验证：**
- `pytest tests/test_tool_registry.py -v` 全过

---

## T10：ReadTool + 单测

**文件：**
- 新建 `mewcode/tools/read.py`
- 新建 `tests/test_tools_read.py`

**依赖：** T4、T5、T6

**步骤：**

1. 在 `read.py` 实现 `class ReadTool(Tool)`：
   - `name = "read"`
   - `description` 说明用途、可选 offset/limit、256KB 截断
   - `parameters_schema`：path（必填）、offset（1-based 起始行）、limit（读取行数）
   - `danger_level = SAFE`
   - `execute` 内套 `asyncio.wait_for(timeout=30)`：
     - sandbox.resolve → 文件存在性检查 → utf-8 读取
     - 应用 offset/limit 切片
     - 字节数 > 256KB 截断 + "已截断"提示
     - 异常一律转为 ToolResult(success=False, error_category=...)
   - 覆盖 render_call_summary / render_result_summary（"读取 N 行"）
2. 在 `tests/test_tools_read.py` 写 6 个测试用例（含整体读取、offset/limit、不存在、越界、大文件截断、非 utf-8）

**验证：**
- `pytest tests/test_tools_read.py -v` 全过

---

## T11：WriteTool + 单测

**文件：**
- 新建 `mewcode/tools/write.py`
- 新建 `tests/test_tools_write.py`

**依赖：** T4、T5、T6

**步骤：**

1. 在 `write.py` 实现 `class WriteTool(Tool)`：
   - `name = "write"`、`danger_level = DANGEROUS`
   - `parameters_schema`：path、content（均必填）
   - `execute` 套 30s 超时：sandbox.resolve → mkdir(parents=True) → write_text(utf-8) → ToolResult(True, "已写入: ..., N 字符")
   - 覆盖 render_confirm_detail（路径 + 内容前 20 行预览）
2. 在 `tests/test_tools_write.py` 写 4 个测试（新建/覆盖/自动创建父目录/越界）

**验证：**
- `pytest tests/test_tools_write.py -v` 全过

---

## T12：EditTool + 单测

**文件：**
- 新建 `mewcode/tools/edit.py`
- 新建 `tests/test_tools_edit.py`

**依赖：** T4、T5、T6

**步骤：**

1. 在 `edit.py` 实现 `class EditTool(Tool)`：
   - `name = "edit"`、`danger_level = DANGEROUS`
   - `parameters_schema`：path、old_text、new_text（均必填）
   - `execute` 套 30s：
     - sandbox.resolve → 读全文
     - count = text.count(old_text)
     - count == 0 → EditNotFoundError → ToolResult(False, ..., "未找到匹配")
     - count > 1 → EditAmbiguousError → ToolResult(False, "匹配 N 次需更多上下文", "匹配多次需更多上下文")
     - count == 1 → text.replace(old, new, 1) 写回 → ToolResult(True, "替换成功: ..., 1 处")
   - 覆盖 render_confirm_detail（用 difflib.unified_diff 展示）
2. 在 `tests/test_tools_edit.py` 写 6 个测试（唯一匹配/多次/未匹配/越界/不存在/render_confirm 烟雾）

**验证：**
- `pytest tests/test_tools_edit.py -v` 全过

---

## T13：RunTool + 单测

**文件：**
- 新建 `mewcode/tools/run.py`
- 新建 `tests/test_tools_run.py`

**依赖：** T4、T5

**步骤：**

1. 在 `run.py` 实现 `class RunTool(Tool)`：
   - `name = "run"`、`danger_level = DANGEROUS`
   - `parameters_schema`：command（必填）
   - `execute`：
     - 用 `asyncio.create_subprocess_shell(command, stdout=PIPE, stderr=PIPE, cwd=str(sandbox.cwd))`
     - `await asyncio.wait_for(process.communicate(), timeout=60.0)`
     - 超时则 `process.kill(); await process.wait()` → ToolResult(False, ..., "超时")
     - utf-8 解码 stdout/stderr（errors='replace'）
     - 拼装 text："$ <cmd>\n退出码：N\n--- stdout ---\n<out>\n--- stderr ---\n<err>"
     - 总长度 > 32KB 截断
     - 返回 ToolResult(success=(exit_code==0), text=拼装文本, error_category="非零退出" if exit_code!=0 else None)
   - 覆盖 render_call_summary（命令截断 60 字符）
   - 覆盖 render_confirm_detail（"即将执行命令：\n  <cmd>"）
   - 覆盖 render_result_summary（从 text 抽 "退出码：N" 显示）
2. 在 `tests/test_tools_run.py` 写 4 个测试（成功/非零/超时-monkey-patch 2s/CWD）

**验证：**
- `pytest tests/test_tools_run.py -v` 全过；超时测试应 < 5 秒

---

## T14：GlobTool + 单测

**文件：**
- 新建 `mewcode/tools/glob.py`
- 新建 `tests/test_tools_glob.py`

**依赖：** T4、T6、T7

**步骤：**

1. 在 `glob.py` 实现 `class GlobTool(Tool)`：
   - `name = "glob"`、`danger_level = SAFE`
   - `parameters_schema`：pattern（必填）
   - `execute` 套 30s：
     - 校验 pattern 不以 `/` 开头、不含 `..`，否则 PathOutOfSandboxError
     - `sandbox.cwd.rglob(pattern)` 拿候选 → 过滤 `is_file() and not has_noise_part(p, sandbox.cwd)`
     - relative_to(cwd).as_posix() 转相对 → sorted
     - count > 1000 截断
     - 返回多行 text："匹配 N 项：\n<paths>"
   - 提供内部 helper `_search_files(sandbox, pattern) -> list[Path]` 供 SearchTool 复用
2. 在 `tests/test_tools_glob.py` 写 5 个测试（基础/排序/噪声目录排除/无匹配/越界）

**验证：**
- `pytest tests/test_tools_glob.py -v` 全过

---

## T15：SearchTool + 单测

**文件：**
- 新建 `mewcode/tools/search.py`
- 新建 `tests/test_tools_search.py`

**依赖：** T4、T6、T7、T14

**步骤：**

1. 在 `search.py` 实现 `class SearchTool(Tool)`：
   - `name = "search"`、`danger_level = SAFE`
   - `parameters_schema`：pattern（必填）、file_glob（可选默认 `**/*`）、is_literal（可选默认 false）
   - `execute` 套 30s：
     - 编译正则：`re.compile(re.escape(pattern) if is_literal else pattern)`
     - 调 GlobTool 的 `_search_files` 拿候选文件
     - 遍历文件 → utf-8 读 → splitlines → 1-based 行号枚举 → `pat.search(line)` 命中收集 (rel_path, lineno, line[:500])
     - 收集 200 条后停止
     - 拼装 text："匹配 N 处（搜索 M 个文件）：\n{path}:{lineno}: {line}"
   - 覆盖 render_call_summary
2. 在 `tests/test_tools_search.py` 写 7 个测试（基础/正则/单行截断/file_glob/噪声排除/is_literal/无效正则）

**验证：**
- `pytest tests/test_tools_search.py -v` 全过

---

## T16：register_builtins() 串起来

**文件：**
- 修改 `mewcode/tools/registry.py`
- 修改 `mewcode/tools/__init__.py`
- 修改 `tests/test_tool_registry.py`（追加测试）

**依赖：** T9、T10-T15

**步骤：**

1. 在 `registry.py` 末尾追加 `register_builtins(registry)` 函数：
   - 函数内 import 6 个工具类（避免循环导入）
   - 依次 `registry.register(ReadTool() / WriteTool() / EditTool() / RunTool() / GlobTool() / SearchTool())`
2. `tools/__init__.py` 暴露 `register_builtins`
3. 在 `tests/test_tool_registry.py` 追加：
   - `test_register_builtins`：构造空 registry → 调 register_builtins → 断言含 6 个工具
   - 断言 `to_anthropic_format()` 与 `to_openai_format()` 各返回 6 个元素

**验证：**
- `pytest tests/test_tool_registry.py -v` 全过
- `python -c "from mewcode.tools import ToolRegistry, register_builtins; r = ToolRegistry(); register_builtins(r); print(sorted(t.name for t in r))"` 输出 `['edit', 'glob', 'read', 'run', 'search', 'write']`

---

## T17：events.py 扩展 ToolUse 事件

**文件：**
- 修改 `mewcode/providers/events.py`
- 修改 `mewcode/providers/__init__.py`

**依赖：** 无

**步骤：**

1. 在 `events.py` 新增 3 个 frozen dataclass：
   - `ToolUseStart(id, name)`
   - `ToolUseInputDelta(id, json_chunk)`
   - `ToolUseEnd(id, name, input)`
2. 更新 `StreamEvent` 联合类型加入 3 个新事件
3. `providers/__init__.py` 暴露 3 个新类型，写入 `__all__`

**验证：**
- `python -c "from mewcode.providers import ToolUseStart, ToolUseInputDelta, ToolUseEnd, StreamEvent; print('ok')"`

---

## T18：AnthropicProvider 改造

**文件：**
- 修改 `mewcode/providers/anthropic.py`

**依赖：** T1、T2、T17

**步骤：**

1. `stream_chat` 签名增加 `tools_format: list[dict] | None = None`
2. 实现 `_serialize_messages_anthropic(messages)` 替换 T3 桥接：
   - assistant 消息 content 翻译：TextBlock → text 块；ThinkingBlock → thinking 块（含 signature）；ToolUseBlock → tool_use 块
   - user 消息纯文本 → content 字符串；含 ToolResultBlock → content 块列表（tool_result 块）
3. 请求体构造时 `if tools_format: body["tools"] = tools_format`
4. SSE 解析新增分支（按 plan 时序图）：
   - `content_block_start` (type=tool_use, index=I, id=X, name=Y) → 维护 `tool_use_buf[I] = {id, name, args:""}`，yield ToolUseStart(X, Y)
   - `content_block_delta` (type=input_json_delta, partial_json=P) → 累加 args，yield ToolUseInputDelta(id, P)
   - `content_block_stop` 在 tool_use 上 → `json.loads(args)`，yield ToolUseEnd(id, name, input)；JSONDecodeError → StreamParseError
5. 保留 thinking_delta 处理（T10 已加，含 thinking=False 时丢弃逻辑）
6. 文件顶部 docstring 更新

**验证：**
- `python -m py_compile mewcode/providers/anthropic.py`
- 临时脚本 `scripts/verify_t18.py` 注册 ReadTool 后发起需要工具的 prompt，观察事件流含 ToolUseStart / End
- 第一阶段单测仍全过

---

## T19：OpenAIProvider 改造

**文件：**
- 修改 `mewcode/providers/openai.py`

**依赖：** T1、T2、T17

**步骤：**

1. `stream_chat` 签名增加 `tools_format`
2. 实现 `_serialize_messages_openai(messages)`：
   - assistant 消息：拆 text 部分与 tool_use 部分，构造 `{role:"assistant", content:<text 拼接 or None>, tool_calls:[{id, type:"function", function:{name, arguments:json.dumps(input)}}, ...]}`，无 tool_call 时不带 tool_calls 字段
   - ThinkingBlock 在 OpenAI 协议下忽略
   - user 含 ToolResultBlock：每个 ToolResultBlock 单独成一条 `{role:"tool", tool_call_id:..., content:...}` 消息
   - 纯文本 user：`{role:"user", content:...}`
3. 请求体加 `tools` 字段
4. SSE 解析新增（按 plan 时序图）：
   - 维护 `tool_call_state: dict[index, dict]`
   - 首次见到 index → 取 id/name → yield ToolUseStart
   - arguments 增量 → 累加，yield ToolUseInputDelta
   - `finish_reason == "tool_calls"` → 遍历 state 发 ToolUseEnd，清空 state
5. 文件顶部 docstring 更新

**验证：**
- `python -m py_compile mewcode/providers/openai.py`
- 临时脚本 `scripts/verify_t19.py` 切换 deepseek-openai 后跑工具调用流程
- 第一阶段单测仍全过

---

## T20：Session 升级

**文件：**
- 修改 `mewcode/chat/session.py`

**依赖：** T1、T2

**步骤：**

1. 替换 Session 的方法：
   - `append_user_text(text)` → 追加 Message.text("user", text)
   - `append_assistant(blocks: list[ContentBlock])` → 追加 Message(role="assistant", content=list(blocks))
   - `append_tool_results(results: list[ToolResultBlock])` → 追加 Message.tool_results(results)
   - 删除旧的 `append_user / append_assistant(text: str)` 以暴露调用方问题
   - 保留 `clear / switch_provider`
2. 字段保留：`provider / messages / thinking_enabled / current_provider_name`

**验证：**
- 内联：构造 Session → 各方法调用 → 断言历史长度与块数

---

## T21：chat.run_turn 重构 + 编排单测

**文件：**
- 修改 `mewcode/chat/engine.py`
- 新建 `tests/test_chat_round_loop.py`

**依赖：** T16、T18、T19、T20、T22

**步骤：**

1. 重写 `run_turn(session, user_input, renderer, registry, confirmer, sandbox) -> bool`：
   - `session.append_user_text(user_input)`
   - 调 `_consume_round` 跑 R1
   - `tool_uses = [b for b in r1_blocks if isinstance(b, ToolUseBlock)]`
   - `session.append_assistant(r1_blocks)`
   - 无 tool_uses → print_usage(r1_usage) + return True
   - 串行执行 tool_uses（DANGEROUS 工具走确认；KeyboardInterrupt/ConfirmCancelled → pop R1 + return False）
   - `session.append_tool_results(results)`
   - 调 `_consume_round` 跑 R2
   - 剥离 R2 的 ToolUseBlock；含 leftover → print_info 硬停提示
   - `print_usage_combined(r1_usage, r2_usage)` + return True
2. 重写 `_consume_round(session, renderer, registry)`：
   - tools_format 按 protocol 选 anthropic/openai 格式
   - 调 stream_chat → async for 事件分派 → 累积块 → 流结束打包返回 (blocks, usage)
   - 沿用第一阶段 SIGINT handler / sub_task / aclose finally 防线
3. 在 `tests/test_chat_round_loop.py` 用 stub Provider 写 6 个测试：
   - test_R1直答_退化第一阶段
   - test_R1工具_R2文本_完整闭环
   - test_单R1多tool_use_串行
   - test_用户拒绝_R1入历史_拒绝result进Round2
   - test_中断回滚（ConfirmCancelled → messages.pop）
   - test_R2含tool_use硬停（剥离 + leftover 提示）

**验证：**
- `pytest tests/test_chat_round_loop.py -v` 全过
- `verify_t15.py` 多轮对话仍跑通（不退化）

---

## T22：Renderer 新方法

**文件：**
- 修改 `mewcode/render/renderer.py`

**依赖：** T1、T17（Usage 类型）

**步骤：**

1. 增加方法（沿用朴素 sys.stdout.write 风格）：
   - `print_tool_call(name, summary)` → "▸ <name>(<summary>)\n"
   - `print_tool_confirm_detail(detail)` → 多行 detail
   - `print_tool_result_summary(summary)` → "  <summary>\n"
   - `print_tool_rejected(name)` → "已拒绝执行 <name>\n"
   - `print_usage_combined(r1, r2)` → 累计 input/output（任一含 thinking_tokens 时累计显示）
2. import Usage

**验证：**
- 手工脚本 `scripts/verify_t22.py` 调每个方法一次，目测格式正确无乱码

---

## T23：main.py + repl 装配

**文件：**
- 修改 `mewcode/main.py`
- 修改 `mewcode/repl/main_loop.py`

**依赖：** T16、T21、T22

**步骤：**

1. 修改 `main.py`：
   - import `ToolRegistry / register_builtins / Sandbox / Confirmer`
   - 阶段 2 装配后追加：构造 registry / sandbox / confirmer
   - 阶段 3 调 `run_repl` 时多传 3 个参数
2. 修改 `repl/main_loop.py`：
   - `run_repl` 签名增加 `registry / sandbox / confirmer`
   - 调 `chat.run_turn` 时多传 3 个参数

**验证：**
- `python -m py_compile mewcode/main.py mewcode/repl/main_loop.py`
- 启动测试：`python -m mewcode` → 输入"你好"应正常对话；输入"读 README.md 的第一行"应触发完整闭环

---

## T24：跑 checklist.md 全量验收 + 生成验收报告

**文件：**
- 产出 `docs/03/acceptance-report.md`

**依赖：** T23 + 阶段四 checklist.md

**步骤：**

1. 阅读 `docs/03/checklist.md`
2. 自动可验证项：
   - `pytest tests/ -q`（应有 86+ 测试）
   - 写 `scripts/verify_tools_full.py` 直接调 6 个工具验证基础语义
   - 写 `scripts/verify_round_loop.py` 用真实 API 跑完整闭环
3. 交互项在 PowerShell 中按 manual 跑通（AC8/AC9/AC10/AC11/AC22 等），记录证据
4. 逐项填入 `acceptance-report.md`，模板沿用第一阶段
5. 失败定位回对应 T 任务修复；全部通过后 close 第二阶段

**验证：**
- 所有 spec.md 中 AC1-AC38 都有 PASSED 或明确"已知降级/待补"标注
- acceptance-report.md 完整生成

---

## 任务汇总

| #   | 任务                                    | 依赖                  | 文件数 | 测试   |
|-----|-----------------------------------------|-----------------------|--------|--------|
| T1  | ContentBlock 数据类                      | 无                    | 1      | -      |
| T2  | Message 升级 + 单测                      | T1                    | 3      | ✅ 4   |
| T3  | 适配第一阶段 Message 用法                 | T2                    | 多     | 跑回归  |
| T4  | Tool 抽象基类                            | T1                    | 2      | -      |
| T5  | ToolError 异常                          | 无                    | 1      | -      |
| T6  | Sandbox + 单测                          | T5                    | 2      | ✅ 6   |
| T7  | 噪声目录常量                            | 无                    | 1      | -      |
| T8  | Confirmer                              | 无                    | 1      | -      |
| T9  | ToolRegistry + 单测                     | T4                    | 3      | ✅ 6   |
| T10 | ReadTool + 单测                         | T4/T5/T6              | 2      | ✅ 6   |
| T11 | WriteTool + 单测                        | T4/T5/T6              | 2      | ✅ 4   |
| T12 | EditTool + 单测                         | T4/T5/T6              | 2      | ✅ 6   |
| T13 | RunTool + 单测                          | T4/T5                 | 2      | ✅ 4   |
| T14 | GlobTool + 单测                         | T4/T6/T7              | 2      | ✅ 5   |
| T15 | SearchTool + 单测                       | T4/T6/T7/T14          | 2      | ✅ 7   |
| T16 | register_builtins                       | T10-T15               | 修+测  | ✅ +1  |
| T17 | events.py 扩展                          | 无                    | 1 修   | -      |
| T18 | AnthropicProvider 改造                  | T1/T2/T17             | 1 修   | 真实    |
| T19 | OpenAIProvider 改造                     | T1/T2/T17             | 1 修   | 真实    |
| T20 | Session 升级                            | T1/T2                 | 1 修   | 内联    |
| T21 | run_turn 重构 + 单测                    | T16/T18/T19/T20/T22   | 1 修+测| ✅ 6   |
| T22 | Renderer 新方法                         | T1                    | 1 修   | 手工    |
| T23 | main + repl 装配                        | T21                   | 2 修   | 手工    |
| T24 | 全量验收                                | T23 + checklist       | -      | 全量    |

**单测累计**：约 55 个新增 + 31 个第一阶段保留 = 86 个

---

## 自检结论

- ✅ **plan 覆盖**：plan.md 所有组件均有任务对应
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落
- ✅ **依赖链**：执行图有合法拓扑序，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（Tool/ToolResult/ToolUseBlock/run_turn 签名等）
