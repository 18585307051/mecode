# MewCode 第六阶段 Spec

## 背景

MewCode 第五阶段已交付五层防御权限系统（docs/06/）。前五阶段累计：
完整工具系统（read/write/edit/run/glob/search）、Agent Loop ReAct 循环、
Plan/Do 模式、prompt cache、五层权限——MewCode 已经是一个能干活、干得
好、放心用的本地 Agent。

但目前所有工具都是**内置**的——MewCode 自己实现的 6 个 Python 类。
真正的 Agent 生态价值在于"插拔"：用户可以连一个 GitHub MCP Server
来管 issues / PR、连一个 Slack Server 来发消息、连一个公司内部的
Server 来查内部 API——这些工具不需要 MewCode 自己实现，只要 Server
方实现 MCP 协议，MewCode 就能用。

第六阶段为 MewCode 装上 **MCP 客户端**：在启动时根据配置自动连接外部
Server，发现工具，包装为 Tool 注册到 ToolRegistry——模型用时完全无感。

## 目标

- MCP 协议版本：`2025-03-26`
- 两种传输：本地子进程走 stdio 管道，远程走 Streamable HTTP（POST 单响应模式）
- JSON-RPC 2.0 编解码 + 异步配对（请求带 id，响应按 id 路由到对应 future）
- 三步会话流程：initialize 握手 → tools/list 列工具 → tools/call 调用
- 适配层：每个 MCP 工具包装为 `MCPToolAdapter(Tool)`，名字加前缀
  `mcp__<server>__<tool>` 注册到 ToolRegistry
- 配置：用户级 `~/.mewcode/mcp_servers.yaml` + 项目级
  `<cwd>/.mewcode/mcp_servers.yaml`，项目级覆盖用户级
- 启动失败处理：单 Server 失败不影响其他（warning + 跳过）
- 工具调用超时：默认 60s，可在配置 override
- 启动时并发连接所有 Server（asyncio.gather）
- env / headers 支持 `${VAR}` 展开（仅从 os.environ 读，缺失变量跳过该 Server）
- 退出时清理：terminate 子进程 / 关闭 HTTP session
- 第一/二/三/四/五阶段功能不退化

## 功能需求

### F1. 配置文件结构

YAML 格式，两层文件：

```yaml
# ~/.mewcode/mcp_servers.yaml 或 <cwd>/.mewcode/mcp_servers.yaml
servers:
  filesystem:
    type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env:
      DEBUG: "${DEBUG_MODE}"
    timeout: 60   # 秒，可选

  github:
    type: http
    url: "https://api.example.com/mcp"
    headers:
      Authorization: "Bearer ${GITHUB_TOKEN}"
    timeout: 120
```

字段：
- `type`: `stdio` 或 `http`（必填）
- stdio 必填：`command`（字符串）、`args`（字符串列表，可选默认 `[]`）
- stdio 可选：`env`（dict，值支持 `${VAR}` 展开）、`cwd`（子进程工作目录，
  默认继承 mewcode 的 cwd）
- http 必填：`url`（字符串）
- http 可选：`headers`（dict，值支持 `${VAR}` 展开）
- 所有类型：`timeout`（数字，默认 60）

### F2. 两层配置合并

加载顺序与优先级（spec D2）：
- 用户级：`~/.mewcode/mcp_servers.yaml`
- 项目级：`<cwd>/.mewcode/mcp_servers.yaml`

合并规则：
- **同名 server**：项目级完整覆盖用户级（不深度合并）
- **不同名 server**：取并集
- 文件不存在 → 视为空 `servers: {}`，不报错
- YAML 解析失败 → 打印 warning，视该层为空，继续启动

### F3. ${VAR} 展开（spec Q4 / D4）

