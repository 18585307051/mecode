# MewCode 第一阶段 Tasks

> 基于已批准的 `spec.md` 和 `plan.md`。共 21 个任务，覆盖项目骨架、
> 配置、传输、Provider、渲染、会话、命令、REPL、入口、端到端验收。

## 文件清单

| 操作 | 文件                                       | 职责                                |
|------|--------------------------------------------|-------------------------------------|
| 新建 | `pyproject.toml`                           | 项目元信息、依赖、`mewcode` 命令入口 |
| 新建 | `.gitignore`                               | 忽略 mewcode.yaml、缓存等           |
| 新建 | `README.md`                                | 简要使用说明                        |
| 新建 | `mewcode.yaml.example`                     | 配置文件样例（双供应商）            |
| 新建 | `mewcode.yaml`                             | 真实配置（不进 git）                |
| 新建 | `mewcode/__init__.py`                      | 包元信息                            |
| 新建 | `mewcode/__main__.py`                      | 支持 `python -m mewcode`            |
| 新建 | `mewcode/main.py`                          | entry：main() 函数                  |
| 新建 | `mewcode/config/__init__.py`               | 模块出口                            |
| 新建 | `mewcode/config/errors.py`                 | ConfigError 系列                    |
| 新建 | `mewcode/config/models.py`                 | AppConfig、ProviderConfig           |
| 新建 | `mewcode/config/loader.py`                 | load(path) 函数                     |
| 新建 | `mewcode/transport/__init__.py`            | 模块出口                            |
| 新建 | `mewcode/transport/sse.py`                 | SSE 帧解析                          |
| 新建 | `mewcode/transport/http_client.py`         | httpx 流式 POST                     |
| 新建 | `mewcode/providers/__init__.py`            | 模块出口                            |
| 新建 | `mewcode/providers/errors.py`              | ProviderError 系列                  |
| 新建 | `mewcode/providers/events.py`              | StreamEvent 联合类型                |
| 新建 | `mewcode/providers/base.py`                | Provider 抽象基类、Message          |
| 新建 | `mewcode/providers/anthropic.py`           | AnthropicProvider                   |
| 新建 | `mewcode/providers/openai.py`              | OpenAIProvider                      |
| 新建 | `mewcode/providers/registry.py`            | PROVIDER_REGISTRY、build_provider   |
| 新建 | `mewcode/render/__init__.py`               | 模块出口                            |
| 新建 | `mewcode/render/renderer.py`               | Renderer 类                         |
| 新建 | `mewcode/chat/__init__.py`                 | 模块出口                            |
| 新建 | `mewcode/chat/session.py`                  | Session 数据类                      |
| 新建 | `mewcode/chat/engine.py`                   | run_turn 协程                       |
| 新建 | `mewcode/commands/__init__.py`             | 模块出口                            |
| 新建 | `mewcode/commands/registry.py`             | Command/Context/Result/dispatch     |
| 新建 | `mewcode/commands/builtin.py`              | 七个内置命令 handler                |
| 新建 | `mewcode/repl/__init__.py`                 | 模块出口                            |
| 新建 | `mewcode/repl/main_loop.py`                | run_repl 协程                       |
| 新建 | `tests/__init__.py`                        | 测试包初始化                        |
| 新建 | `tests/test_config_loader.py`              | 配置加载单测                        |
| 新建 | `tests/test_sse_parser.py`                 | SSE 解析单测                        |
| 新建 | `tests/test_provider_registry.py`          | 协议分发单测                        |
| 新建 | `tests/test_command_dispatch.py`           | 命令分发单测                        |

---

## 任务执行顺序图

```
T1 ──┬─→ T2 ──────────────────────────────────────────┐
     ├─→ T3 ──→ T4                                    │
     ├─→ T5                                           │
     ├─→ T7 ──┬─→ T6                                  │
     │        ├─→ T8 ──┬─→ T9 ──→ T10 ──┐             │
     │        │        └─→ T11 ─────────┤             │
     │        └─→ T14 ──┐               │             │
     │                  ├─→ T15 ────────┼─→ T17 ──→ T18 ──→ T19 ──→ T20 ──→ T21
     ├─→ T12 ──→ T13 ──┘                │             │
     └─→ T16 ←──────────────────────────┘             │
                                                      │
       T2 ───────────────────────────────────────────→┘ (T19 依赖 T2)
```

**关键路径（最长串行）**：
T1 → T7 → T8 → T9 → T10 → T15 → T17 → T18 → T19 → T20 → T21（11 步）

**可并行机会**：
- T2 / T3-T4 / T5 / T7 在 T1 后立即可并行
- T9 与 T11 在 T8 后可并行
- T12-T13 与 T7-T8-T9-T10 可并行

---

## T1：初始化项目骨架

**文件：**
- 新建 `pyproject.toml`
- 新建 `.gitignore`
- 新建 `README.md`
- 新建 `mewcode/__init__.py`
- 新建 `mewcode/__main__.py`
- 新建空 `__init__.py`：`mewcode/config/`、`mewcode/providers/`、`mewcode/transport/`、`mewcode/chat/`、`mewcode/commands/`、`mewcode/render/`、`mewcode/repl/`、`tests/`

**依赖：** 无

**步骤：**

1. 创建 `pyproject.toml`，使用 setuptools 后端：
   - `name = "mewcode"`、`version = "0.1.0"`、`requires-python = ">=3.10"`
   - `dependencies = ["prompt_toolkit>=3.0", "rich>=13.0", "PyYAML>=6.0", "httpx>=0.27"]`
   - `[project.scripts] mewcode = "mewcode.main:main"`
   - `[project.optional-dependencies] dev = ["pytest>=7", "pytest-asyncio>=0.21"]`
   - `[tool.setuptools.packages.find] where = ["."]; include = ["mewcode*"]`
2. 创建 `.gitignore`：`mewcode.yaml`、`__pycache__/`、`.venv/`、`*.egg-info/`、`dist/`、`build/`、`.pytest_cache/`
3. 创建 `README.md`：项目名、安装命令（`pip install -e .`）、启动命令（`mewcode`）、配置参考 `mewcode.yaml.example`
4. 创建 `mewcode/__init__.py`：`__version__ = "0.1.0"`
5. 创建 `mewcode/__main__.py`：`from mewcode.main import main; import sys; sys.exit(main())`
6. 在 8 个子模块目录下创建空 `__init__.py`（暂不填出口，后续任务回填）

