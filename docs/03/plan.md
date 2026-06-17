# MewCode 第二阶段 Plan

> 基于已批准的 `docs/03/spec.md`。本文档定义工具系统架构、数据结构、
> 模块边界、关键技术决策。语言相关，按 Python 3.10+ 设计。

## 架构概览

### 设计思路（基于 spec 关键约束）

| spec 约束 | 架构含义 |
|----------|---------|
| F1/F2 统一 Tool 抽象 + 注册中心 | 单独的 `tools` 模块，与 `providers` 平级 |
| F11 协议层都要支持工具调用 | Provider 层增加"序列化 Tool list"和"反序列化 SSE 工具事件"两个翻译职责 |
| F12-F14 单轮闭环（Round 1 → 工具 → Round 2） | chat 层升级为"两阶段编排器" |
| F16 历史结构升级为内容块列表 | `Message.content` 从 `str` 改为 `list[ContentBlock]`；面向第一阶段的破坏性升级 |
| F17 协议特性差异 | Provider 内部把内容块翻译成各家协议的格式 |
| F19 中等粒度 UI | Renderer 增加工具相关方法（调用前提示、确认对话、简略反馈） |
| F10 工作目录沙盒 | tools 模块内统一的"路径校验"helper |

### 模块演进

```
原有：config / transport / providers / chat / commands / render / repl / main
新增：tools                ← 工具抽象 + 实现 + 注册中心
增强：providers / chat / render / repl / main
```

### 整体分层

```
┌──────────────────────────────────────────────────┐
│ entry (main.py) — 装配对象图，构造 ToolRegistry / │
│ Sandbox / Confirmer 注入下游                       │
└──────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────┐
│ repl 层 — 主循环不变；多了一处把 ToolRegistry /    │
│ Sandbox / Confirmer 透传给 chat.run_turn          │
└──────────────────────────────────────────────────┘
                       ↓
┌─────────────────┬────────────────────────────────┐
│ commands 层      │ chat 层（重构）                 │
│ — 不变           │ - Session 历史升级为内容块列表  │
│                 │ - run_turn 重构为：             │
│                 │   Round1 → 收集 ToolUse → 串行  │
│                 │   → 回灌 → Round2 → 硬停处理     │
└─────────────────┴────────────────────────────────┘
              ↓    ↓
┌─────────────┬─────────────────┬──────────────────┐
│ render 层    │ tools 层（新增） │ providers 层（增强）│
│ - 流式渲染    │ - Tool 抽象      │ - 请求体加 tools 字段│
│ - 工具调用    │ - 6 个工具实现   │ - SSE 解析含       │
│   提示       │ - ToolRegistry   │   ToolUseStart/    │
│ - 确认回显    │ - Sandbox 路径   │   InputDelta/End   │
│ - 简略反馈    │   校验           │ - 历史按协议序列化  │
└─────────────┴─────────────────┴──────────────────┘
                       ↓
                              ┌────────────────────┐
                              │ transport 层 — 不变│
                              └────────────────────┘
                       ↓
                              ┌────────────────────┐
                              │ config 层 — 不变   │
                              └────────────────────┘
```

### 模块职责一览（第二阶段后）

| 模块 | 职责 | 改动量 |
|------|------|-------|
| `config` | 不变 | - |
| `transport` | 不变 | - |
| `providers` | + tools 序列化 + ToolUseDelta/End SSE 解析 + 历史按协议序列化 | **大** |
| **`tools`（新增）** | Tool 抽象 + 6 实现 + ToolRegistry + Sandbox + Confirmer | **大** |
| `chat` | Session 历史结构升级 + run_turn 重构为两阶段编排 | **大** |
| `render` | + 工具调用提示 / 确认回显 / 简略反馈方法 | 中 |
| `commands` | 不变（七个命令仍然是命令；本阶段不加 `/tools` 等） | - |
| `repl` | 透传 registry/sandbox/confirmer 给 chat | 小 |
| `main` | + 启动时构造 ToolRegistry/Sandbox/Confirmer | 小 |

---

