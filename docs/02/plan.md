# MewCode 第一阶段 Plan

> 基于已批准的 `spec.md`。本文档定义架构、数据结构、模块边界、文件组织
> 与关键技术决策。语言相关，按 Python 3.10+ 设计。

## 架构概览

### 设计思路

基于 spec 的核心约束推导：

- **F7 协议级抽象** → 一个 `Provider` 抽象基类 + 两个具体实现
- **F8 扩展点 + N7 协议扩展成本** → 一个**协议分发表**作为唯一注册点
- **F1/F2 配置加载独立** → 配置层与 Provider 层解耦，配置只产出"数据
  结构"，不直接构造 Provider
- **F11 命令识别** → 命令分发与对话流程平级，不嵌入 Provider 也不嵌
  入渲染层
- **N6 模块边界清晰** → 每个职责一个模块，不跨模块访问内部状态

### 架构分层

```
┌─────────────────────────────────────────────────┐
│  入口层 (entry)                                  │
│  - main.py：解析 CLI、读取配置、启动 REPL        │
└─────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────┐
│  REPL 层 (repl)                                  │
│  - REPL 主循环：读输入、分发命令、调用对话引擎    │
│  - prompt_toolkit 输入框 + Ctrl+C 信号处理       │
└─────────────────────────────────────────────────┘
                   ↓
┌──────────────────────┬──────────────────────────┐
│  命令层 (commands)   │  对话引擎层 (chat)        │
│  - 注册表             │  - Session：消息历史 +    │
│  - 各命令处理函数     │    当前 Provider + 思考状态│
│                      │  - 调用 Provider，把流式  │
│                      │    事件转给渲染层          │
└──────────────────────┴──────────────────────────┘
                   ↓                    ↓
┌──────────────────────┐  ┌──────────────────────┐
│  渲染层 (render)      │  Provider 层 (providers)│
│  - rich Console 封装  │  - Provider 抽象基类     │
│  - 普通文本流式渲染   │  - AnthropicProvider     │
│  - Markdown 增量渲染  │  - OpenAIProvider        │
│  - 思考块灰色斜体渲染 │  - 协议分发表             │
│  - 错误红字、用量灰字 │  - StreamEvent 统一事件   │
└──────────────────────┘  └──────────────────────┘
                                ↓
                        ┌──────────────────────┐
                        │  HTTP/SSE 层         │
                        │  (transport)         │
                        │  - httpx 异步流式    │
                        │  - SSE 帧解析        │
                        └──────────────────────┘
                                       ↓
┌─────────────────────────────────────────────────┐
│  配置层 (config)                                 │
│  - YAML 加载、字段校验                           │
│  - ProviderConfig / AppConfig 数据类             │
└─────────────────────────────────────────────────┘
```

### 模块职责一览

| 模块        | 职责                                                | 依赖                |
|-------------|-----------------------------------------------------|---------------------|
| `config`    | 读取 mewcode.yaml，校验，产出 AppConfig             | 仅 PyYAML           |
| `providers` | Provider 抽象 + Anthropic/OpenAI 实现 + 协议分发表  | `transport`         |
| `transport` | httpx 流式 HTTP 客户端 + SSE 帧解析工具             | httpx               |
| `chat`      | Session（消息历史 + 思考状态）、调用 Provider 转事件 | `providers`         |
| `commands`  | 命令注册表、各命令处理函数                          | `chat`、`config`    |
| `render`    | 终端渲染：流式 Markdown、思考块、错误、用量         | rich                |
| `repl`      | REPL 主循环、输入框、Ctrl+C 处理、命令/对话分发     | `commands`、`chat`、`render` |
| `entry`     | main 入口：CLI、加载配置、装配对象图、启动 REPL     | 全部                |

---

## 核心数据结构

### config 模块

```python
from dataclasses import dataclass
from typing import Literal

# 协议类型字面量——新增协议时在这里加一项
Protocol = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class ProviderConfig:
    """单个供应商的配置条目，对应 mewcode.yaml 中 providers 列表的一项。"""
    name: str              # 供应商名（YAML 里的 key）
    protocol: Protocol     # wire protocol
    model: str             # 模型名
    base_url: str          # 请求基础 URL，不含路径
    api_key: str           # 鉴权用，绝不外露


@dataclass(frozen=True)
class AppConfig:
    """整份 mewcode.yaml 解析后的结果。"""
    providers: dict[str, ProviderConfig]   # name -> ProviderConfig
    default: str                            # 启动时使用的供应商名
```