**验证：**
- 项目根执行 `pip install -e .` 安装成功
- `python -c "import mewcode; print(mewcode.__version__)"` 输出 `0.1.0`
- `python -m mewcode` 报错"找不到 main"——预期，因为 `main.py` 还没写

---

## T2：mewcode.yaml.example

**文件：**
- 新建 `mewcode.yaml.example`

**依赖：** T1

**步骤：**

1. 创建样例配置（spec F2 结构）：
   ```yaml
   # MewCode 配置文件样例
   # 复制本文件为 mewcode.yaml 并填入真实 api_key 后使用
   # mewcode.yaml 已在 .gitignore 中，不会被提交

   default: deepseek-anthropic

   providers:
     deepseek-anthropic:
       protocol: anthropic
       model: deepseek-v4-pro[1m]
       base_url: https://api.deepseek.com/anthropic
       api_key: sk-your-key-here

     deepseek-openai:
       protocol: openai
       model: deepseek-chat
       base_url: https://api.deepseek.com
       api_key: sk-your-key-here
   ```
2. 顶部注释说明四个核心字段含义和 `default` 的作用

**验证：**
- `python -c "import yaml; print(yaml.safe_load(open('mewcode.yaml.example')))"` 输出 dict，含 `default` 和 `providers`

---

## T3：config 数据模型与异常

**文件：**
- 新建 `mewcode/config/errors.py`
- 新建 `mewcode/config/models.py`
- 修改 `mewcode/config/__init__.py`

**依赖：** T1

**步骤：**

1. 在 `errors.py` 中定义 4 个异常类：
   - `ConfigError(Exception)`，`category: str = "配置错误"`
   - `ConfigFileNotFound(ConfigError)`，`category = "配置文件不存在"`
   - `ConfigFormatError(ConfigError)`，`category = "配置格式错误"`
   - `ConfigFieldError(ConfigError)`，`category = "配置字段错误"`
   - 每个类前加中文 docstring 说明触发场景
2. 在 `models.py` 中定义：
   - `Protocol = Literal["anthropic", "openai"]`
   - `@dataclass(frozen=True) class ProviderConfig`：5 个字段 `name / protocol / model / base_url / api_key`
   - `@dataclass(frozen=True) class AppConfig`：`providers: dict[str, ProviderConfig]`、`default: str`
3. 在 `__init__.py` 中暴露：`AppConfig`、`ProviderConfig`、`Protocol`、4 个异常类、`load`（先用 `from .loader import load`，T4 实现）

**验证：**
- `python -c "from mewcode.config import AppConfig, ProviderConfig, ConfigError; print('ok')"`
- `python -c "from mewcode.config.models import ProviderConfig; ProviderConfig(name='x', protocol='anthropic', model='m', base_url='u', api_key='k')"` 不报错

---

## T4：config.load 实现 + 单测

**文件：**
- 新建 `mewcode/config/loader.py`
- 新建 `tests/test_config_loader.py`

**依赖：** T3

**步骤：**

1. 在 `loader.py` 中实现 `def load(path: str | Path) -> AppConfig`：
   - 步骤 a：`path` 不存在 → 抛 `ConfigFileNotFound(f"未找到配置文件: {path}")`
   - 步骤 b：`yaml.safe_load` 失败或返回非 dict → 抛 `ConfigFormatError`
   - 步骤 c：顶层缺 `default` 或 `providers` → 抛 `ConfigFieldError`，错误信息含字段名
   - 步骤 d：`providers` 不是 dict 或为空 → 抛 `ConfigFieldError`
   - 步骤 e：遍历每个 provider 项，校验 4 个核心字段非空字符串，`protocol` 为 `"anthropic"` 或 `"openai"`；非法时抛 `ConfigFieldError(f"供应商 '{name}' 缺少字段 '{field}'")`
   - 步骤 f：`default` 必须存在于 `providers` keys → 否则抛 `ConfigFieldError(f"default 指向不存在的供应商: {default}")`
   - 步骤 g：构造 ProviderConfig（YAML key 作为 `name`），返回 `AppConfig`
2. 在 `tests/test_config_loader.py` 中写测试（用 `tmp_path` fixture）：
   - `test_load_合法配置成功`
   - `test_文件不存在`
   - `test_yaml格式错误`
   - `test_缺失default字段`
   - `test_protocol非法`
   - `test_default指向不存在的供应商`

**验证：**
- `pytest tests/test_config_loader.py -v` 全部通过
- `python -c "from mewcode.config import load; print(load('mewcode.yaml.example'))"` 正常输出

---

## T5：SSE 帧解析 + 单测

**文件：**
- 新建 `mewcode/transport/sse.py`
- 新建 `tests/test_sse_parser.py`
- 修改 `mewcode/transport/__init__.py`

**依赖：** T1

**步骤：**

1. 在 `sse.py` 中定义：
   - `@dataclass(frozen=True) class SSEFrame`：`event: str | None`、`data: str`
   - `async def iter_sse_frames(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[SSEFrame]`：
     - 维护 bytes 缓冲区
     - 按 `\n\n` 切分帧块（不完整片段留缓冲区）
     - 行 `event: xxx` → 填 event；行 `data: xxx` → 累加 data（多行用 `\n` 连接）
     - 跳过空行和以 `:` 开头的注释
     - 处理 `\r\n` 与 `\n`
     - bytes 解码 `utf-8`
2. 在 `tests/test_sse_parser.py` 中（pytest-asyncio）：
   - `test_单帧基础`：`b"event: foo\ndata: hello\n\n"` → 1 帧 event=foo data=hello
   - `test_无event字段`：`b"data: hello\n\n"` → frame.event 为 None
   - `test_多行data`：`b"data: line1\ndata: line2\n\n"` → data=`line1\nline2`
   - `test_注释行被忽略`：`b": comment\ndata: ok\n\n"` → 1 帧
   - `test_chunk边界跨帧`：`b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"` 拆 3 chunk → 2 帧
   - `test_data为DONE`：`b"data: [DONE]\n\n"` → data=`[DONE]`
3. 在 `__init__.py` 暴露 `SSEFrame`、`iter_sse_frames`

**验证：**
- `pytest tests/test_sse_parser.py -v` 全部通过

---

## T6：httpx 流式 POST

**文件：**
- 新建 `mewcode/transport/http_client.py`
- 修改 `mewcode/transport/__init__.py`

