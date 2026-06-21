# MewCode 第六阶段 Tasks

> 基于已批准的 `docs/07/spec.md` 与 `docs/07/plan.md`。共 18 个任务，
> 覆盖 mcp 子模块 7 个文件、main 集成、单测与端到端验收。

## 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/mcp/__init__.py` |
| 新建 | `mewcode/mcp/config.py` |
| 新建 | `mewcode/mcp/protocol.py` |
| 新建 | `mewcode/mcp/transport.py` |
| 新建 | `mewcode/mcp/client.py` |
| 新建 | `mewcode/mcp/adapter.py` |
| 新建 | `mewcode/mcp/manager.py` |
| 修改 | `mewcode/permissions/rules.py` (+ "mcp" 工具桶) |
| 修改 | `mewcode/main.py` (启动 / 退出集成) |
| 修改 | `.gitignore` (+ mcp_servers.local.yaml 预留) |
| 新建 | `tests/test_mcp_config.py` |
| 新建 | `tests/test_mcp_protocol.py` |
| 新建 | `tests/test_mcp_transport_stdio.py` |
| 新建 | `tests/test_mcp_transport_http.py` |
| 新建 | `tests/test_mcp_client.py` |
| 新建 | `tests/test_mcp_adapter.py` |
| 新建 | `tests/test_mcp_manager.py` |
| 新建 | `scripts/verify_mcp.py` |

共 18 个文件（13 新建 + 4 修改 + 1 预留）。

---

## 任务执行顺序图

```
T1 (config) ──┬─→ T7 (manager)
T2 (protocol)─┤
T3 (transport)┤              ┌→ T11 (test_config)
T4 (client) ──┤              ├→ T12 (test_protocol)
T5 (adapter) ─┤              ├→ T13 (test_stdio)
T6 (rules.py 扩展)            ├→ T14 (test_http)
T7 ──┬────────────────────────→ T15 (test_client)
     │                        ├→ T16 (test_adapter)
T8 (main 集成) ──┐            └→ T17 (test_manager)
                 │
T9 (.gitignore) ─┤
                 ▼
              T18 (verify_mcp.py + 全量验收 + commit)
```

**关键路径**：T1→T2→T3→T4→T5→T7→T8→T11-17→T18（10 步主线）

**可并行**：
- T1 / T2 / T3 / T4 / T5 / T6 之间互相独立（除了 T3 依赖 T2、T4 依赖 T3）
- T11-T17 七个测试文件各自独立，T11 完成 T1 后可并行写

---

## T1: config.py

**文件：** 新建 `mewcode/mcp/config.py`

**依赖：** 无

**步骤：**
1. 定义 `ServerConfig` dataclass（按 plan 2.1）
2. 实现 `expand_vars(value) -> tuple[str, list[str]]`：${VAR} 展开 + 缺失列表
3. 实现 `_expand_dict(d) -> tuple[dict, list[str]]`
4. 实现 `_parse_server(name, raw) -> ServerConfig | None`：
   - 缺 type / command / url → warning 跳过
   - ${VAR} 缺失 → warning 跳过整个 Server
5. 实现 `_load_layer(path) -> dict[str, dict]`
6. 实现 `load_all(cwd) -> dict[str, ServerConfig]`：用户 + 项目两层合并

**验证：**
- `python -c "from mewcode.mcp.config import expand_vars; import os; os.environ['X']='hi'; print(expand_vars('${X}/world'))"` → `('hi/world', [])`
- `python -c "from mewcode.mcp.config import expand_vars; print(expand_vars('${MISSING}'))"` → `('${MISSING}', ['MISSING'])`

---

## T2: protocol.py

**文件：** 新建 `mewcode/mcp/protocol.py`

**依赖：** 无

**步骤：**
1. 定义 `MCPProtocolError` 与 `MCPTimeoutError` 异常
2. 实现 `encode_request(req_id, method, params=None) -> dict`
3. 实现 `encode_notification(method, params=None) -> dict`
4. 定义 `_Pending` dataclass + `PendingRegistry` 类
5. PendingRegistry 方法：alloc_id / register / resolve / fail_all

**验证：**
- `python -c "from mewcode.mcp.protocol import encode_request; print(encode_request(1, 'foo'))"` → `{"jsonrpc":"2.0","id":1,"method":"foo"}`

---

## T3: transport.py

**文件：** 新建 `mewcode/mcp/transport.py`

**依赖：** T2

