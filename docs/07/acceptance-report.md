# MewCode 第六阶段验收报告

> 按 `docs/07/checklist.md` 逐项验证。
> 验证日期：2026-06-17
> 环境：Windows + PowerShell 5.x + Anaconda Python 3.13.9

---

## 一、自动验证结果

### 编译与测试基础

- [x] **C1 项目可安装** — 继承前阶段
- [x] **C2 包可导入** — `import mewcode` → `0.1.0`
- [x] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **297 passed**
      （238 已有 + 59 第六阶段新增）
- [x] **C4 全部源文件语法合法** — `python -m py_compile mewcode/mcp/*.py mewcode/main.py`
- [x] **C5 命令行入口可调用** — `python -m mewcode` 保持可启动
- [x] **C6 mewcode.mcp 可导入** — `from mewcode.mcp import ...` 通过

### 配置加载与 ${VAR} 展开（spec F1/F2/F3）

- [x] **AC1 配置文件解析** — `test_mcp_config.py` 覆盖合法 stdio/http、缺 type、缺 command、缺 url
- [x] **AC2 两层合并** — 用户级 + 项目级合并；项目级同名覆盖用户级；不同名取并集
- [x] **AC3 ${VAR} 展开成功** — `${TEST_VAR}` 正确从 os.environ 展开
- [x] **AC4 ${VAR} 缺失跳过 Server** — 缺失变量打印 warning 并跳过整个 Server

### JSON-RPC 协议（spec F4）

- [x] **AC5 JSON-RPC 编解码** — encode_request / encode_notification 测试通过
- [x] **AC6 id 异步配对** — PendingRegistry alloc/register/resolve 全覆盖
- [x] **AC7 JSON-RPC 错误响应** — error 字段转 MCPProtocolError
- [x] **AC8 JSON-RPC 超时** — stdio transport call timeout 覆盖
- [x] **fail_all 收尾** — 传输关闭时所有 pending future 被设置异常

### stdio 传输（spec F6）

- [x] **AC9 stdio 启动** — 用最小 Python echo server 启动子进程成功
- [x] **AC10 stdio 读写** — 写入 JSON-RPC 请求，reader_loop 路由响应
- [x] **AC11 stdio 关闭** — shutdown 后子进程退出
- [x] **stdio 启动失败** — command 不存在抛 FileNotFoundError
- [x] **timeout** — server 不响应时 asyncio.TimeoutError
- [x] **Windows 进程组** — win32 下使用 CREATE_NEW_PROCESS_GROUP

### HTTP 传输（spec F7）

- [x] **AC12 HTTP JSON 响应** — httpx.MockTransport 返回 application/json 正常解析
- [x] **AC13 HTTP SSE 响应** — text/event-stream 首个 data 帧正常解析
- [x] **HTTP 4xx** — MCPProtocolError
- [x] **HTTP error 字段** — MCPProtocolError
- [x] **未知 Content-Type** — MCPProtocolError
- [x] **headers 注入** — 自定义 headers 进入请求

### MCPClient 三步流程（spec F5）

- [x] **AC14 initialize** — transport.start + initialize 请求 + notifications/initialized
- [x] **协议版本** — initialize params.protocolVersion = `2025-03-26`
- [x] **AC15 tools/list** — 返回 ToolInfo 列表，name/description/input_schema 正确
- [x] **AC16 tools/call** — text/image/isError 全覆盖
- [x] **shutdown** — 调 transport.shutdown

### MCPToolAdapter 适配层（spec F8/F9/F10）

- [x] **AC17 name 前缀** — `mcp__fs__read_file`
- [x] **AC18 inputSchema 透传** — 原样进入 Tool.parameters_schema
- [x] **AC19 响应转换** — text 拼接、image 占位、isError → success=False
- [x] **默认属性** — danger_level=SAFE，readonly=False
- [x] **超时/协议错误/其他异常** — 转为 ToolResult(error_category)

### 生命周期管理（spec F11/F12/F14）

- [x] **AC20 并发启动** — start_all 多 Server 成功
- [x] **AC21 单 Server 失败跳过** — 一个失败不影响其他
- [x] **AC22 退出清理** — shutdown_all 调每个 client.shutdown
- [x] **AC23 注册到 ToolRegistry** — 注册 `mcp__<server>__<tool>` 工具

### main 集成

- [x] 无 MCP 配置时不打横幅、行为等同第五阶段
- [x] MCP 配置存在时 start_all → register_to → REPL
- [x] 退出时 shutdown_all 在同一 asyncio loop 内执行