## 核心数据结构

### 内容块体系（providers/blocks.py — 新增）

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class TextBlock:
    """assistant 或 user 消息中的纯文本块。"""
    text: str

@dataclass(frozen=True)
class ThinkingBlock:
    """assistant 消息中的思考块（仅 Anthropic + thinking 开启时出现）。
    协议要求把 thinking 块原样回传给后端以维持上下文一致性。"""
    text: str
    signature: str = ""   # Anthropic 协议要求回传的签名字段（如有）

@dataclass(frozen=True)
class ToolUseBlock:
    """assistant 发起的工具调用。"""
    id: str               # 协议生成的工具调用 ID
    name: str
    input: dict           # 已拼接好的参数字典（JSON 解析后）

@dataclass(frozen=True)
class ToolResultBlock:
    """user 消息中的工具结果块（回填给模型）。"""
    tool_use_id: str
    content: str          # 面向模型的文本（成功输出或错误信息）
    is_error: bool = False

ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock
```

### Message 升级（providers/base.py — 修改）

```python
@dataclass(frozen=True)
class Message:
    """会话历史中的一条消息。

    破坏性升级：content 从 str 变为 list[ContentBlock]。
    需要纯文本的旧场景用 Message.text(role, content_str) 工厂方法。
    """
    role: Role
    content: list[ContentBlock]

    @classmethod
    def text(cls, role: Role, content: str) -> "Message":
        return cls(role=role, content=[TextBlock(text=content)])

    @classmethod
    def tool_results(cls, results: list[ToolResultBlock]) -> "Message":
        return cls(role="user", content=list(results))
```

### Tool 抽象（tools/base.py — 新增）

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class ToolResult:
    """工具执行返回的结构化结果。"""
    success: bool
    text: str
    error_category: str | None = None


class DangerLevel:
    SAFE = "safe"            # 自动执行（read/glob/search）
    DANGEROUS = "dangerous"  # 执行前必须确认（write/edit/run）


class Tool(ABC):
    """所有具体工具继承此基类。"""

    name: str = ""
    description: str = ""
    parameters_schema: dict = {}
    danger_level: str = DangerLevel.SAFE

    @abstractmethod
    async def execute(self, params: dict, sandbox: "Sandbox") -> ToolResult:
        """执行工具。任何异常都应在内部捕获转为 ToolResult(success=False)。"""

    def render_call_summary(self, params: dict) -> str:
        """生成 '▸ <name>(<key params>)' 中括号内的参数概要。"""

    def render_confirm_detail(self, params: dict) -> str:
        """生成确认提示中展示的多行详细信息（write/edit/run 覆盖）。"""

    def render_result_summary(self, result: ToolResult) -> str:
        """生成调用后的一行简略反馈。"""
```

### ToolRegistry（tools/registry.py — 新增）

```python
class ToolRegistry:
    """工具注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def __getitem__(self, name: str) -> Tool: ...
    def __iter__(self): ...
    def all(self) -> list[Tool]: ...

    def to_anthropic_format(self) -> list[dict]:
        """每项形如 {name, description, input_schema}。"""

    def to_openai_format(self) -> list[dict]:
        """每项形如 {type:'function', function:{name, description, parameters}}。"""


def register_builtins(registry: ToolRegistry) -> None:
    """一次性注册全部 6 个内置工具。新增工具时此处加一行。"""
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    registry.register(RunTool())
    registry.register(GlobTool())
    registry.register(SearchTool())
```

### Sandbox（tools/sandbox.py — 新增）

```python
@dataclass(frozen=True)
class Sandbox:
    """工作目录沙盒。基于 Path.cwd() 在 main 启动时创建。"""
    cwd: Path

    def resolve(self, raw_path: str) -> Path:
        """把 raw_path 解析为绝对路径并校验落在 cwd 子树内。

        Raises:
            PathOutOfSandboxError: 越界。
        """
```

### Confirmer（tools/confirmer.py — 新增）