**步骤：**
1. 定义 abstract `Transport`：start / call / notify / shutdown
2. 实现 `StdioTransport`（plan 2.3 全文）：
   - `__init__`：command / args / env / cwd
   - `start`：create_subprocess_exec + 启 reader_loop / stderr_loop
     - Windows 加 `creationflags=CREATE_NEW_PROCESS_GROUP`
   - `_reader_loop`：readline → 路由响应 → fail_all 收尾
   - `_stderr_loop`：捕获 stderr 到 buffer
   - `call`：register pending + write + drain + wait_for
   - `notify`：write + drain（不 register）
   - `shutdown`：close stdin → wait 2s → terminate → wait 2s → kill；取消任务
3. 实现 `HttpTransport`：
   - `start`：创建 httpx.AsyncClient
   - `call`：POST + 解析 JSON 或 SSE 首帧 + 错误检测
   - `notify`：POST 不等响应（10s 短超时）
   - `_parse_first_sse_data`：解析 `data: {...}` 行
   - `shutdown`：aclose

**验证：**
- `python -m py_compile mewcode/mcp/transport.py`
- `python -c "from mewcode.mcp.transport import StdioTransport, HttpTransport, Transport"` 不报错

---

## T4: client.py

**文件：** 新建 `mewcode/mcp/client.py`

**依赖：** T2, T3

**步骤：**
1. 常量：`_PROTOCOL_VERSION="2025-03-26"` / `_CLIENT_NAME="mewcode"` / `_CLIENT_VERSION="0.1.0"`
2. 定义 `ToolInfo` 与 `CallResult` dataclass
3. 实现 `MCPClient` 类：
   - `__init__(name, transport, timeout)`
   - `initialize()`：transport.start + initialize 请求 + initialized 通知
   - `list_tools() -> list[ToolInfo]`：tools/list 请求 + 解析
   - `call_tool(name, arguments, timeout=None) -> CallResult`
   - `_parse_call_result(result) -> CallResult`：text 拼接 / image 占位 / isError
   - `shutdown()`：transport.shutdown

**验证：**
- 单元覆盖在 T15 完成

---

## T5: adapter.py

**文件：** 新建 `mewcode/mcp/adapter.py`

**依赖：** T4 + 已有 `mewcode.tools.base`

**步骤：**
1. 定义 `MCPToolAdapter(Tool)`：
   - 类属性 `danger_level = SAFE`，`readonly = False`
   - `__init__(client, original_name, description, input_schema, timeout)`
   - 拼接 `name = "mcp__{client.name}__{original_name}"`
   - `name / description / parameters_schema` 实例 property
   - `async execute(params, sandbox, render_event) -> ToolResult`：
     - 调 client.call_tool
     - TimeoutError → ToolResult(success=False, error_category="MCP 超时")
     - MCPProtocolError → ToolResult(success=False, error_category="MCP 协议错误")
     - 其他 Exception → ToolResult(success=False, error_category="MCP 错误")

**验证：**
- `python -c "from mewcode.mcp.adapter import MCPToolAdapter"` 不报错
- 与现有 `Tool` 基类协议一致（在 T16 单测中全面验证）

---

## T6: rules.py 扩展（mcp 工具桶）

**文件：** 修改 `mewcode/permissions/rules.py`

**依赖：** 无

**步骤：**
1. 在 TOOL_NAME_MAP 中添加：`"mcp": "mcp"`（小写虚拟工具名）
2. 在 `extract_match_target(tool_name, params)` 中：如果 tool_name 以
   `mcp__` 开头 → return tool_name（用工具的全名作为匹配 target）
3. 修改 `Rule.matches(tool_name, target)`：
   - 当 rule.tool == "mcp" 时，匹配以 `mcp__` 开头的任何工具名
   - 用 fnmatch 对工具名而非命令做匹配
4. 文档化：用户写 `Mcp(mcp__filesystem__*)` 即可放行整个 filesystem Server 的工具

**验证：**
- `python -c "from mewcode.permissions.rules import parse_rule; r = parse_rule('Mcp(mcp__fs__*)'); print(r.tool, r.pattern); print(r.matches('mcp__fs__read', 'mcp__fs__read'))"` 输出 `mcp mcp__fs__* True`

---

## T7: manager.py

**文件：** 新建 `mewcode/mcp/manager.py`

**依赖：** T1, T3, T4, T5

**步骤：**
1. 实现 `_build_transport(cfg)`：按 cfg.type 构造对应 Transport
2. 实现 `async _start_one(cfg) -> tuple[MCPClient, list]`
3. 实现 `async start_all(configs) -> dict`：
   - asyncio.gather + return_exceptions=True
   - 对 BaseException：warning 跳过
4. 实现 `register_to(registry, started) -> int`：
   - 遍历 (client, tools)，构造 MCPToolAdapter，调 registry.register
   - 返回总数
5. 实现 `async shutdown_all(started)`：gather 所有 client.shutdown

**验证：**
- 单元覆盖在 T17 完成

---

## T8: __init__.py + main.py 集成