**依赖：** T5、T7（需要 ProviderError 系列）

**步骤：**

1. 在 `http_client.py` 中实现 `async def stream_post(url, headers, json_body, timeout=60.0) -> AsyncIterator[bytes]`：
   - `async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout)) as client`
   - `async with client.stream("POST", url, headers=headers, json=json_body) as resp`
   - 401/403 → 读 200 字节 snippet → 抛 `AuthError(status_code, snippet)`
   - 其他 4xx/5xx → 抛 `HTTPStatusError(status_code, snippet)`
   - 2xx → `async for chunk in resp.aiter_bytes(): yield chunk`
   - 顶层 `try` 捕获 `httpx.RequestError` → 抛 `NetworkError(str(e))` 链式
2. 内部辅助 `async def _read_body_snippet(resp, limit=200) -> str`：读流前 N 字节解码；对 `Authorization` 字眼做 mask（保险起见）
3. 在 `__init__.py` 追加 `stream_post`

**验证：**
- `python -m py_compile mewcode/transport/http_client.py`
- `python -c "from mewcode.transport import stream_post; print('ok')"`
- 端到端在 T9 验证

---

## T7：Provider 抽象 + Message + 异常 + 事件

**文件：**
- 新建 `mewcode/providers/errors.py`
- 新建 `mewcode/providers/events.py`
- 新建 `mewcode/providers/base.py`
- 修改 `mewcode/providers/__init__.py`

**依赖：** T1、T3

**步骤：**

1. 在 `errors.py` 定义 5 个异常：
   - `ProviderError(Exception)`，`category = "Provider 错误"`
   - `NetworkError(ProviderError)`，`category = "网络错误"`
   - `HTTPStatusError(ProviderError)`，`category = "HTTP 错误"`，构造接收 `status_code: int` 和 `body_snippet: str`
   - `AuthError(HTTPStatusError)`，`category = "鉴权失败"`
   - `StreamParseError(ProviderError)`，`category = "流解析错误"`
2. 在 `events.py` 定义事件：
   - `@dataclass(frozen=True) class TextDelta`：`text: str`
   - `@dataclass(frozen=True) class ThinkingDelta`：`text: str`
   - `@dataclass(frozen=True) class Usage`：`input_tokens: int`、`output_tokens: int`、`thinking_tokens: int | None = None`
   - `@dataclass(frozen=True) class Done`：无字段
   - `StreamEvent = TextDelta | ThinkingDelta | Usage | Done`
3. 在 `base.py` 定义：
   - `Role = Literal["user", "assistant"]`
   - `@dataclass(frozen=True) class Message`：`role: Role`、`content: str`
   - `class Provider(ABC)`：构造接收 `ProviderConfig`，`protocol`/`model` property，抽象方法 `stream_chat(messages, thinking) -> AsyncIterator[StreamEvent]`，docstring 说明事件流约定
4. 在 `__init__.py` 暴露：`Provider`、`Message`、`Role`、所有事件类型、`StreamEvent`、所有异常

**验证：**
- `python -c "from mewcode.providers import Provider, Message, TextDelta, ThinkingDelta, Usage, Done, ProviderError; print('ok')"`
- `python -c "from mewcode.providers import Provider; print(Provider.__abstractmethods__)"` 输出 `frozenset({'stream_chat'})`

---

## T8：PROVIDER_REGISTRY + build_provider + 单测

**文件：**
- 新建 `mewcode/providers/registry.py`
- 新建 `tests/test_provider_registry.py`
- 修改 `mewcode/providers/__init__.py`

**依赖：** T7

**步骤：**

1. 在 `registry.py` 实现：
   - `from .base import Provider`
   - `PROVIDER_REGISTRY: dict[Protocol, type[Provider]] = {}`
   - `def build_provider(config: ProviderConfig) -> Provider`：从 registry 取类，取不到抛 `ValueError(f"未知协议: {config.protocol}")`，否则 `cls(config)`
2. 在 `__init__.py` 中按以下顺序写（T9/T11 完成后启用最后两组 import）：
   ```python
   from .base import Provider, Message, Role
   from .events import TextDelta, ThinkingDelta, Usage, Done, StreamEvent
   from .errors import ProviderError, NetworkError, HTTPStatusError, AuthError, StreamParseError
   from .registry import PROVIDER_REGISTRY, build_provider
   # T9 完成后启用：
   # from .anthropic import AnthropicProvider
   # PROVIDER_REGISTRY["anthropic"] = AnthropicProvider
   # T11 完成后启用：
   # from .openai import OpenAIProvider
   # PROVIDER_REGISTRY["openai"] = OpenAIProvider
   ```
3. 在 `tests/test_provider_registry.py` 中（用 stub Provider 子类，不依赖真实实现）：
   - 定义 `class StubProvider(Provider)` 实现 `stream_chat` 为空异步生成器
   - `test_注册与查找`：手动注册 → `build_provider` → 验证返回 StubProvider 实例
   - `test_未知协议抛错`：清空 registry 后 `build_provider` → ValueError
   - `test_扩展性可见`（AC24）：`assert "anthropic" in PROVIDER_REGISTRY and "openai" in PROVIDER_REGISTRY`，断言 registry 是 dict 类型，注释说明"新协议只需在此 dict 加一行"
   - 测试 setup/teardown 保存与恢复 registry 状态

**验证：**
- `pytest tests/test_provider_registry.py -v` 全部通过
- `python -c "from mewcode.providers.registry import build_provider, PROVIDER_REGISTRY; print(PROVIDER_REGISTRY)"`

---

## T9：AnthropicProvider 基础（不带 thinking）

**文件：**
- 新建 `mewcode/providers/anthropic.py`
- 修改 `mewcode/providers/__init__.py`（启用 T8 中的 import 占位）

**依赖：** T6、T7、T8

**步骤：**