env 与 headers 字段值支持 `${VAR}` 引用环境变量：
- 仅从 `os.environ` 读（不带 fallback、不带默认值语法）
- `${VAR}` 整体替换，可与字面量混用：`"Bearer ${GITHUB_TOKEN}"`
- 不支持 `$VAR`（必须带大括号，避免歧义）
- **变量缺失** → 打印 warning + **跳过整个 Server**：
  ```
  ⚠️ MCP Server 'github' 配置含未定义环境变量 GITHUB_TOKEN（已跳过）
  ```

### F4. JSON-RPC 2.0 协议

三种消息类型（spec D9 / D10）：

**请求**（客户端 → Server）：
```json
{"jsonrpc":"2.0", "id":1, "method":"tools/call", "params":{...}}
```

**响应**（Server → 客户端）：
```json
{"jsonrpc":"2.0", "id":1, "result":{...}}
{"jsonrpc":"2.0", "id":1, "error":{"code":-32602, "message":"..."}}
```

**通知**（无 id，单向，本阶段仅发起方向用）：
```json
{"jsonrpc":"2.0", "method":"notifications/initialized"}
```

异步配对：
- 客户端维护 `_pending: dict[int, Future]`
- 发请求时：分配自增 id，构造 future 入 dict，写入传输层
- 后台 reader_loop 持续读消息：响应（含 id）→ 找 future → set_result；
  通知（无 id）→ 本阶段忽略
- await future（带 timeout）拿响应

### F5. 三步会话流程

每个 Server 启动后立即执行：

```
1. initialize 握手
   client → server: initialize 请求，含 protocolVersion=2025-03-26 +
                    clientInfo + capabilities
   server → client: initialize 响应，含 serverInfo + capabilities
   client → server: notifications/initialized 通知（不等响应）

2. tools/list 列出工具
   client → server: tools/list 请求
   server → client: 响应含 tools 数组（name / description / inputSchema）

3. 运行时按需 tools/call
   client → server: tools/call 请求 {name, arguments}
   server → client: 响应含 content 数组 + isError
```

### F6. stdio 传输

启动子进程：
- `asyncio.create_subprocess_exec(command, *args, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=merged_env, cwd=...)`
- Windows：`creationflags=CREATE_NEW_PROCESS_GROUP` 防 Ctrl+C 误杀
- env：`os.environ` 拷贝 + 配置的 env 覆盖（${VAR} 已展开）

消息编解码：
- **行分隔**：每条 JSON-RPC 消息单独一行（`\n` 结尾）
- 写：`json.dumps(msg) + "\n"` → `proc.stdin.write` + `await drain`
- 读：`await proc.stdout.readline()` → `json.loads(line.decode("utf-8"))`
- stderr 持续读取到 buffer（debug 时打印，错误时附在异常）

后台协程：
- `_reader_loop`：循环 readline + 路由到 _pending future
- `_stderr_loop`：循环读 stderr 累积到 buffer

关闭：
- 关闭 stdin → 等 5s → terminate → 等 5s → kill
- 取消 reader / stderr 协程

### F7. Streamable HTTP 传输（spec Q12 / D12）

POST 单响应模式（不维护持久 GET SSE 连接）：
- 用 httpx.AsyncClient（已有依赖）
- POST 请求体：JSON-RPC 消息
- 响应处理：
  - `Content-Type: application/json` → 直接解析为 JSON
  - `Content-Type: text/event-stream` → 读 SSE 帧，取第一个 `data: {...}`
    解析为 JSON 返回（不维持连接）
  - 其他 → 错误
- HTTP 错误（4xx/5xx）→ 抛 MCPProtocolError
- 自定义 headers 注入（${VAR} 已展开）
- 不发 notifications（HTTP 模式下通知没有意义；如果协议要求可改成 POST 不等响应）

注：本阶段 HTTP 模式没有真正的"reader_loop"——每次 tools/call 是独立
的 POST。请求-响应在同一次 HTTP 调用内完成，所以 `_pending` 字典在
HTTP 模式下不使用。

### F8. 工具适配层

`MCPToolAdapter` 继承 `Tool`：