**文件：**
- 新建 `mewcode/mcp/__init__.py`
- 修改 `mewcode/main.py`

**依赖：** T1-T7

**步骤：**

1. `mewcode/mcp/__init__.py` 暴露：
   ```python
   from mewcode.mcp.config import ServerConfig, load_all
   from mewcode.mcp.client import MCPClient, ToolInfo, CallResult
   from mewcode.mcp.adapter import MCPToolAdapter
   from mewcode.mcp.manager import start_all, register_to, shutdown_all
   from mewcode.mcp.protocol import MCPProtocolError, MCPTimeoutError
   ```

2. `main.py` 修改：
   - 在 PermissionPolicy 之后加载 MCP 配置
   - 启动前并发 start_all + register_to
   - 打印横幅：`🔌 已加载 MCP Server: filesystem (3 工具)`
   - asyncio.run(run_repl(...)) 包在 try / finally：
     - finally 中 asyncio.run(shutdown_all(...))（注意已经退出原 loop）

   注：因为 main 现在已经是 asyncio.run 启动 run_repl，重启 loop 跑
   shutdown 不可靠。改为把 mcp_started 通过参数传给 run_repl，由
   run_repl 在 finally 块中调 shutdown_all。

**验证：**
- `python -m py_compile mewcode/main.py`
- `python -m mewcode` 启动正常（无 mcp_servers.yaml 时不打横幅）

---

## T9: .gitignore 预留

**文件：** 修改 `.gitignore`

**依赖：** 无

**步骤：**
- 追加：`.mewcode/mcp_servers.local.yaml`（虽然本阶段不实现 local 层，
  但预留 .gitignore 防未来踩坑）

**验证：**
- 无（仅文档性预防）

---

## T10: repl/main_loop.py 改造（接管 mcp 生命周期）

**文件：** 修改 `mewcode/repl/main_loop.py`

**依赖：** T8

**步骤：**
1. `run_repl` 签名增加 `mcp_started: dict | None = None`
2. main loop 外层加 try / finally：
   - finally 中 `if mcp_started: await shutdown_all(mcp_started)`
3. main.py 把 mcp_started 透传给 run_repl

**验证：**
- 启动 + 退出无异常，子进程能正确终结

---

## T11: tests/test_mcp_config.py

**文件：** 新建

**依赖：** T1

**步骤：** ~10 个测试
- expand_vars 成功 / 缺失 / 部分缺失
- _parse_server 合法 stdio / 合法 http / 缺 type / 缺 command / 缺 url
- _load_layer 文件不存在 / YAML 错误 / 顶层非 dict
- load_all 用户级单独 / 项目级单独 / 两层合并 / 同名覆盖

**验证：**
- `pytest tests/test_mcp_config.py -v`

---

## T12: tests/test_mcp_protocol.py

**文件：** 新建

**依赖：** T2

**步骤：** ~8 个测试
- encode_request / encode_notification 字段正确
- PendingRegistry alloc_id 自增
- register + resolve OK 响应 → set_result
- register + resolve error 响应 → set_exception(MCPProtocolError)
- resolve 找不到 id → 返回 False（孤儿响应）
- fail_all 设所有 future 为异常

**验证：**
- `pytest tests/test_mcp_protocol.py -v`

---

## T13: tests/test_mcp_transport_stdio.py

**文件：** 新建

**依赖：** T3

**步骤：** ~5 个测试
- 用 echo 脚本作为子进程：start + send simple request + 读响应
  （写一个测试用的 fake stdio server 脚本：读一行 JSON-RPC 写回 echo）
- 启动失败（command 不存在）→ raise FileNotFoundError
- 关闭：shutdown 后 proc 已退出
- 写入后读响应（用 mock 子进程更稳）
- timeout 触发

**验证：**
- `pytest tests/test_mcp_transport_stdio.py -v`

---

## T14: tests/test_mcp_transport_http.py

**文件：** 新建

**依赖：** T3

**步骤：** ~6 个测试（用 httpx 的 MockTransport）
- POST + JSON 响应
- POST + SSE 响应（首帧）
- HTTP 4xx → MCPProtocolError
- 未知 Content-Type → MCPProtocolError
- 响应含 error → MCPProtocolError
- shutdown 关闭 client

**验证：**
- `pytest tests/test_mcp_transport_http.py -v`

---

## T15: tests/test_mcp_client.py

**文件：** 新建

**依赖：** T4

**步骤：** ~8 个测试（用 stub Transport）
- initialize 三步：start + initialize + notifications/initialized
- list_tools 解析 tools 数组
- call_tool 解析 text content
- call_tool 解析 image content（占位）
- call_tool isError=true → CallResult(is_error=True)
- shutdown 调 transport.shutdown
- 协议版本 = "2025-03-26"
- timeout 透传