1. 在 `anthropic.py` 实现 `class AnthropicProvider(Provider)`：
   - `async def stream_chat(self, messages, thinking) -> AsyncIterator[StreamEvent]`
   - 步骤 a：`url = f"{self._config.base_url}/v1/messages"`
   - 步骤 b：headers `{"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}`
   - 步骤 c：`messages` 列表转 `[{"role": m.role, "content": m.content} for m in messages]`
   - 步骤 d：body `{"model": ..., "max_tokens": 8192, "stream": True, "messages": [...]}`，**本任务不加 thinking 字段**
   - 步骤 e：调用 `transport.stream_post(url, headers, body)`
   - 步骤 f：维护 `input_tokens=0`、`output_tokens=0`、`thinking_tokens=None`
   - 步骤 g：`async for frame in iter_sse_frames(byte_stream):`
     - 跳过空 data
     - `try: data_obj = json.loads(frame.data) except JSONDecodeError as e: raise StreamParseError(...) from e`
     - `match (frame.event, data_obj):`
       - `("message_start", {"message": {"usage": {"input_tokens": int(n)}}})` → `input_tokens = n`
       - `("content_block_delta", {"delta": {"type": "text_delta", "text": str(t)}})` → `yield TextDelta(text=t)`
       - `("content_block_delta", {"delta": {"type": "thinking_delta", **_}})` → 本任务忽略（T10 启用）
       - `("message_delta", {"usage": {"output_tokens": int(n), **_}})` → `output_tokens = n`
       - `("message_stop", _)` → `yield Usage(input_tokens, output_tokens, thinking_tokens); yield Done(); return`
       - `_` → pass
2. 编辑 `mewcode/providers/__init__.py` 启用：
   ```python
   from .anthropic import AnthropicProvider
   PROVIDER_REGISTRY["anthropic"] = AnthropicProvider
   ```
3. 文件顶部加中文 docstring 简述协议端点、请求体、事件映射

**验证：**
- `python -m py_compile mewcode/providers/anthropic.py`
- `python -c "from mewcode.providers import AnthropicProvider, PROVIDER_REGISTRY; print(PROVIDER_REGISTRY)"` 含 `anthropic` 键
- 真实端到端（先在工作目录放 `mewcode.yaml`）：
  ```python
  import asyncio
  from mewcode.config import load
  from mewcode.providers import build_provider, Message
  cfg = load("mewcode.yaml")
  prov = build_provider(cfg.providers["deepseek-anthropic"])
  async def go():
      async for ev in prov.stream_chat([Message("user", "你好，一句话自我介绍")], False):
          print(ev)
  asyncio.run(go())
  ```
  期望：若干 TextDelta，最后 Usage + Done，无异常

---

## T10：AnthropicProvider 加 thinking 支持

**文件：**
- 修改 `mewcode/providers/anthropic.py`

**依赖：** T9

**步骤：**

1. 请求体构造处增加：
   ```python
   if thinking:
       body["thinking"] = {"type": "enabled", "budget_tokens": 4096}
   ```
   注：`max_tokens=8192 > budget_tokens=4096`，满足 Anthropic 协议约束
2. 启用事件循环中的 thinking_delta 分支：
   ```python
   case ("content_block_delta", {"delta": {"type": "thinking_delta", "thinking": str(t)}}):
       yield ThinkingDelta(text=t)
   ```
3. **思考 token 字段处理（实施时验证）**：先在 `message_delta` 与 `message_stop` 分支临时打印 `print(json.dumps(data_obj, ensure_ascii=False))`，跑一次 thinking=True 请求观察响应，确认思考 token 在 usage 中的字段名（可能位于 `cache_creation_input_tokens`、`thinking_tokens` 或并入 `output_tokens`）
4. 据观察补一行：
   ```python
   if "thinking_tokens" in usage_dict:  # 字段名以实际响应为准
       thinking_tokens = usage_dict["thinking_tokens"]
   ```
   若后端不单独返回，则 `thinking_tokens` 保持 `None`，由 Renderer 判空决定是否显示（spec F13）

**验证：**
- `python -m py_compile mewcode/providers/anthropic.py`
- 真实端到端：T9 脚本改为 `thinking=True`，prompt 改为"证明素数有无穷多个，简要说明思路"
  ```python
  async for ev in prov.stream_chat([Message("user", "证明素数有无穷多个，简要说明思路")], True):
      print(type(ev).__name__, ev)
  ```
  期望：先若干 ThinkingDelta，再若干 TextDelta，最后 Usage + Done
- 若实际字段名与步骤 4 不符，就地修正（这正是步骤 3 debug 打印的目的）

---

## T11：OpenAIProvider

**文件：**
- 新建 `mewcode/providers/openai.py`
- 修改 `mewcode/providers/__init__.py`（启用 OpenAI 注册）

**依赖：** T6、T7、T8（与 T9 平行可并行）

**步骤：**

1. 在 `openai.py` 实现 `class OpenAIProvider(Provider)`：
   - 步骤 a：`url = f"{self._config.base_url}/v1/chat/completions"`
   - 步骤 b：headers `{"Authorization": f"Bearer {api_key}", "content-type": "application/json"}`
   - 步骤 c：消息转换 `[{"role": m.role, "content": m.content} for m in messages]`
   - 步骤 d：body `{"model": ..., "stream": True, "stream_options": {"include_usage": True}, "messages": [...]}`，**忽略 thinking 参数**
   - 步骤 e：调用 `transport.stream_post`
   - 步骤 f：维护 `input_tokens / output_tokens` 局部变量
   - 步骤 g：`async for frame in iter_sse_frames(byte_stream):`
     - 跳过空 data
     - 处理哨兵：`if frame.data.strip() == "[DONE]": yield Done(); return`
     - `try: data_obj = json.loads(frame.data) except JSONDecodeError as e: raise StreamParseError(...) from e`
     - 派发：
       - `data_obj["choices"][0]["delta"].get("content")` 非空 → `yield TextDelta(text=...)`
       - `data_obj.get("usage")` 存在（最后一帧）→ `yield Usage(input_tokens=usage["prompt_tokens"], output_tokens=usage["completion_tokens"])`
       - 其他（finish_reason、role 字段）→ pass
   - 注：OpenAI SSE 没有 `event:` 字段，只看 `frame.data`
2. 编辑 `__init__.py` 启用：
   ```python
   from .openai import OpenAIProvider
   PROVIDER_REGISTRY["openai"] = OpenAIProvider
   ```

**验证：**
- `python -m py_compile mewcode/providers/openai.py`
- `python -c "from mewcode.providers import OpenAIProvider, PROVIDER_REGISTRY; assert 'openai' in PROVIDER_REGISTRY; print('ok')"`
- 真实端到端（确保 `mewcode.yaml` 中 `deepseek-openai` 配置正确）：
  ```python
  prov = build_provider(cfg.providers["deepseek-openai"])
  async for ev in prov.stream_chat([Message("user", "你好，一句话自我介绍")], False):
      print(ev)
  ```
  期望：若干 TextDelta + Usage + Done