### 权限系统集成（spec N9/D13）

- [x] rules.py 新增 `Mcp(...)` 虚拟工具桶
- [x] `Mcp(mcp__fs__*)` 可匹配 `mcp__fs__read_file`
- [x] MCP 工具默认 readonly=False，Plan Mode 不可用（保守）
- [x] MCP 工具默认 SAFE，安全由第五阶段权限系统负责

### 不退化（spec N5 / AC26）

- [x] **297 单测全过**
- [x] `verify_t9.py` 通过
- [x] `verify_t18.py` 通过
- [x] `verify_t19.py` 通过
- [x] `verify_round_loop.py` 通过
- [x] `verify_permissions.py` 通过

### 端到端真实集成（AC24）

- [x] `python scripts/verify_mcp.py` 通过：
  - 动态生成最小 Python stdio MCP Server
  - initialize 成功
  - tools/list 返回 echo 工具
  - register_to 注册 `mcp__fake__echo`
  - execute 调 tools/call 返回 `echo:hello`
  - shutdown_all 清理子进程

输出摘要：

```text
[1] start_all...
🔌 MCP Server 'fake' 已就绪（1 个工具）
    tools: ['echo']
[2] register_to ToolRegistry...
    registered: mcp__fake__echo
[3] execute MCP tool...
    result: echo:hello
[4] shutdown_all...

✓ MCP 端到端通过
```

---

## 二、关键技术成果

### 1. MCP 客户端完整骨架

新增 `mewcode/mcp/` 7 个模块：

| 模块 | 职责 |
|------|------|
| config.py | 两层 YAML 加载、${VAR} 展开、ServerConfig |
| protocol.py | JSON-RPC 编解码、PendingRegistry |
| transport.py | stdio 子进程 + HTTP POST 单响应 |
| client.py | initialize / tools/list / tools/call |
| adapter.py | MCPToolAdapter(Tool) |
| manager.py | 并发启动、注册、关闭 |
| __init__.py | 公共出口 |

### 2. stdio 与 HTTP 双传输

- stdio：asyncio 子进程、stdin/stdout JSON 行协议、reader_loop 路由响应
- HTTP：httpx.AsyncClient POST，支持 JSON 与 SSE 首帧

### 3. MCP 工具无感接入 ToolRegistry

MCP 工具包装为标准 MewCode Tool：

```python
mcp__fake__echo
mcp__filesystem__read_file
mcp__github__create_issue
```

Agent 看到的就是普通工具；chat.engine、Provider、Renderer 全部无需感知 MCP。

### 4. 与第五阶段权限系统联动

新增 `Mcp(...)` 规则桶：

```yaml
allow:
  - "Mcp(mcp__filesystem__*)"
```

默认不放行，第一次调用仍会进入人在回路；用户可按 server 维度放行。

### 5. 无配置零影响

无 `mcp_servers.yaml` 时，MewCode 启动行为完全等同第五阶段。

---

## 三、测试统计

```text
pytest tests/ -q
297 passed in 14.13s
```

第六阶段新增约 59 个测试：

- test_mcp_config.py: 15
- test_mcp_protocol.py: 8
- test_mcp_client.py: 7
- test_mcp_adapter.py: 9
- test_mcp_transport_http.py: 6
- test_mcp_transport_stdio.py: 6
- test_mcp_manager.py: 5
- test_permissions_rules.py: +3

---

## 四、待手工验证

- [ ] 使用真实第三方 MCP Server（如 filesystem/github）配置到
      `.mewcode/mcp_servers.yaml` 后启动，观察 🔌 横幅和工具注册
- [ ] REPL 中让模型调用真实 MCP 工具，并通过 `/permissions allow "Mcp(...)"`
      放行
- [ ] 退出时确认真实 Server 子进程被清理

---

## 五、整体结论

**第六阶段自动验收通过**：

- 26 个 AC 全部有自动或端到端验证
- 297 单测全过
- MCP 端到端脚本通过
- 第一至第五阶段功能零退化
- 不引入新依赖

MewCode 现在拥有了 MCP 生态入口：用户只需声明 Server，MewCode 就能在
启动时自动发现工具、加前缀注册、通过标准 ToolRegistry 提供给 Agent 使用。

下一阶段建议：
1. `/mcp show/reload/disable` 命令
2. MCP resources / prompts / sampling 支持
3. HTTP Streamable SSE 长连接与 progress 通知
4. MCP Server 健康检查与自动重连