**配置加载契约：**

- 入口函数：从给定路径读取 YAML、校验、返回 `AppConfig`
- 任一字段缺失或非法时抛出**带明确字段名**的 `ConfigError` 子类
- 不读环境变量、不做 fallback、不做默认值填充

### providers 模块（消息与事件）

```python
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    """会话历史中的一条消息。本阶段只支持 user / assistant。"""
    role: Role
    content: str   # 纯文本，不支持多模态、不支持 tool_use/tool_result


# --- 流式事件（Union 类型 + frozen dataclass）---

@dataclass(frozen=True)
class TextDelta:
    """正文增量。"""
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """思考增量。仅 Anthropic + thinking 开启时出现。"""
    text: str


@dataclass(frozen=True)
class Usage:
    """本次调用的 token 用量，流结束前发出一次。后端未返回时不发。"""
    input_tokens: int
    output_tokens: int
    thinking_tokens: int | None = None


@dataclass(frozen=True)
class Done:
    """流正常结束的标记。"""
    pass


StreamEvent = TextDelta | ThinkingDelta | Usage | Done
```

**事件流约定：**

- 顺序：`[ThinkingDelta×N（可选）] → [TextDelta×N] → Usage（可选）→ Done`
- `ThinkingDelta` 全部出现在 `TextDelta` 之前
- `Usage` 在 `Done` 之前最多出现一次
- `Done` 是流正常结束的唯一标志；异常情况通过抛异常表达