---

## T12：Renderer 启动/错误/命令回显方法

**文件：**
- 新建 `mewcode/render/renderer.py`
- 修改 `mewcode/render/__init__.py`

**依赖：** T1、T7（需要 Usage 类型，但本任务不实现 print_usage）

**步骤：**

1. 在 `renderer.py` 定义 `class Renderer`：
   - 构造 `def __init__(self, console: Console)`：保存 console，初始化 `self._live: Live | None = None`、`self._buffer: str = ""`、`self._thinking_buffer: str = ""`、`self._thinking_live: Live | None = None`
2. 实现非流式方法：
   - `print_banner(provider_name, protocol, model)`：rich Panel 或对齐文本，三行：
     ```
     MewCode v0.1.0
     当前供应商: <name>  协议: <protocol>  模型: <model>
     输入 /help 查看可用命令，/exit 退出
     ```
   - `print_help_hint(commands: list[str])`：一行简短提示
   - `print_info(text: str)`：默认色一行
   - `print_command_list(commands: list)`：传入 Command 对象列表，对齐文本每行 `/<name> (<aliases>)  <description>`
   - `print_provider_list(providers: dict[str, ProviderConfig], current_name: str)`：列出所有 name/protocol/model/base_url，标记 `*` 当前生效项；**绝不打印 api_key**
   - `print_unknown_command(name: str, available: list[str])`：红字"未知命令: /<name>" + 灰字列出可用命令
   - `print_error(category: str, message: str)`：`style="bold red"` 打印 `[<category>] <message>`
3. **api_key 保护**（spec N9）：`print_provider_list` 只取 name/protocol/model/base_url，绝不引用 api_key；文件顶部 docstring 注明该约束
4. 在 `__init__.py` 暴露 `Renderer`

**验证：**
- `python -m py_compile mewcode/render/renderer.py`
- 手工测试：
  ```python
  from rich.console import Console
  from mewcode.render import Renderer
  r = Renderer(Console())
  r.print_banner("deepseek-anthropic", "anthropic", "deepseek-v4-pro[1m]")
  r.print_error("网络错误", "连接被拒绝")
  r.print_unknown_command("foo", ["exit", "help", "clear"])
  ```
  目测：含中文、有颜色（红字明显）、无 api_key
- 构造含 api_key 的 ProviderConfig 调 `print_provider_list`，输出**不**应出现 api_key 值

---

## T13：Renderer 流式正文/思考/用量

**文件：**
- 修改 `mewcode/render/renderer.py`

**依赖：** T12

**步骤：**

1. 流式正文：
   - `begin_assistant()`：清空 `_buffer`，创建 `self._live = Live(Markdown(""), console=self._console, refresh_per_second=10, transient=False)`，调 `__enter__`
   - `push_text(text)`：累加 `_buffer`，`self._live.update(Markdown(self._buffer))`
   - `end_assistant()`：`self._live.__exit__(None, None, None)`，置 None，`self._console.print()` 空行分隔
2. 流式思考：
   - `begin_thinking()`：先 `self._console.print("[dim italic]▎思考中…[/]")`，清空 `_thinking_buffer`，创建 `self._thinking_live = Live(Text("", style="dim italic"), ...)`，调 `__enter__`
   - `push_thinking(text)`：累加 `_thinking_buffer`，`self._thinking_live.update(Text(self._thinking_buffer, style="dim italic"))`
   - `end_thinking()`：关闭 `_thinking_live` 置 None，`self._console.print()` 空行分隔
   - 思考块用纯 `Text` 而非 Markdown，因为思考是连续推理流不是结构化输出
3. `print_usage(usage: Usage)`：
   - 基础格式 `↑<input> tokens · ↓ <output> tokens`
   - `usage.thinking_tokens is not None` → 追加 ` · 思考 <thinking> tokens`
   - 整行 `style="dim"` 灰色
4. `abort_streaming()`：
   - `_live` 非 None → `__exit__(None, None, None)` 置 None
   - `_thinking_live` 非 None → 同上
   - `self._console.print()` 空行分隔
   - **不**打印任何"已中断"标记（spec N5：保留显示，不进历史）

**验证：**
- `python -m py_compile mewcode/render/renderer.py`
- 手工模拟：
  ```python
  import time
  from rich.console import Console
  from mewcode.render import Renderer
  from mewcode.providers import Usage
  r = Renderer(Console())
  r.begin_assistant()
  for chunk in ["# 标题\n", "这是**加粗**", "\n\n```python\nprint(", "'hi'", ")\n```\n"]:
      r.push_text(chunk); time.sleep(0.3)
  r.end_assistant()
  r.print_usage(Usage(input_tokens=12, output_tokens=34))
  ```
  目测：标题加粗、代码块带框、流式逐字、灰字用量行
- 思考流模拟：`begin_thinking → push_thinking → end_thinking → begin_assistant → push_text → end_assistant`，目测灰色斜体思考 + 空行 + 正文

---

## T14：Session 数据类

**文件：**
- 新建 `mewcode/chat/session.py`
- 修改 `mewcode/chat/__init__.py`

**依赖：** T7

**步骤：**

1. 在 `session.py` 定义：
   ```python
   from dataclasses import dataclass, field
   from mewcode.providers import Provider, Message

   @dataclass
   class Session:
       """单次 MewCode 进程内的会话状态。可变。"""
       provider: Provider
       messages: list[Message] = field(default_factory=list)
       thinking_enabled: bool = False
       current_provider_name: str = ""

       def append_user(self, text: str) -> None:
           """追加用户消息到历史。"""
           self.messages.append(Message(role="user", content=text))

       def append_assistant(self, text: str) -> None:
           """追加 AI 回复到历史。被中断时不应调用此方法（N5 语义）。"""
           self.messages.append(Message(role="assistant", content=text))

       def clear(self) -> None:
           """清空消息历史。"""
           self.messages.clear()

       def switch_provider(self, provider: Provider, name: str = "") -> None:
           """切换 Provider 并清空历史。"""
           self.provider = provider
           if name:
               self.current_provider_name = name
           self.messages.clear()
   ```