```python
class Confirmer:
    """用户确认对话器。

    - SAFE 工具不调用此模块。
    - DANGEROUS 工具调用前由 chat 层调用 ask()。
    - 走 prompt_toolkit 的 PromptSession.prompt_async。
    """

    async def ask(self, tool_name: str) -> bool:
        """显示 '执行 <tool_name>？[y/N] ' 后等待输入。
        返回 True=允许执行；
        Ctrl+C 抛 ConfirmCancelled（区别于普通拒绝）。"""


class ConfirmCancelled(Exception):
    """用户在确认提示中按 Ctrl+C，表示取消整个 turn。"""
```

注：detail 的多行预览/diff 由 chat 层先调 Renderer 打印；Confirmer 只负责"提示 + 收输入"。

### SSE 事件类型扩展（providers/events.py — 修改）

```python
# 已有：TextDelta / ThinkingDelta / Usage / Done

@dataclass(frozen=True)
class ToolUseStart:
    id: str
    name: str

@dataclass(frozen=True)
class ToolUseInputDelta:
    id: str
    json_chunk: str

@dataclass(frozen=True)
class ToolUseEnd:
    id: str
    name: str
    input: dict           # 由 Provider 在 End 时把累计 JSON 字符串 json.loads

StreamEvent = (
    TextDelta | ThinkingDelta
    | ToolUseStart | ToolUseInputDelta | ToolUseEnd
    | Usage | Done
)
```

### Session 升级（chat/session.py — 修改）

```python
@dataclass
class Session:
    provider: Provider
    messages: list[Message] = field(default_factory=list)
    thinking_enabled: bool = False
    current_provider_name: str = ""

    def append_user_text(self, text: str) -> None:
        self.messages.append(Message.text("user", text))

    def append_assistant(self, blocks: list[ContentBlock]) -> None:
        self.messages.append(Message(role="assistant", content=blocks))

    def append_tool_results(self, results: list[ToolResultBlock]) -> None:
        self.messages.append(Message.tool_results(results))

    def clear(self) -> None: ...
    def switch_provider(self, ...) -> None: ...
```

---

## 异常体系

### tools/errors.py（新增）

```python
class ToolError(Exception):
    """工具层基类。所有 ToolError 子类都被工具自身捕获并转为 ToolResult。"""
    category: str = "工具错误"

class PathOutOfSandboxError(ToolError):
    category = "路径越界"

class FileTooLargeError(ToolError):
    category = "文件过大"

class FileDecodeError(ToolError):
    category = "解码失败"

class EditNotFoundError(ToolError):
    category = "未找到匹配"

class EditAmbiguousError(ToolError):
    category = "匹配多次需更多上下文"

class CommandTimeoutError(ToolError):
    category = "超时"

class ToolInterruptedError(ToolError):
    category = "用户中断"
```

异常处理位置：
- `ToolError` 由工具自身捕获，转为 `ToolResult(success=False, error_category=cat, text=...)`
- `ConfirmCancelled` / `KeyboardInterrupt` 在 chat 层捕获，回滚 R1 历史，return False
- `ProviderError` 沿用第一阶段处理路径

---

## Provider 改造

### AnthropicProvider

#### 请求体新增

```python
if tools_format:
    body["tools"] = tools_format    # 来自 registry.to_anthropic_format()
```

#### 历史序列化（spec F16/F17）

```python
def _serialize_messages_anthropic(messages: list[Message]) -> list[dict]:
    """Anthropic 协议下：
    - assistant 消息的 content 是块列表：
      [{type:"text", text:"..."},
       {type:"thinking", thinking:"...", signature:"..."},
       {type:"tool_use", id:"...", name:"...", input:{}}]
    - user 消息可以是字符串也可以是块列表，含 tool_result 时用：
      [{type:"tool_result", tool_use_id:"...", content:"...", is_error:bool}]
    """
```

#### SSE 事件映射（新增）

| Anthropic SSE | 内部事件 |
|---------------|---------|
| `content_block_start` (type=tool_use, id=X, name=Y) | `ToolUseStart(X, Y)` |
| `content_block_delta` (type=input_json_delta, partial_json) | `ToolUseInputDelta` |
| `content_block_stop`（在 tool_use 块上） | `ToolUseEnd(id, name, json.loads(累计 JSON))` |