```python
class MCPToolAdapter(Tool):
    name: str           # "mcp__<server>__<tool>"
    description: str    # 直接用 MCP 的 description
    parameters_schema: dict  # 直接用 MCP 的 inputSchema
    danger_level = DangerLevel.SAFE   # 默认 SAFE（spec D13）
    readonly = False                   # 默认 False（Plan Mode 下不可用）

    async def execute(self, params, sandbox, render_event):
        try:
            result = await self._client.call_tool(
                self._original_name, params, timeout=self._timeout
            )
            return ToolResult(success=not result.is_error, text=result.text)
        except MCPTimeoutError as e:
            return ToolResult(success=False, text=f"MCP 工具超时：{e}", error_category="MCP 超时")
        except MCPProtocolError as e:
            return ToolResult(success=False, text=f"MCP 协议错误：{e}", error_category="MCP 协议错误")
```

### F9. 工具名前缀（spec Q5 / D5）

注册到 ToolRegistry 时：
- 名字：`mcp__<server_name>__<tool_name>`
- 例如：`mcp__filesystem__read_file` / `mcp__github__create_issue`
- 前缀避免与内置工具及其他 Server 工具名冲突
- 模型在工具列表中能从名字识别出来源

### F10. tools/call 响应解析（spec Q14 / D14）

MCP 响应：
```json
{
  "result": {
    "content": [
      {"type": "text", "text": "..."},
      {"type": "image", "data": "...", "mimeType": "image/png"},
      {"type": "resource", "resource": {...}}
    ],
    "isError": false
  }
}
```

转 ToolResult.text：
- text 类型：直接拼接 `\n`
- image 类型：占位 `[image:<mimeType>, <bytes_len> bytes]`
- resource 类型：占位 `[resource:<uri>]`
- audio 类型：占位 `[audio:<mimeType>]`
- 拼接顺序按 content 数组顺序
- isError=true → ToolResult(success=False)

### F11. 启动时并发连接（spec Q8 / D11）

```python
async def start_all(servers: dict) -> dict[str, MCPClient]:
    tasks = {name: asyncio.create_task(_start_one(name, cfg))
             for name, cfg in servers.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    clients = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            print(f"⚠️ MCP Server {name!r} 启动失败：{result}（已跳过）")
        else:
            clients[name] = result
    return clients
```

每个 Server 独立 try/except，单个失败不影响其他。

### F12. 失败 Server 跳过（spec Q6 / D6）

启动时单 Server 失败 → 打 warning + 跳过：
- 子进程命令找不到（FileNotFoundError）
- HTTP 连接失败（ConnectionError / TimeoutError）
- initialize 失败（4xx HTTP / JSON-RPC error）
- tools/list 失败
- ${VAR} 展开失败

REPL 横幅打印：
```
🔌 已加载 MCP Server: filesystem (3 工具) / github (8 工具)
⚠️ Server 'docker' 启动失败：command not found（已跳过）
```

### F13. 工具调用超时（spec Q7 / D7）

每个 Server 配置 `timeout` 字段，默认 60s：
- 实现：`asyncio.wait_for(call, timeout)`
- 超时返回 `ToolResult(success=False, text="MCP 工具超时...", error_category="MCP 超时")`
- 不重试

### F14. 退出清理

mewcode 退出时（main.py 的 finally 段）：
- 对所有活跃 client 调 `await client.shutdown()`
- stdio：close stdin → terminate → kill（如需）
- http：关闭 httpx.AsyncClient
- 取消所有后台协程

### F15. 不做的事

明确不做：
- MCP resources（文件资源）/ prompts（提示词模板）/ sampling（让 server 反向调用 LLM）—— 后续章节
- Server 健康检查 / 心跳
- 自动重连（断线后人工 /mcp reload）
- 流式工具响应（SSE 持久连接）
- Server-initiated 通知（progress / cancellation）
- 工具描述本地缓存（每次启动重新 tools/list）
- /mcp 斜杠命令（show / reload / disable）—— 留给下个阶段
- 工具名冲突时的细粒度处理（前缀已经避免冲突）