> **task 阶段对 plan 的扩展**：本任务在 plan.md 定义的 Session 基础上增
> 加了 `current_provider_name: str` 字段。理由：T16 的 `/providers` 命
> 令需要标记当前生效供应商，`/provider <name>` 切换时也需要同步记录，
> 通过名称匹配 protocol/model 在多个供应商共用同模型时存在歧义。
> `switch_provider` 同步增加可选 `name` 参数。

2. 在 `__init__.py` 暴露 `Session`（`run_turn` 留给 T15）

**验证：**
- `python -c "from mewcode.chat import Session; print('ok')"`
- 内联测试：
  ```python
  from mewcode.chat import Session
  s = Session(provider=None)
  s.append_user("hi"); s.append_assistant("hello")
  assert len(s.messages) == 2
  s.clear(); assert s.messages == []
  print("ok")
  ```

---

## T15：chat.run_turn 协程

**文件：**
- 新建 `mewcode/chat/engine.py`
- 修改 `mewcode/chat/__init__.py`（追加 `run_turn`）

**依赖：** T13、T14

**步骤：**

1. 在 `engine.py` 实现：
   ```python
   from mewcode.chat.session import Session
   from mewcode.providers import (
       TextDelta, ThinkingDelta, Usage, Done, ProviderError,
   )
   from mewcode.render import Renderer

   async def run_turn(session: Session, user_input: str, renderer: Renderer) -> bool:
       """跑一轮对话。返回 True 正常完成，False 中断或出错。"""
       session.append_user(user_input)
       assistant_buf = ""
       pending_usage: Usage | None = None
       in_thinking = False
       in_assistant = False
       try:
           stream = session.provider.stream_chat(
               session.messages, session.thinking_enabled
           )
           async for event in stream:
               match event:
                   case ThinkingDelta(text=t):
                       if not in_thinking:
                           renderer.begin_thinking()
                           in_thinking = True
                       renderer.push_thinking(t)
                   case TextDelta(text=t):
                       if in_thinking:
                           renderer.end_thinking()
                           in_thinking = False
                       if not in_assistant:
                           renderer.begin_assistant()
                           in_assistant = True
                       assistant_buf += t
                       renderer.push_text(t)
                   case Usage() as u:
                       pending_usage = u
                   case Done():
                       break
           # 正常结束
           if in_thinking:
               renderer.end_thinking()
           if in_assistant:
               renderer.end_assistant()
           if assistant_buf:
               session.append_assistant(assistant_buf)
           if pending_usage:
               renderer.print_usage(pending_usage)
           return True
       except KeyboardInterrupt:
           renderer.abort_streaming()
           # 不调用 append_assistant —— N5 语义
           return False
       except ProviderError as e:
           renderer.abort_streaming()
           renderer.print_error(e.category, str(e))
           return False
   ```
2. 在 `__init__.py` 追加 `from .engine import run_turn`

**验证：**
- `python -m py_compile mewcode/chat/engine.py`
- 真实端到端：
  ```python
  import asyncio
  from rich.console import Console
  from mewcode.config import load
  from mewcode.providers import build_provider
  from mewcode.chat import Session, run_turn
  from mewcode.render import Renderer
  cfg = load("mewcode.yaml")
  prov = build_provider(cfg.providers[cfg.default])
  s = Session(provider=prov)
  r = Renderer(Console())
  asyncio.run(run_turn(s, "用一句话介绍 Python", r))
  print("messages:", len(s.messages))
  ```
  期望：流式输出 + 灰字用量行；`len(s.messages) == 2`
- 中断测试：prompt 改为"写一篇 1000 字关于猫的文章"，按 Ctrl+C，期望立即停止，`len(s.messages) == 1`（只剩 user）

---

## T16：命令分发 + 七个内置命令 + 单测

**文件：**
- 新建 `mewcode/commands/registry.py`
- 新建 `mewcode/commands/builtin.py`
- 新建 `tests/test_command_dispatch.py`
- 修改 `mewcode/commands/__init__.py`

**依赖：** T14、T13、T8、T3

**步骤：**

1. 在 `registry.py` 定义：
   ```python
   from dataclasses import dataclass
   from collections.abc import Callable, Awaitable

   @dataclass(frozen=True)
   class Command:
       name: str
       aliases: tuple[str, ...]
       description: str
       handler: Callable[["CommandContext"], Awaitable["CommandResult"]]

   @dataclass
   class CommandContext:
       session: "Session"
       app_config: "AppConfig"
       args: list[str]
       renderer: "Renderer"

   @dataclass(frozen=True)
   class CommandResult:
       should_exit: bool = False

   COMMANDS: dict[str, Command] = {}

   def register(cmd: Command) -> None:
       COMMANDS[cmd.name] = cmd
       for alias in cmd.aliases:
           COMMANDS[alias] = cmd

   async def dispatch(line: str, ctx: CommandContext) -> CommandResult | None:
       if not line.startswith("/"):
           return None
       parts = line[1:].split()
       if not parts:
           ctx.renderer.print_unknown_command("", sorted({c.name for c in COMMANDS.values()}))
           return CommandResult()
       name, *args = parts
       cmd = COMMANDS.get(name)
       if cmd is None:
           ctx.renderer.print_unknown_command(name, sorted({c.name for c in COMMANDS.values()}))
           return CommandResult()
       ctx.args = args
       return await cmd.handler(ctx)
   ```
2. 在 `builtin.py` 实现 7 个 handler 与 `register_builtins()`：
   - `_handle_exit(ctx)` → `CommandResult(should_exit=True)`
   - `_handle_help(ctx)` → 调 `renderer.print_command_list(...)`，去重后的 Command 对象列表（按 name 去重）
   - `_handle_clear(ctx)` → `session.clear()`，`renderer.print_info("会话历史已清空")`
   - `_handle_think(ctx)` → 解析 args：
     - 缺参数或非 `on/off` → `print_info("用法: /think on|off")`
     - `on`：`session.provider.protocol != "anthropic"` → `print_info("当前协议不支持 extended thinking")`；否则 `session.thinking_enabled = True`，`print_info("extended thinking 已开启")`
     - `off`：`session.thinking_enabled = False`，`print_info("extended thinking 已关闭")`
   - `_handle_providers(ctx)` → `renderer.print_provider_list(ctx.app_config.providers, current_name=ctx.session.current_provider_name)`
   - `_handle_provider(ctx)` → 解析 `args[0]`；不存在 → `print_info("供应商不存在: <name>")` + `print_provider_list`；存在 → `from mewcode.providers import build_provider; new_prov = build_provider(target_cfg); session.switch_provider(new_prov, name=target_name); renderer.print_info(f"已切换到 {name} ({protocol}/{model})")`
   - `register_builtins()` 注册全部 7 个命令（`exit` 别名 `quit`）