Provider 内部维护 `dict[id, str]` 累计参数 JSON 碎片；JSON 解析失败抛 `StreamParseError`。

### OpenAIProvider

#### 请求体新增

```python
if tools_format:
    body["tools"] = tools_format
```

#### 历史序列化

```python
def _serialize_messages_openai(messages: list[Message]) -> list[dict]:
    """OpenAI 协议下：
    - assistant 工具调用走 tool_calls 字段：
      {role:"assistant", content:"text内容或null", tool_calls:[
        {id:"...", type:"function", function:{name:"...", arguments:"<JSON 字符串>"}}
      ]}
    - 工具结果走 role=tool 的独立消息：
      {role:"tool", tool_call_id:"...", content:"..."}
    - thinking 块在 OpenAI 协议下不存在，序列化时忽略。
    """
```

#### SSE 事件映射

OpenAI 流式工具调用通过 `delta.tool_calls[]` 增量返回，按 index 维度拼装：
- 首次见到 index → 发 `ToolUseStart`
- arguments 增量 → 发 `ToolUseInputDelta` 同时累加 args_buf
- `finish_reason == "tool_calls"` 时遍历所有 index 发 `ToolUseEnd`

---

## chat 层重构

### chat/engine.py（重写）

```python
async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
    registry: ToolRegistry,
    confirmer: Confirmer,
    sandbox: Sandbox,
) -> bool:
    """跑一轮对话，含 Round 1 + 工具执行 + Round 2 单轮闭环。

    返回值：
        True  — 正常完成（含 R1 直答 / 完整闭环 / R2 含 tool_use 硬停）
        False — 被用户中断 / Provider 错误。
    """
    session.append_user_text(user_input)

    # ---------- Round 1 ----------
    r1_blocks, r1_usage = await _consume_round(session, renderer, registry)
    if r1_blocks is None:
        return False    # 中断/错误

    tool_uses = [b for b in r1_blocks if isinstance(b, ToolUseBlock)]
    session.append_assistant(r1_blocks)

    # 无工具调用 → 退化为第一阶段
    if not tool_uses:
        if r1_usage:
            renderer.print_usage(r1_usage)
        return True

    # ---------- 工具执行（串行 + 确认）----------
    tool_results: list[ToolResultBlock] = []
    try:
        for tu in tool_uses:
            tool = registry.get(tu.name)
            if tool is None:
                tool_results.append(ToolResultBlock(
                    tool_use_id=tu.id,
                    content=f"未知工具：{tu.name}",
                    is_error=True,
                ))
                continue

            renderer.print_tool_call(tool.name, tool.render_call_summary(tu.input))

            if tool.danger_level == DangerLevel.DANGEROUS:
                renderer.print_tool_confirm_detail(tool.render_confirm_detail(tu.input))
                approved = await confirmer.ask(tool.name)
                if not approved:
                    renderer.print_tool_rejected(tool.name)
                    tool_results.append(ToolResultBlock(
                        tool_use_id=tu.id,
                        content="用户拒绝执行此工具",
                        is_error=True,
                    ))
                    continue

            result = await tool.execute(tu.input, sandbox)
            renderer.print_tool_result_summary(tool.render_result_summary(result))
            tool_results.append(ToolResultBlock(
                tool_use_id=tu.id,
                content=result.text,
                is_error=not result.success,
            ))

    except (KeyboardInterrupt, ConfirmCancelled, asyncio.CancelledError):
        renderer.print_info("（已取消本轮）")
        # 协议要求 tool_use 必须有对应 tool_result——回滚 R1 assistant
        # 消息（pop）保持历史合法
        session.messages.pop()
        return False

    session.append_tool_results(tool_results)

    # ---------- Round 2 ----------
    r2_blocks, r2_usage = await _consume_round(session, renderer, registry)
    if r2_blocks is None:
        return False

    # F15 硬停：剥离 tool_use 块
    cleaned_blocks: list[ContentBlock] = []
    leftover_tools: list[str] = []
    for b in r2_blocks:
        if isinstance(b, ToolUseBlock):
            leftover_tools.append(b.name)
        else:
            cleaned_blocks.append(b)
    session.append_assistant(cleaned_blocks)

    if leftover_tools:
        names = "、".join(leftover_tools)
        renderer.print_info(
            f"模型在最终答复中还想调用工具 {names}（共 {len(leftover_tools)} 个），"
            "本阶段不再继续；下一轮可以追问。"
        )

    if r1_usage or r2_usage:
        renderer.print_usage_combined(r1_usage, r2_usage)

    return True


async def _consume_round(
    session: Session, renderer: Renderer, registry: ToolRegistry,
) -> tuple[list[ContentBlock] | None, Usage | None]:
    """跑一次流式请求，按事件类型边渲染边累积块。

    返回 (blocks, usage) 或 (None, None) 表示中断/错误（已渲染过）。

    内部职责：
    - 装 SIGINT handler，把流式包成 sub-task，Ctrl+C 触发 cancel
    - 按事件类型分派：
        TextDelta      → renderer.push_text + 累计当前 text 块
        ThinkingDelta  → renderer.push_thinking + 累计当前 thinking 块
        ToolUseStart   → 进入 tool_use 累积模式
        ToolUseInputDelta → 累加（本阶段 UI 不显示参数增量）
        ToolUseEnd     → 把累积块封装成 ToolUseBlock 加入 blocks
        Usage          → pending_usage = u
        Done           → finished = True，继续吃完流（防 GeneratorExit）
    - finally 显式 aclose stream（继承第一阶段防线）
    """
```