### providers 模块（抽象基类与分发表）

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class Provider(ABC):
    """每种 wire protocol 一个具体实现。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    @property
    def protocol(self) -> Protocol:
        return self._config.protocol

    @property
    def model(self) -> str:
        return self._config.model

    @abstractmethod
    def stream_chat(
        self,
        messages: list[Message],
        thinking: bool,
    ) -> AsyncIterator[StreamEvent]:
        """发起流式对话。

        参数：
            messages: 完整会话历史（包含本轮 user 消息）
            thinking: 是否启用 extended thinking；openai 协议下应忽略

        异常：
            ProviderError 及其子类
        """
        ...


# --- 协议分发表（F8 扩展点：新增协议只在此 dict 加一行）---

PROVIDER_REGISTRY: dict[Protocol, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def build_provider(config: ProviderConfig) -> Provider:
    """根据 ProviderConfig.protocol 构造对应 Provider 实例。"""
    cls = PROVIDER_REGISTRY[config.protocol]
    return cls(config)
```

### chat 模块

```python
@dataclass
class Session:
    """单次 MewCode 进程内的会话状态。可变。"""
    provider: Provider
    messages: list[Message] = field(default_factory=list)
    thinking_enabled: bool = False     # 默认关闭

    def append_user(self, text: str) -> None: ...
    def append_assistant(self, text: str) -> None: ...
    def clear(self) -> None: ...
    def switch_provider(self, provider: Provider) -> None: ...
```

`Session` 是整个 REPL 的"状态容器"。命令层、REPL 层都通过它读写状态。

---

## 异常体系

### Provider 层错误

```python
class ProviderError(Exception):
    """Provider 层错误的基类。REPL 捕获时打印红字、不重试、回到提示符。"""
    category: str = "Provider 错误"


class NetworkError(ProviderError):
    """网络层错误：连接、超时、DNS。"""
    category = "网络错误"


class HTTPStatusError(ProviderError):
    """HTTP 非 2xx。包含状态码与脱敏的响应体片段。"""
    category = "HTTP 错误"

    def __init__(self, status_code: int, body_snippet: str) -> None:
        super().__init__(f"HTTP {status_code}: {body_snippet}")
        self.status_code = status_code
        self.body_snippet = body_snippet


class AuthError(HTTPStatusError):
    """鉴权失败（401/403）。"""
    category = "鉴权失败"


class StreamParseError(ProviderError):
    """SSE 帧或事件结构非预期。"""
    category = "流解析错误"
```

### 配置层错误（独立体系）

```python
class ConfigError(Exception):
    category: str = "配置错误"


class ConfigFileNotFound(ConfigError):
    category = "配置文件不存在"


class ConfigFormatError(ConfigError):
    """YAML 解析失败或顶层结构非法。"""
    category = "配置格式错误"


class ConfigFieldError(ConfigError):
    """字段缺失、非法、default 指向不存在的供应商。"""
    category = "配置字段错误"
```

**异常处理位置：**

- `ConfigError` 由入口层捕获 → 红字打印 → 进程非 0 退出（AC2、AC3）
- `ProviderError` 由 chat.run_turn 捕获 → 红字打印 → 回到输入提示符（AC20）
- 其他未预期异常向上传播至顶层 catch-all → 打印堆栈 → 退出码 2

---

## 模块设计

### transport 模块

#### sse.py

```python
@dataclass(frozen=True)
class SSEFrame:
    event: str | None    # event: 字段
    data: str            # data: 字段拼接后的文本


async def iter_sse_frames(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[SSEFrame]:
    """把字节流切分成 SSE 帧。

    规则简化版（覆盖 Anthropic / OpenAI）：
    - 帧之间以空行分隔
    - 行前缀 'event: '、'data: ' 分别填入字段
    - 多行 data 合并为换行连接
    - 忽略以 ':' 开头的注释行
    """
```

#### http_client.py

```python
async def stream_post(
    url: str,
    headers: dict[str, str],
    json_body: dict,
    timeout: float = 60.0,
) -> AsyncIterator[bytes]:
    """对给定 URL 发起流式 POST，按 chunk 产出字节。

    抛出：
        NetworkError: 连接/超时/DNS
        HTTPStatusError: 状态码非 2xx
        AuthError: 状态码 401/403
    """
```

### providers 模块（具体实现）

#### AnthropicProvider

**端点**：`POST {base_url}/v1/messages`

**请求头**：
```
x-api-key: <api_key>
anthropic-version: 2023-06-01
content-type: application/json
```

**请求体（thinking 关闭）**：
```json
{
  "model": "<model>",
  "max_tokens": 8192,
  "stream": true,
  "messages": [{"role": "user", "content": "..."}, ...]
}
```

**请求体（thinking 开启时追加）**：
```json
"thinking": {"type": "enabled", "budget_tokens": 4096}
```

**SSE 事件 → StreamEvent 映射：**

| Anthropic SSE event                              | 映射                              |
|--------------------------------------------------|-----------------------------------|
| `message_start`（含 input_tokens）              | 记录 input_tokens                 |
| `content_block_delta` (thinking_delta)           | `ThinkingDelta(text=...)`         |
| `content_block_delta` (text_delta)               | `TextDelta(text=...)`             |
| `message_delta`（含 output_tokens）             | 累积 output/thinking tokens       |
| `message_stop`                                   | 发 `Usage`（如有）+ `Done`        |
| 其他（`ping`、`content_block_start/stop`）       | 忽略                              |

**思考 token 字段名**：实现 T2/T3 时打印一次原始 SSE 帧确认（不同 API
版本字段名微调过）。

#### OpenAIProvider

**端点**：`POST {base_url}/v1/chat/completions`

**请求头**：
```
Authorization: Bearer <api_key>
content-type: application/json
```

**请求体**：
```json
{
  "model": "<model>",
  "stream": true,
  "stream_options": {"include_usage": true},
  "messages": [{"role": "user", "content": "..."}, ...]
}
```

**SSE 数据 → StreamEvent 映射：**

| data 内容                                          | 映射                         |
|----------------------------------------------------|------------------------------|
| `{"choices":[{"delta":{"content":"..."}}]}`        | `TextDelta(text=...)`        |
| `{"usage":{"prompt_tokens":N,"completion_tokens":M}}` | `Usage(input=N,output=M)` |
| `data: [DONE]`                                     | 发 `Done`                    |

**thinking 参数处理**：构造请求体时无视 `thinking` 参数。F9 已规定在
用户尝试 `/think on` 时由命令层提示，OpenAIProvider 自身不需要做事。

### chat 模块

```python
async def run_turn(
    session: Session,
    user_input: str,
    renderer: Renderer,
) -> bool:
    """跑一轮对话。

    流程：
    1. session.append_user(user_input)        # 用户消息无条件入历史
    2. stream = session.provider.stream_chat(messages, thinking_enabled)
    3. async for event in stream:
         - ThinkingDelta → renderer.begin/push_thinking
         - TextDelta     → renderer.begin/push_text（首次切换时关思考）
         - Usage         → 暂存
         - Done          → 跳出循环
    4. 正常结束：
         - renderer.end_assistant
         - session.append_assistant(累计正文)
         - 若有 Usage → renderer.print_usage
         - 返回 True
    5. 捕获 KeyboardInterrupt：
         - renderer.abort_streaming
         - 不调用 append_assistant（N5）
         - 返回 False
    6. 捕获 ProviderError：
         - renderer.abort_streaming
         - renderer.print_error
         - 返回 False
    """
```

**关键约定：**

- user_input 在调用 Provider 之**前**就进入历史
- KeyboardInterrupt / ProviderError 在 chat 层闭环处理，不上抛 REPL
  主循环

### commands 模块

```python
@dataclass(frozen=True)
class Command:
    name: str                                # 不含 / 前缀
    aliases: tuple[str, ...]
    description: str
    handler: Callable[[CommandContext], Awaitable[CommandResult]]


@dataclass
class CommandContext:
    session: Session
    app_config: AppConfig
    args: list[str]
    renderer: Renderer


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False


COMMANDS: dict[str, Command] = {}     # name/alias -> Command


def register(cmd: Command) -> None:
    COMMANDS[cmd.name] = cmd
    for alias in cmd.aliases:
        COMMANDS[alias] = cmd


async def dispatch(line: str, ctx: CommandContext) -> CommandResult | None:
    """返回 None：不是命令；返回 CommandResult：是命令（含未知命令）。"""
```

**七个内置命令的 handler：**

| 命令              | handler 行为                                           |
|-------------------|--------------------------------------------------------|
| `/exit`, `/quit`  | 返回 `CommandResult(should_exit=True)`                |
| `/help`           | renderer 打印命令清单                                  |
| `/clear`          | `session.clear()`，提示"会话历史已清空"               |
| `/think on`       | 协议非 anthropic 时提示"当前协议不支持思考"；否则置位 |
| `/think off`      | `session.thinking_enabled = False`，提示已关闭        |
| `/providers`      | 列出 app_config.providers，标记当前生效项             |
| `/provider <name>`| 校验 → build_provider → switch_provider（清空历史）→ 提示切换结果 |

### render 模块

```python
class Renderer:
    """终端渲染器。所有写终端的语义化方法集中在此。"""

    def __init__(self, console: Console) -> None: ...

    # --- 启动/横幅 ---
    def print_banner(self, provider_name: str, protocol: str, model: str) -> None: ...
    def print_help_hint(self, commands: list[str]) -> None: ...

    # --- 流式正文（Markdown 增量重绘）---
    def begin_assistant(self) -> None: ...
    def push_text(self, text: str) -> None: ...
    def end_assistant(self) -> None: ...

    # --- 流式思考（灰色斜体）---
    def begin_thinking(self) -> None: ...
    def push_thinking(self, text: str) -> None: ...
    def end_thinking(self) -> None: ...

    # --- 末尾信息 ---
    def print_usage(self, usage: Usage) -> None: ...

    # --- 命令回显 ---
    def print_info(self, text: str) -> None: ...
    def print_command_list(self, commands: list[Command]) -> None: ...
    def print_provider_list(self, ...) -> None: ...
    def print_unknown_command(self, name: str, available: list[str]) -> None: ...

    # --- 错误 ---
    def print_error(self, category: str, message: str) -> None: ...

    # --- 中断收尾 ---
    def abort_streaming(self) -> None: ...
```

**实现要点：**

- `begin_assistant` 创建 `Live(Markdown(""), refresh_per_second=10)`
- `push_text` 累加 buffer 后调 `Live.update(Markdown(buffer))`
- 思考块走纯文本 Live + `dim italic` 样式 + `▎思考中…` 起始标记

### repl 模块

```python
async def run_repl(session: Session, app_config: AppConfig, renderer: Renderer) -> int:
    """REPL 主循环。返回进程退出码。

    流程：
    1. renderer.print_banner / print_help_hint
    2. 创建 PromptSession（自带方向键历史）
    3. with patch_stdout(): 循环
       a. line = await pt_session.prompt_async("> ")
          - EOFError → 当 /exit
          - KeyboardInterrupt 第一次 → 提示+置位 _ctrl_c_pending
          - KeyboardInterrupt 第二次（_ctrl_c_pending=True）→ return 0
          - 成功输入 → 清零 _ctrl_c_pending
       b. 空白行 → continue
       c. result = await commands.dispatch(line, ctx)
          - 非命令 → result is None → 落到 d
          - 命令 → 若 should_exit return 0；否则 continue
       d. await chat.run_turn(session, line, renderer)
    """
```

### entry 模块（main.py）

```python
def main() -> int:
    console = Console()
    renderer = Renderer(console)

    try:
        app_config = config.load("mewcode.yaml")
    except ConfigError as e:
        renderer.print_error(e.category, str(e))
        return 1

    try:
        provider = providers.build_provider(
            app_config.providers[app_config.default]
        )
        session = Session(provider=provider)
        return asyncio.run(run_repl(session, app_config, renderer))
    except Exception:
        console.print_exception()
        return 2
```

**退出码语义：**

- `0` 正常退出
- `1` 配置错误
- `2` 未预期异常

---

## 文件组织

```
mecode/
├── mewcode/                              ← Python 包根
│   ├── __init__.py                       ← 包元信息
│   ├── __main__.py                       ← 支持 python -m mewcode
│   ├── main.py                           ← entry：main() 函数
│   │
│   ├── config/
│   │   ├── __init__.py                   ← 暴露 load、AppConfig 等
│   │   ├── models.py                     ← AppConfig、ProviderConfig
│   │   ├── loader.py                     ← load(path) 函数
│   │   └── errors.py                     ← ConfigError 系列
│   │
│   ├── providers/
│   │   ├── __init__.py                   ← 暴露 Provider、build_provider 等
│   │   ├── base.py                       ← Provider 抽象基类、Message
│   │   ├── events.py                     ← StreamEvent 联合类型
│   │   ├── errors.py                     ← ProviderError 系列
│   │   ├── registry.py                   ← PROVIDER_REGISTRY、build_provider
│   │   ├── anthropic.py                  ← AnthropicProvider
│   │   └── openai.py                     ← OpenAIProvider
│   │
│   ├── transport/
│   │   ├── __init__.py
│   │   ├── http_client.py                ← httpx 流式 POST
│   │   └── sse.py                        ← SSE 帧解析
│   │
│   ├── chat/
│   │   ├── __init__.py                   ← 暴露 Session、run_turn
│   │   ├── session.py                    ← Session 数据类及方法
│   │   └── engine.py                     ← run_turn 协程
│   │
│   ├── commands/
│   │   ├── __init__.py                   ← 暴露 dispatch、register_builtins
│   │   ├── registry.py                   ← Command、Context、Result、dispatch
│   │   └── builtin.py                    ← 七个内置命令的 handler
│   │
│   ├── render/
│   │   ├── __init__.py
│   │   └── renderer.py                   ← Renderer 类
│   │
│   └── repl/
│       ├── __init__.py
│       └── main_loop.py                  ← run_repl 协程
│
├── tests/                                ← 单元测试
│   ├── __init__.py
│   ├── test_config_loader.py
│   ├── test_sse_parser.py
│   ├── test_provider_registry.py
│   └── test_command_dispatch.py
│
├── docs/
│   └── 02/                               ← 本阶段四份文档
│       ├── spec.md
│       ├── plan.md
│       ├── task.md
│       └── checklist.md
│
├── .skills/
│   └── mew-spec/
│       └── SKILL.md
│
├── mewcode.yaml.example                  ← 配置文件样例
├── pyproject.toml                        ← 项目元信息 + console_scripts
├── README.md                             ← 简要使用说明
└── .gitignore                            ← 忽略 mewcode.yaml、__pycache__、.venv
```

**关键点：**

- 每个模块一个 `__init__.py` 明确声明对外暴露什么——模块边界由 import
  路径强制（N6）
- 真实的 `mewcode.yaml` 进 `.gitignore`（含 api_key）
- `pyproject.toml` 提供 `mewcode` 命令行入口

---

## 模块交互时序

### 时序 1：启动流程

```
main()
   ├─→ Renderer(Console())
   │
   ├─→ config.load("mewcode.yaml")
   │       ├─ 读 YAML → 校验顶层 → 校验每个 provider → 返回 AppConfig
   │       ↓ 失败 → renderer.print_error → return 1
   │
   ├─→ providers.build_provider(app_config.providers[default])
   │       └─ PROVIDER_REGISTRY[protocol] 查类 → 构造实例
   │
   ├─→ Session(provider=...)
   │
   └─→ asyncio.run(run_repl(...))
           ├─ renderer.print_banner / print_help_hint
           └─ patch_stdout 包裹的 while 循环
```

### 时序 2：一轮对话流程

```
REPL 主循环（一次迭代）
    ├─→ line = await pt_session.prompt_async("> ")
    │       ↓ KeyboardInterrupt → 双击退出处理
    │       ↓ EOFError → 当 /exit
    │
    ├─→ if 空白: continue
    │
    ├─→ result = await commands.dispatch(line, ctx)
    │       ↓ 不是命令 → return None
    │       ↓ 命令存在 → 执行 handler → return CommandResult
    │       ↓ 未知命令 → renderer.print_unknown → return CommandResult()
    │   if result is not None:
    │       if result.should_exit: return 0
    │       continue
    │
    └─→ await chat.run_turn(session, line, renderer)
```

### 时序 3：run_turn 内部

```
run_turn(session, user_input, renderer)
    ├─→ session.append_user(user_input)
    ├─→ assistant_buf = ""; pending_usage = None; in_thinking = False
    │
    ├─→ try:
    │     stream = session.provider.stream_chat(messages, thinking)
    │     async for event in stream:
    │       match event:
    │         case ThinkingDelta(text):
    │           if not in_thinking:
    │             renderer.begin_thinking(); in_thinking = True
    │           renderer.push_thinking(text)
    │         case TextDelta(text):
    │           if in_thinking:
    │             renderer.end_thinking(); in_thinking = False
    │           if assistant_buf == "":
    │             renderer.begin_assistant()
    │           assistant_buf += text
    │           renderer.push_text(text)
    │         case Usage() as u:
    │           pending_usage = u
    │         case Done():
    │           break
    │
    │     if assistant_buf:
    │       renderer.end_assistant()
    │       session.append_assistant(assistant_buf)
    │     if pending_usage:
    │       renderer.print_usage(pending_usage)
    │     return True
    │
    │ except KeyboardInterrupt:
    │     renderer.abort_streaming()
    │     return False                  # 不进历史（N5）
    │
    │ except ProviderError as e:
    │     renderer.abort_streaming()
    │     renderer.print_error(e.category, str(e))
    │     return False
```

### 时序 4：AnthropicProvider.stream_chat

```
AnthropicProvider.stream_chat(messages, thinking)
    ├─→ url = f"{base_url}/v1/messages"
    │   headers = {x-api-key, anthropic-version, content-type}
    │   body = {model, max_tokens, stream, messages, thinking?}
    │
    ├─→ byte_stream = transport.stream_post(url, headers, body)
    │       ↓ NetworkError / HTTPStatusError / AuthError → 直接传出
    │
    ├─→ async for frame in transport.iter_sse_frames(byte_stream):
    │     try: data_obj = json.loads(frame.data)
    │     except JSONDecodeError: raise StreamParseError(...)
    │
    │     match (frame.event, data_obj):
    │       case ("message_start", {"message":{"usage":{"input_tokens": n}}}):
    │           input_tokens = n
    │       case ("content_block_delta", {"delta":{"type":"thinking_delta","thinking": t}}):
    │           yield ThinkingDelta(t)
    │       case ("content_block_delta", {"delta":{"type":"text_delta","text": t}}):
    │           yield TextDelta(t)
    │       case ("message_delta", {"usage":{"output_tokens": n, ...}}):
    │           output_tokens = n
    │       case ("message_stop", _):
    │           yield Usage(input_tokens, output_tokens, thinking_tokens)
    │           yield Done()
    │           return
    │       case _:
    │           pass     # 忽略 ping、content_block_start/stop
```

---

## 技术决策汇总

| #   | 决策点              | 选择                                | 理由                                                              |
|-----|---------------------|-------------------------------------|-------------------------------------------------------------------|
| D1  | Python 版本下限     | 3.10                                | PEP 604 联合类型、PEP 634 match-case 大量使用（N10）              |
| D2  | 同步 vs 异步        | asyncio 全异步                      | SSE 必须非阻塞 IO；prompt_toolkit 原生支持 asyncio（N1、N2）      |
| D3  | TUI 库              | prompt_toolkit + rich               | spec Q1 选 A；事实标准组合                                         |
| D4  | YAML 库             | PyYAML                              | 生态最广；safe_load 防任意类反序列化                              |
| D5  | HTTP 客户端         | httpx                               | 原生流式 aiter_bytes、超时控制粒度足                              |
| D6  | 不引入官方 SDK      | 自实现 SSE                          | spec N11 强约束；适配 DeepSeek 借壳 Anthropic 协议等场景          |
| D7  | Provider 抽象粒度   | 协议级（按 wire protocol）          | spec 方案 1；与 protocol 字段一一对应                              |
| D8  | 协议分发机制        | 全局 dict + build_provider 工厂     | 满足 N7：新增协议 = 新文件 + 注册表加一行                         |
| D9  | 事件流类型          | Union + frozen dataclass            | 比继承扁平；配合 match-case；字段固定可校验                       |
| D10 | 流结束信号          | 显式 Done 事件                      | 比依赖 StopAsyncIteration 语义更明确                              |
| D11 | 错误体系切分        | ConfigError vs ProviderError 独立   | 配置错误致命，Provider 错误可恢复；处理位置不同                   |
| D12 | thinking 参数默认值 | budget_tokens=4096, max_tokens=8192 | 写死在 Provider 内部；不暴露给用户调节（YAGNI）                   |
| D13 | Markdown 流式渲染   | rich Live + 每 chunk update         | rich 标准做法；refresh_per_second=10 经验值                       |
| D14 | 思考块样式          | dim italic + ▎思考中… 起始标记    | 终端语义"次要内容"通用表达                                        |
| D15 | Ctrl+C 双语义       | prompt 阶段在 REPL 处理；流式阶段在 chat 处理 | 各处理点最自然，主循环不需双重 try                          |
| D16 | prompt_toolkit + rich 协作 | patch_stdout 上下文          | 标准做法，避免 Live 与输入框互相覆盖                              |
| D17 | 命令分发模型        | 注册表 + dataclass Command + 别名   | 易于在第二阶段扩展；别名让 /exit 与 /quit 共用 handler            |
| D18 | 渲染器形态          | 单一胖子类 Renderer                 | 让其他模块只依赖一个对象；更换终端后端时改一处                    |
| D19 | 配置文件位置        | 项目级 mewcode.yaml                 | spec Q2 选 B；不读用户级、不读环境变量                            |
| D20 | 敏感字段保护        | api_key 不出现在错误/日志/用量行    | spec N9；HTTPStatusError.body_snippet 需脱敏截断                  |
| D21 | 退出码语义          | 0=正常 / 1=配置错误 / 2=未预期      | 标准 Unix 约定；便于 CI 与 tmux 验收脚本断言                      |
| D22 | 单测范围            | 仅纯逻辑（SSE 解析、配置、命令分发）| Provider 端到端走 tmux 验收（AC23）；不做 mock 服务器（YAGNI）    |

### 显式不做（plan 层 YAGNI）

- 不引入 logging 框架（debug 用 print 即可）
- 不引入配置 schema 验证库（pydantic / marshmallow）—— 手写校验 < 30 行
- 不做请求/响应中间件层 —— 现在只 2 个 Provider，加抽象是过早优化
- 不做命令自动补全 —— prompt_toolkit 支持，但 spec 没要求

---

## spec 覆盖核对

| F   | 归属模块                                                              |
|-----|-----------------------------------------------------------------------|
| F1  | `config/loader.py`                                                    |
| F2  | `config/models.py` + `config/loader.py`                               |
| F3  | `render.Renderer.print_banner` + `repl.run_repl` 启动调用             |
| F4  | `repl/main_loop.py` 的 `pt_session.prompt_async`                      |
| F5  | `render.Renderer.begin/push/end_assistant` + chat.run_turn 驱动       |
| F6  | `chat/session.py` 的 `Session.messages`                               |
| F7  | `providers/base.py` + `anthropic.py` + `openai.py`                    |
| F8  | `providers/registry.py` 的 `PROVIDER_REGISTRY` + `build_provider`     |
| F9  | `Session.thinking_enabled` + `commands/builtin.py` 的 `/think` handler |
| F10 | `render.Renderer.begin/push/end_thinking` + AnthropicProvider 解析    |
| F11 | `commands/registry.py` 的 `dispatch`                                  |
| F12 | `commands/builtin.py` 七个 handler                                     |
| F13 | `render.Renderer.print_usage` + chat.run_turn 收 Usage                |
| F14 | `providers/errors.py` + chat.run_turn try/except + `print_error`      |

依赖图（→ 表示"依赖"）：

```
main → config, providers, chat, commands, render, repl
repl → commands, chat, render
commands → chat, config, render
chat → providers, render
providers → transport
render → rich
transport → httpx
config → PyYAML
```

无环。F1~F14 全部有架构归属。