3. 在 `tests/test_command_dispatch.py`（用 stub Session/Provider/Renderer）：
   - `test_未知命令`：`/foobar` → CommandResult()，stub renderer 收到 `print_unknown_command`
   - `test_非命令返回None`：`hello world` → None
   - `test_exit返回should_exit`：`/exit` → result.should_exit == True
   - `test_quit_别名`：`/quit` → should_exit == True
   - `test_clear清空历史`：先 append 几条消息，`/clear` → messages == []
   - `test_think_on_anthropic协议`：stub provider.protocol="anthropic"，`/think on` → thinking_enabled == True
   - `test_think_on_openai协议`：stub provider.protocol="openai"，`/think on` → thinking_enabled 保持 False，stub renderer 收到"不支持"info
4. 在 `__init__.py` 暴露 `Command`、`CommandContext`、`CommandResult`、`dispatch`、`register_builtins`、`COMMANDS`

**验证：**
- `pytest tests/test_command_dispatch.py -v` 全部通过
- `python -m py_compile mewcode/commands/builtin.py mewcode/commands/registry.py`

---

## T17：repl.run_repl 主循环

**文件：**
- 新建 `mewcode/repl/main_loop.py`
- 修改 `mewcode/repl/__init__.py`

**依赖：** T15、T16

**步骤：**

1. 在 `main_loop.py` 实现：
   ```python
   from prompt_toolkit import PromptSession
   from prompt_toolkit.patch_stdout import patch_stdout

   from mewcode.config import AppConfig
   from mewcode.chat import Session, run_turn
   from mewcode.commands import (
       CommandContext, dispatch, register_builtins,
   )
   from mewcode.render import Renderer


   async def run_repl(
       session: Session,
       app_config: AppConfig,
       renderer: Renderer,
   ) -> int:
       """REPL 主循环。返回进程退出码。"""
       register_builtins()

       renderer.print_banner(
           provider_name=session.current_provider_name,
           protocol=session.provider.protocol,
           model=session.provider.model,
       )
       renderer.print_help_hint(["/help", "/exit"])

       pt_session = PromptSession()
       ctrl_c_pending = False

       with patch_stdout():
           while True:
               try:
                   line = await pt_session.prompt_async("> ")
               except EOFError:
                   return 0
               except KeyboardInterrupt:
                   if ctrl_c_pending:
                       return 0
                   ctrl_c_pending = True
                   renderer.print_info("再按一次 Ctrl+C 或输入 /exit 退出")
                   continue

               ctrl_c_pending = False

               if not line.strip():
                   continue

               ctx = CommandContext(
                   session=session,
                   app_config=app_config,
                   args=[],
                   renderer=renderer,
               )
               result = await dispatch(line, ctx)
               if result is not None:
                   if result.should_exit:
                       return 0
                   continue

               await run_turn(session, line, renderer)
   ```
2. 在 `__init__.py` 暴露 `run_repl`

**关键说明：**
- `patch_stdout()` 让 rich Live 与 prompt 输入框共存
- 流式 Ctrl+C 由 `run_turn` 自捕获，不冒泡到 REPL，所以这里只处理 prompt 阶段
- `register_builtins()` 幂等

**验证：**
- `python -m py_compile mewcode/repl/main_loop.py`
- `python -c "from mewcode.repl import run_repl; print('ok')"`
- 完整端到端验证留给 T18+

---

## T18：main.py 入口装配

**文件：**
- 新建 `mewcode/main.py`
- 确认 `mewcode/__main__.py`（T1 已创建）

**依赖：** T17

**步骤：**

1. 在 `mewcode/main.py` 实现：
   ```python
   """MewCode 入口。负责装配对象图、启动 REPL、转换异常为退出码。"""
   import asyncio
   import sys
   from pathlib import Path

   from rich.console import Console

   from mewcode.config import load, ConfigError
   from mewcode.providers import build_provider
   from mewcode.chat import Session
   from mewcode.render import Renderer
   from mewcode.repl import run_repl

   CONFIG_FILENAME = "mewcode.yaml"


   def main() -> int:
       """主入口。返回值即进程退出码。

       退出码语义：
           0 - 正常退出
           1 - 配置错误
           2 - 未预期异常
       """
       console = Console()
       renderer = Renderer(console)

       # 阶段 1：加载配置
       try:
           app_config = load(Path.cwd() / CONFIG_FILENAME)
       except ConfigError as e:
           renderer.print_error(e.category, str(e))
           return 1

       # 阶段 2：装配对象图
       try:
           default_cfg = app_config.providers[app_config.default]
           provider = build_provider(default_cfg)
           session = Session(provider=provider)
           session.current_provider_name = app_config.default
       except Exception:
           console.print_exception()
           return 2

       # 阶段 3：启动 REPL
       try:
           return asyncio.run(run_repl(session, app_config, renderer))
       except Exception:
           console.print_exception()
           return 2


   if __name__ == "__main__":
       sys.exit(main())
   ```
2. 确认 `mewcode/__main__.py`（T1 已创建）：
   ```python
   from mewcode.main import main
   import sys
   sys.exit(main())
   ```

**验证：**
- `python -m py_compile mewcode/main.py mewcode/__main__.py`
- 缺失配置场景（AC2）：在不含 `mewcode.yaml` 的临时目录执行 `python -m mewcode`，期望红字 `[配置文件不存在]`，退出码 1
- 非法配置场景（AC3）：临时写一份 default 指向不存在供应商的 `mewcode.yaml`，执行后红字错误 + 退出码 1
- 正常启动（pip install -e . 后）：`mewcode` 进入 REPL，看到横幅，`/exit` 退出码 0

---

## T19：创建本地 mewcode.yaml（真实凭据）

**文件：**
- 新建 `mewcode.yaml`（不进 git，已在 T1 .gitignore 中）

**依赖：** T2

**步骤：**