---

## tools 模块详细设计

### 6 个工具的核心实现

| 工具 | 关键逻辑 |
|------|---------|
| `ReadTool` | sandbox.resolve 校验 → open(encoding='utf-8') → 按 offset/limit 切片 → 字节数 > 256KB 截断 |
| `WriteTool` | sandbox.resolve → mkdir(parents=True) → 写入；DANGEROUS |
| `EditTool` | sandbox.resolve → 读全文 → text.count(old) 校验唯一 → text.replace(old, new, 1) → 写回；DANGEROUS；render_confirm_detail 用 difflib.unified_diff |
| `RunTool` | asyncio.create_subprocess_shell（CWD=sandbox.cwd）→ asyncio.wait_for(timeout=60) → 超时则 process.kill()；返回 stdout+stderr+exit_code 拼接文本（>32KB 截断）；DANGEROUS |
| `GlobTool` | sandbox.cwd.rglob(pattern) → 过滤噪声目录 → sorted |
| `SearchTool` | 先用 GlobTool 拿候选文件（默认 `**/*`，可被参数 file_glob 覆盖）→ 逐个 open 读 → re.compile 匹配 → 行号 1-based、单行截断 500 |

### 噪声目录（tools/_noise.py）

```python
NOISE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".tox",
}

def has_noise_part(p: Path, base: Path) -> bool:
    """p 相对 base 的任一段是否是噪声目录或匹配 *.egg-info。"""
```

### 工具超时

| 工具 | 超时 | 实现 |
|------|------|------|
| read / write / edit / glob / search | 30s | 工具内 `asyncio.wait_for` 包裹核心逻辑 |
| run | 60s | 同上 |

写死在工具实现内，不暴露给用户调节（spec N14、不做的事）。

---

## render 层增强

```python
class Renderer:
    # 第一阶段已有：流式渲染、错误、命令回显、用量、abort_streaming

    # 新增方法（沿用朴素 sys.stdout.write 风格）：

    def print_tool_call(self, name: str, summary: str) -> None:
        """灰字一行 '▸ <name>(<summary>)'。"""

    def print_tool_confirm_detail(self, detail: str) -> None:
        """打印 detail 多行（write 内容预览 / edit diff / run 命令）。"""

    def print_tool_result_summary(self, summary: str) -> None:
        """灰字一行简略反馈。"""

    def print_tool_rejected(self, name: str) -> None:
        """灰字一行 '已拒绝执行 <name>'。"""

    def print_usage_combined(self, r1: Usage | None, r2: Usage | None) -> None:
        """累计用量行：'↑ X tokens · ↓ Y tokens'，X、Y 为两次请求合计。
        thinking_tokens 任一非 None 时累计显示。"""
```