## 非功能需求

### N1. 模块边界

- 新模块 `mewcode/mcp/`：
  - `__init__.py`：暴露公共 API
  - `config.py`：配置加载与 ${VAR} 展开
  - `protocol.py`：JSON-RPC 编解码
  - `transport.py`：StdioTransport + HttpTransport（共同接口 Transport）
  - `client.py`：MCPClient（initialize / tools/list / tools/call）
  - `adapter.py`：MCPToolAdapter（包装为 Tool）
  - `manager.py`：start_all / shutdown_all（生命周期管理）
- chat / providers / commands 不依赖 mewcode.mcp（单向依赖：mcp 依赖 tools，反之不）
- main.py 启动 / 退出时调 manager 接入 ToolRegistry

### N2. 不引入新依赖

dependencies 仍仅 `prompt_toolkit / rich / PyYAML / httpx`：
- JSON-RPC 用 stdlib `json`
- 子进程用 `asyncio.create_subprocess_exec`
- HTTP 用 `httpx.AsyncClient`
- ${VAR} 展开用 `re.sub`

### N3. 中文优先

错误提示、warning、文档注释中文。

### N4. 单测覆盖（spec Q15 / D15）

新增约 30-40 个单测：
1. 配置加载（4-5 个）：合法 / 缺失 / 非法 / 两层合并
2. ${VAR} 展开（3-4 个）：成功 / 缺失 / 部分缺失
3. JSON-RPC 编解码（3-4 个）：请求 / 响应 / 通知 / 错误
4. id 异步配对（3 个）：单请求 / 多请求 / 超时
5. stdio transport（4-5 个）：启动 / 读写 / 关闭 / stderr
6. http transport（4-5 个）：JSON 响应 / SSE 响应 / 错误
7. MCPClient 三步流程（3-4 个）：initialize / tools/list / tools/call
8. 适配层（3-4 个）：name 前缀 / inputSchema 透传 / 响应转换
9. 生命周期（3-4 个）：并发启动 / 单失败跳过 / 退出清理

### N5. 不退化

- 238 个已有单测全过
- 所有已有端到端脚本仍通过
- run_turn / Provider / ToolRegistry / Sandbox / PermissionPolicy
  接口不变
- 不传 mcp_servers.yaml 时（无 MCP）行为与第五阶段一致

### N6. Windows 兼容

- 子进程：CREATE_NEW_PROCESS_GROUP
- 子进程编码：stdin/stdout 用 utf-8 字节流
- 路径：os.path 跨平台

### N7. 启动性能

并发启动避免串行慢：
- 5 个 Server 各 200ms 握手 → 串行 1s vs 并发 200ms
- 启动失败的 Server 不能阻塞其他 Server 注册

### N8. 错误信息可追溯

启动失败时 warning 含：Server 名 / 失败原因 / 是否影响其他 / 修复建议
（如"请检查命令是否在 PATH 中"）。

### N9. 安全：MCP 工具进入权限系统

MCP 工具注册到 ToolRegistry 后，自然走第五阶段的权限系统：
- 默认 mode=default + 无规则 → 第一次调用走人在回路询问
- 用户可在 permissions.yaml 写 `Mcp(mcp__filesystem__*)` 规则放行
  整个 Server 的工具
- 但本阶段不为 MCP 单独定义工具名映射（rules.py TOOL_NAME_MAP），
  只通过原工具名匹配——即用户写的是 `mcp__filesystem__read_file`
  全名（fnmatch glob 支持 *）
- 注：rules.py 的 TOOL_NAME_MAP 仅识别内置 6 个工具名；MCP 工具
  匹配由 fnmatch 的 glob 处理（target 是工具的完整名，而非 command）
  → **本阶段** 简化：MCP 工具的 policy.check 用工具名前缀匹配作为
  target；用户规则用 `Bash(mcp__filesystem__read_file*)` 等字符串
  匹配；如不够灵活后续优化