1. 复制 `mewcode.yaml.example` 为 `mewcode.yaml`
2. 替换 api_key 为题目给定凭据：
   ```yaml
   default: deepseek-anthropic

   providers:
     deepseek-anthropic:
       protocol: anthropic
       model: deepseek-v4-pro[1m]
       base_url: https://api.deepseek.com/anthropic
       api_key: sk-***REDACTED***

     deepseek-openai:
       protocol: openai
       model: deepseek-chat
       base_url: https://api.deepseek.com
       api_key: sk-***REDACTED***
   ```
   注：DeepSeek 同一把 key 同时支持 Anthropic 协议和 OpenAI 协议端点
3. 验证文件未进暂存区

**验证：**
- `python -c "from mewcode.config import load; cfg = load('mewcode.yaml'); print(list(cfg.providers.keys()), '->', cfg.default)"` 输出 `['deepseek-anthropic', 'deepseek-openai'] -> deepseek-anthropic`
- `git check-ignore mewcode.yaml` 输出 `mewcode.yaml`

---

## T20：pip install + tmux 烟雾测试（Git Bash + tmux）

**文件：** 无（验证性任务）

**依赖：** T18、T19

**步骤：**

1. 在项目根（cmd 或 PowerShell）：
   ```cmd
   pip install -e .
   pytest tests/ -v
   ```
   全部通过

2. **安装 tmux 到 Git Bash**（首次执行）：
   - 推荐路径：通过 scoop 安装 msys2，再装 tmux
     ```cmd
     scoop install msys2
     msys2 -c "pacman -S --noconfirm tmux"
     ```
   - 或使用 git-sdk-64 的 pacman 装 tmux
   - 验证：Git Bash 中 `tmux -V` 输出版本号
   - 安装受阻时降级到 Windows Terminal 验证（spec N4 已包含），但需在 T21 验收报告中注明"未在 tmux 内验证 AC23"

3. 在 Git Bash 启动 tmux：
   ```bash
   cd /e/AI/vscode_project/mecode
   tmux new -s mewcode-test
   mewcode
   ```

4. **烟雾测试 5 项**（在 tmux pane 内）：
   - **S1 启动可见**：横幅显示 `deepseek-anthropic / anthropic / deepseek-v4-pro[1m]`，提示符 `> `
   - **S2 单轮对话**：输入"用一句话介绍 Python"，观察流式 + 末尾灰字 token 用量
   - **S3 Markdown 渲染**：要求"用 Markdown 列 3 个 Python 数据结构，配示例代码"，观察标题加粗、代码块带框
   - **S4 切协议 + thinking**：`/provider deepseek-openai` → `/think on` 应提示不支持 → `/provider deepseek-anthropic` → `/think on` → 提复杂问题 → 看到灰色斜体思考 + 正文
   - **S5 退出**：`/exit`，Git Bash 中 `echo $?` 验证退出码 0

5. 失败场景就地修复，回到对应 T 任务

**验证：**
- 5 个烟雾场景在 tmux 内全部通过
- `pytest tests/ -v` 全部通过

---

## T21：跑 checklist.md 全量验收

**文件：**
- 无新文件
- 产出：验收报告 `docs/02/acceptance-report.md`

**依赖：** T20 通过 + 阶段四 checklist.md 已产出

**步骤：**

1. 阅读 `docs/02/checklist.md`
2. 在 tmux 会话中按 checklist 逐项验证：
   - 启动新 tmux 会话保持环境干净
   - 每项准备测试输入、运行、观察输出
   - 通过/不通过分别记录证据（终端输出片段、命令、现象）
3. 若有不通过：
   - 定位到对应 T 任务修复
   - 重跑 `pytest tests/`
   - 重跑该 checklist 条目
   - 修复影响其他条目时重跑相关条目
4. 全部通过后生成验收报告（mew-spec 阶段六模板）：
   ```markdown
   ## 验收报告

   ### 通过（N/M）
   - [x] AC1 — 证据：...

   ### 未通过（如有）
   - [ ] ACx — 预期：... 实际：... 修复：...

   ### 端到端
   - [x] AC23 tmux 场景 — 结果：...
   ```

**验证：**
- checklist.md 中每一项都有运行证据
- 验收报告生成完毕
- 任何"未通过"项给出修复方案，最终全部通过

---

## 任务汇总

| #   | 任务                                    | 依赖              | 文件数 | 测试   |
|-----|-----------------------------------------|-------------------|--------|--------|
| T1  | 项目骨架                                | 无                | 11     | -      |
| T2  | mewcode.yaml.example                    | T1                | 1      | -      |
| T3  | config 数据模型与异常                   | T1                | 3      | -      |
| T4  | config.load + 单测                      | T3                | 2      | ✅ 6   |
| T5  | SSE 帧解析 + 单测                       | T1                | 3      | ✅ 6   |
| T6  | httpx 流式 POST                         | T5、T7            | 2      | -      |
| T7  | Provider 抽象 + Message + 异常 + 事件   | T1、T3            | 4      | -      |
| T8  | PROVIDER_REGISTRY + 单测                | T7                | 3      | ✅ 3   |
| T9  | AnthropicProvider 基础                  | T6、T7、T8        | 2      | 真实   |
| T10 | AnthropicProvider thinking              | T9                | 1      | 真实   |
| T11 | OpenAIProvider                          | T6、T7、T8        | 2      | 真实   |
| T12 | Renderer 启动/错误/命令回显             | T1、T7            | 2      | 手工   |
| T13 | Renderer 流式正文/思考/用量             | T12               | 1      | 手工   |
| T14 | Session（含 current_provider_name）     | T7                | 2      | 内联   |
| T15 | chat.run_turn                           | T13、T14          | 2      | 真实   |
| T16 | 命令分发 + 七个内置命令 + 单测          | T14、T13、T8、T3  | 4      | ✅ 7   |
| T17 | repl.run_repl                           | T15、T16          | 2      | -      |
| T18 | main.py 入口                            | T17               | 2      | 手工   |
| T19 | 创建本地 mewcode.yaml                   | T2                | 1      | 内联   |
| T20 | pip install + tmux 烟雾测试             | T18、T19          | 0      | 手工   |
| T21 | checklist.md 全量验收                   | T20 + checklist   | 0      | 全量   |

**单测总数**：22 个；**文件总数**：约 36 个

---

## 自检结论

- ✅ **plan 覆盖**：plan.md 所有组件均有任务对应
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落；T10 的"实施时验证字段名"是明确指令，不是占位
- ✅ **依赖链**：执行图有合法拓扑序，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：T14 的 `current_provider_name` 是 task 阶段对 plan 的合理扩展，已在 T14 处明确标注