确认对话的"等待 y/N"由 Confirmer 用 prompt_toolkit 实现，不在 Renderer 里。

---

## main.py 装配新增

```python
from mewcode.tools import (
    ToolRegistry, register_builtins, Sandbox, Confirmer,
)

# 启动时构造
registry = ToolRegistry()
register_builtins(registry)
sandbox = Sandbox(cwd=Path.cwd())
confirmer = Confirmer()

# 透传给 REPL，进而透传给 chat.run_turn
asyncio.run(run_repl(session, app_config, renderer, registry, sandbox, confirmer))
```

Provider **不持有 registry**：每次 `stream_chat` 时由 chat 把 `registry.to_xxx_format()` 作为参数传入（D6 决策）。

---

## 文件组织

```
mewcode/
├── tools/                         ← 新增
│   ├── __init__.py                ← 暴露 ToolRegistry / Tool / Sandbox / Confirmer / register_builtins
│   ├── base.py                    ← Tool 抽象、ToolResult、DangerLevel
│   ├── registry.py                ← ToolRegistry + register_builtins
│   ├── sandbox.py                 ← Sandbox + PathOutOfSandboxError
│   ├── confirmer.py               ← Confirmer + ConfirmCancelled
│   ├── errors.py                  ← ToolError 系列
│   ├── _noise.py                  ← 噪声目录列表
│   ├── read.py                    ← ReadTool
│   ├── write.py                   ← WriteTool
│   ├── edit.py                    ← EditTool
│   ├── run.py                     ← RunTool
│   ├── glob.py                    ← GlobTool
│   └── search.py                  ← SearchTool
│
├── providers/
│   ├── blocks.py                  ← 新增：ContentBlock 系列
│   ├── events.py                  ← 修改：ToolUseStart/InputDelta/End
│   ├── base.py                    ← 修改：Message.content -> list[ContentBlock]
│   ├── anthropic.py               ← 修改：tools 字段、SSE 工具事件、历史序列化
│   ├── openai.py                  ← 修改：同上
│   └── ...
│
├── chat/
│   ├── session.py                 ← 修改：append_user_text/_assistant/_tool_results
│   └── engine.py                  ← 重写：Round 1 + 工具 + Round 2 编排
│
├── render/
│   └── renderer.py                ← 增加 print_tool_* 方法
│
├── repl/main_loop.py              ← 修改：注入 registry/sandbox/confirmer
└── main.py                        ← 修改：构造对象图

tests/
├── test_tools_read.py             ← 新增
├── test_tools_write.py
├── test_tools_edit.py
├── test_tools_run.py
├── test_tools_glob.py
├── test_tools_search.py
├── test_sandbox.py
├── test_tool_registry.py
├── test_blocks_serialization.py
└── test_chat_round_loop.py        ← stub Provider 验证 Round 1+R2 编排
```

---

## 模块交互时序

### 时序：完整闭环