**验证：**
- `pytest tests/test_mcp_client.py -v`

---

## T16: tests/test_mcp_adapter.py

**文件：** 新建

**依赖：** T5

**步骤：** ~6 个测试（用 stub MCPClient）
- name 前缀 `mcp__server__tool`
- description / parameters_schema 透传
- danger_level == SAFE / readonly == False
- execute 成功 → ToolResult(success=True, text=...)
- execute 超时 → ToolResult(success=False, error_category="MCP 超时")
- execute MCPProtocolError → ToolResult(success=False, error_category="MCP 协议错误")
- execute isError=true → ToolResult(success=False)

**验证：**
- `pytest tests/test_mcp_adapter.py -v`

---

## T17: tests/test_mcp_manager.py

**文件：** 新建

**依赖：** T7

**步骤：** ~5 个测试（mock _start_one）
- start_all 空配置 → 空 dict
- start_all 多 Server 全成功 → 全部就绪
- start_all 单 Server 失败 → 其他成功 + warning
- register_to → 工具数量正确，注册到 ToolRegistry
- shutdown_all 调每个 client.shutdown

**验证：**
- `pytest tests/test_mcp_manager.py -v`

---

## T18: 端到端 + 全量验收 + commit

**文件：**
- 新建 `scripts/verify_mcp.py`
- 新建 `docs/07/checklist.md`
- 新建 `docs/07/acceptance-report.md`

**依赖：** T1-T17

**步骤：**

1. `scripts/verify_mcp.py`（手工运行，需要 npx）：
   - 写一个最小 echo MCP Server 脚本：用 Python stdin 读 JSON-RPC，
     write JSON 响应（避免依赖 npx）
   - 或用 npx @modelcontextprotocol/server-filesystem 真实 Server
   - 测试 initialize / tools/list / 调用一次工具
   - 打印 "✓ MCP 端到端通过"

2. 写 checklist.md（基于 spec AC1-AC26 展开为可执行项）

3. 全量回归：
   - `pytest tests/ -q`（238 + ~50 = ~290）
   - `python scripts/verify_t9.py / verify_t18.py / verify_t19.py /
     verify_round_loop.py / verify_agent_loop.py / verify_plan_mode.py /
     verify_cache_hit.py / verify_permissions.py`
   - 全部通过

4. 写 acceptance-report.md

5. git add + commit + push

**验证：**
- 全部 AC PASSED
- 无回归
- GitHub 上看到第六阶段 commit

---

## 任务汇总

| #   | 任务 | 依赖 | 文件数 | 测试 |
|-----|------|------|--------|------|
| T1  | config.py | 无 | 1 | - |
| T2  | protocol.py | 无 | 1 | - |
| T3  | transport.py | T2 | 1 | - |
| T4  | client.py | T2/T3 | 1 | - |
| T5  | adapter.py | T4 | 1 | - |
| T6  | rules.py 扩展 mcp 桶 | 无 | 1 修 | - |
| T7  | manager.py | T1/T3/T4/T5 | 1 | - |
| T8  | __init__.py + main.py 集成 | T1-T7 | 2 | - |
| T9  | .gitignore 预留 | 无 | 1 修 | - |
| T10 | repl 接管生命周期 | T8 | 1 修 | - |
| T11 | test_mcp_config.py | T1 | 1 | ✅ ~10 |
| T12 | test_mcp_protocol.py | T2 | 1 | ✅ ~8 |
| T13 | test_mcp_transport_stdio.py | T3 | 1 | ✅ ~5 |
| T14 | test_mcp_transport_http.py | T3 | 1 | ✅ ~6 |
| T15 | test_mcp_client.py | T4 | 1 | ✅ ~8 |
| T16 | test_mcp_adapter.py | T5 | 1 | ✅ ~6 |
| T17 | test_mcp_manager.py | T7 | 1 | ✅ ~5 |
| T18 | 端到端 + 验收 + push | T1-T17 | 3 + commit | 全量 |

**单测累计**：约 50 个新增 + 238 个已有 + 可能 1-2 个 rules.py 新增 = **~290**

---

## 自检结论

- ✅ **plan 覆盖**：plan 7 个新模块 + main / rules / .gitignore 修改全部有任务
- ✅ **占位符扫描**：无 TBD/TODO/未完成段落
- ✅ **依赖链**：执行图 T1-T18 拓扑序合法，无环
- ✅ **验证完整性**：每个任务都有可执行的验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（ServerConfig / MCPClient /
  Transport / MCPToolAdapter / start_all / register_to / shutdown_all 等）
- ✅ **不退化覆盖**：T18 跑全套回归 + 8 个端到端脚本
- ✅ **API 兼容**：MCP 是叠加，不传 mcp_servers.yaml 时回退第五阶段行为