更精确的 MCP 工具规则设计推迟到下个阶段。

## 验收标准

### AC1. 配置文件解析

通过单测：合法 YAML 解析为 ServerConfig 对象；非法/缺字段给清晰错误。

### AC2. 两层合并

通过单测：项目级覆盖用户级；不同名取并集；缺失文件不报错。

### AC3. ${VAR} 展开成功

通过单测：env 中 `${TEST_VAR}` 替换为 os.environ["TEST_VAR"]。

### AC4. ${VAR} 缺失跳过 Server

通过单测：未设置的 ${MISSING_VAR} → 配置加载阶段跳过该 Server +
打印 warning。

### AC5. JSON-RPC 编解码

通过单测：构造请求/响应/通知 dict → 编码 → 解码 → 等价。

### AC6. id 异步配对

通过单测（mock transport）：发 3 个并发请求，乱序响应，每个 future
拿到正确响应。

### AC7. JSON-RPC 错误响应

通过单测：响应含 error 字段时 await 抛 MCPProtocolError。

### AC8. JSON-RPC 超时

通过单测：等待时间超过 timeout → 抛 asyncio.TimeoutError →
工具调用返回 ToolResult(error)。

### AC9. stdio 启动

通过单测（mock subprocess）：调 transport.start → 创建子进程 →
reader_loop 启动。

### AC10. stdio 读写

通过单测：写一条消息 → mock 子进程 stdout 返回响应 → reader 路由
到 future。

### AC11. stdio 关闭

通过单测（mock）：调 transport.shutdown → close stdin →
terminate → kill 兜底。

### AC12. HTTP JSON 响应

通过单测：mock httpx 返回 JSON 响应 → 解析为 dict。

### AC13. HTTP SSE 响应

通过单测：mock httpx 返回 SSE 流（`data: {...}\n\n`）→ 解析为 dict。

### AC14. MCPClient initialize

通过 stub transport 单测：发 initialize → 拿响应 → 发
notifications/initialized → 状态 ready。

### AC15. MCPClient tools/list

通过 stub transport 单测：发 tools/list → 返回 tools 数组。

### AC16. MCPClient tools/call

通过 stub transport 单测：发 tools/call → 返回 content + isError。

### AC17. 适配层 name 前缀

通过单测：MCP 工具 `read_file` 注册到 ToolRegistry 后名字是
`mcp__<server>__read_file`。

### AC18. 适配层 inputSchema 透传

通过单测：MCP 的 inputSchema 不经修改进入 Tool.parameters_schema。

### AC19. 适配层响应转换

通过单测：text 内容拼接；image 占位；isError 转 success=False。

### AC20. 并发启动

通过 mock 单测：3 个 Server 配置 → asyncio.gather 启动 →
全部就绪后返回 dict。

### AC21. 单 Server 失败跳过

通过 mock 单测：3 个 Server 中 1 个 initialize 失败 → 其他 2 个仍可用。

### AC22. 退出清理

通过 mock 单测：调 manager.shutdown_all → 所有 client.shutdown 被调。

### AC23. 注册到 ToolRegistry

通过单测：start_all + register_to_registry → registry.list 含
`mcp__<server>__<tool>` 工具。

### AC24. 真实集成（手工）

`scripts/verify_mcp.py` 通过：
- 启动 npx @modelcontextprotocol/server-filesystem
- initialize / tools/list 成功
- 调用 read_file 工具拿到文件内容

### AC25. 无 MCP 配置时行为不变

无 mcp_servers.yaml 时，mewcode 启动行为完全等同第五阶段。

### AC26. 不退化

- 238 已有单测全过
- 所有已有端到端脚本通过

## 依赖与约束

- 继承前五阶段全部接口契约
- Python 3.10+
- 不引入新依赖
- Windows + Linux + macOS 跨平台