```
用户 prompt
    │
    ▼
chat.run_turn
    │
    ├── session.append_user_text(prompt)
    │
    ├── _consume_round(R1)
    │   ├── Provider.stream_chat(messages, thinking, tools_format)
    │   ├── async for event:
    │   │     TextDelta      → renderer.push_text + 累积
    │   │     ToolUseStart   → 新建累积块
    │   │     ToolUseInputDelta → 累积 args_buf
    │   │     ToolUseEnd     → 落成 ToolUseBlock
    │   │     Usage          → r1_usage
    │   │     Done           → 结束
    │   └── return (r1_blocks, r1_usage)
    │
    ├── tool_uses = filter(ToolUseBlock, r1_blocks)
    ├── session.append_assistant(r1_blocks)        # R1 整条入历史
    │
    ├── if not tool_uses: print_usage; return True # 退化为 R1 直答
    │
    ├── for tu in tool_uses:                        # 串行执行
    │     renderer.print_tool_call
    │     if dangerous:
    │       renderer.print_tool_confirm_detail
    │       approved = await confirmer.ask(...)
    │       if not approved → 收集 "用户拒绝" tool_result; continue
    │     result = await tool.execute(input, sandbox)
    │     renderer.print_tool_result_summary
    │     收集 ToolResultBlock
    │   except (KeyboardInterrupt | ConfirmCancelled):
    │     回滚 R1 assistant 入历史; return False
    │
    ├── session.append_tool_results(results)
    │
    ├── _consume_round(R2)
    │   └── return (r2_blocks, r2_usage)
    │
    ├── 剥离 r2_blocks 中的 ToolUseBlock → cleaned_blocks
    ├── session.append_assistant(cleaned_blocks)
    ├── if leftover_tools: renderer.print_info(硬停提示)
    └── renderer.print_usage_combined(r1, r2)
```

### 时序：Anthropic SSE 工具调用解析

```
event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{
        "type":"tool_use","id":"toolu_01","name":"read","input":{}}}
    → yield ToolUseStart(id="toolu_01", name="read")
    → buf["toolu_01"] = ""

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{
        "type":"input_json_delta","partial_json":"{\"path\":\"a"}}
    → buf["toolu_01"] += '{"path":"a'
    → yield ToolUseInputDelta(id="toolu_01", json_chunk='{"path":"a')

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{
        "type":"input_json_delta","partial_json":".py\"}"}}
    → buf["toolu_01"] += '.py"}'
    → yield ToolUseInputDelta(...)

event: content_block_stop
data: {"type":"content_block_stop","index":1}
    → input = json.loads(buf["toolu_01"])
    → yield ToolUseEnd(id="toolu_01", name="read", input={"path":"a.py"})
```

### 时序：OpenAI SSE 工具调用解析

```
data: {"choices":[{"delta":{"tool_calls":[
        {"index":0,"id":"call_xxx","type":"function",
         "function":{"name":"read","arguments":""}}]}}]}
    → state[0] = {id:"call_xxx", name:"read", args:""}
    → yield ToolUseStart(id="call_xxx", name="read")

data: {"choices":[{"delta":{"tool_calls":[
        {"index":0,"function":{"arguments":"{\"pa"}}]}}]}
    → state[0]["args"] += '{"pa'
    → yield ToolUseInputDelta(...)

data: {"choices":[{"delta":{"tool_calls":[
        {"index":0,"function":{"arguments":"th\":\"a.py\"}"}}]}}]}
    → state[0]["args"] += 'th":"a.py"}'

data: {"choices":[{"finish_reason":"tool_calls"}]}
    → 遍历 state，每个 index 发：
      ToolUseEnd(id, name, json.loads(args))
```

---

## 技术决策汇总

| #   | 决策点 | 选择 | 理由 |
|-----|--------|------|------|
| D1  | Tool 抽象形态 | ABC + 类属性（name/description/schema）+ 抽象 execute + 可覆盖渲染方法 | 与第一阶段 Provider 的 ABC 模式一致 |
| D2  | 参数 Schema 表示 | 纯 dict（手写 JSON Schema） | 不引入 pydantic；6 个工具 schema 都简单 |
| D3  | Message.content 升级 | str → list[ContentBlock] 破坏性升级 | 协议本身是块结构 |
| D4  | ContentBlock 类型 | Union（Text/Thinking/ToolUse/ToolResult） | 与 StreamEvent Union 哲学一致 |
| D5  | SSE 工具事件粒度 | Start/InputDelta/End 三事件 | InputDelta 暂未被 UI 使用，保留以支持未来扩展 |
| D6  | tools_format 传递 | 通过 stream_chat 参数注入，Provider 不持有 registry | Provider 保持无状态 |
| D7  | Round 1 + Round 2 编排位置 | chat.run_turn | 编排是业务逻辑，不该埋进 Provider |
| D8  | Sandbox 注入方式 | 通过 execute(params, sandbox) 参数注入 | Tool 实例无状态可复用；测试可 mock |
| D9  | Confirmer 与 Renderer 拆分 | Confirmer 独立模块（不进 render） | render 只负责写 stdout；输入由 Confirmer 走 prompt_toolkit |
| D10 | Confirmer 走 prompt_toolkit | 复用 PromptSession | 与主输入框统一处理 Ctrl+C |
| D11 | run 工具实现 | asyncio.create_subprocess_shell + wait_for | 跨平台、原生支持超时；shell=True 让模型可用管道与 redirect |
| D12 | run 输出限制 | stdout+stderr 总 32KB 截断 | 避免大输出污染 token |
| D13 | edit 唯一匹配检查 | text.count(old) == 1 | 简单可靠 |
| D14 | edit diff 渲染 | difflib.unified_diff 文本无色 | 标准库；不引依赖；老 conhost 稳定 |
| D15 | search/glob 实现 | 纯 Python（pathlib + re） | 零外部依赖；中小项目性能足够 |
| D16 | 噪声目录处理 | 写死常量列表 + 路径段匹配 | 简单、可见、易扩展 |
| D17 | 工具超时 | read/write/edit/glob/search 30s；run 60s | 分级写死 |
| D18 | 用户拒绝时 R1 历史处理 | R1 assistant 块仍入历史 + 工具结果包"用户拒绝"入历史 | 协议要求 tool_use 必须有对应 tool_result |
| D19 | 中断时 R1 历史处理 | 回滚 R1 assistant 消息（messages.pop） | 避免协议层"孤儿 tool_use" |
| D20 | Round 2 含 tool_use 硬停 | 剥离 tool_use 块 + 灰字提示 | 用户体验明确；下一轮历史合法 |
| D21 | 用量行展示 | Round 1 + Round 2 累计一行 | 用户感知"这一 turn 总开销" |
| D22 | thinking + tools 兼容 | thinking 块原样保留入历史；R2 请求按协议如实回传 | 防止协议拒绝；signature 字段实施时验证 |
| D23 | 单元测试范围 | 6 工具 + sandbox + registry + 块序列化 + chat 编排（stub Provider） | Provider 工具调用流走端到端验收 |

### 显式不做（plan 层 YAGNI）

- 不引入 pydantic / jsonschema 校验
- 不引入 ripgrep / pathspec 等外部依赖（spec N14）
- 不引入命令白名单 / 超时可配置
- 不抽象 DangerousTool 基类（用 danger_level 字段足够）
- 不做 ToolMiddleware（执行前后钩子）

---

## spec 覆盖核对

| F   | 归属 |
|-----|------|
| F1  | tools/base.py |
| F2  | tools/registry.py |
| F3  | tools/base.py + 各工具内部 try/except |
| F4  | tools/read.py |
| F5  | tools/write.py |
| F6  | tools/edit.py |
| F7  | tools/run.py |
| F8  | tools/glob.py |
| F9  | tools/search.py |
| F10 | tools/sandbox.py |
| F11 | providers/anthropic.py + openai.py + events.py |
| F12 | chat/engine.py 的 _consume_round |
| F13 | chat/engine.py 主循环 + Confirmer |
| F14 | chat/engine.py |
| F15 | chat/engine.py 末段剥离逻辑 |
| F16 | providers/blocks.py + base.py + chat/session.py |
| F17 | providers/anthropic.py + openai.py 的 _serialize_messages_* |
| F18 | ThinkingBlock 保留 + Provider 序列化 |
| F19 | render.Renderer 新方法 |
| F20 | chat/engine.py + render |
| F21 | main.py 启动时无条件构造 ToolRegistry |

依赖图：

```
main → tools, providers, chat, render, repl, config
repl → chat, render, commands
commands → chat
chat → providers, tools (Sandbox, Confirmer 通过参数), render
tools → (无 mewcode 内依赖)
providers → transport
render → rich
transport → httpx
config → PyYAML
```

无环。F1～F21 全部有归属。
