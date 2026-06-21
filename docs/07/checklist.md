# MewCode 第六阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证。
> 验证环境：Windows + PowerShell 5.x，项目根 `e:\AI\vscode_project\mecode`，
> 启动命令 `python -m mewcode`。
> 全部通过后第六阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装** — 继承前阶段
- [ ] **C2 包可导入** — `python -c "import mewcode; print(mewcode.__version__)"` → `0.1.0`
- [ ] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **~290 passed**
      （238 已有 + 约 50 第六阶段新增）
- [ ] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q`
- [ ] **C5 命令行入口可调用** — `python -m mewcode`
- [ ] **C6 mewcode.mcp 可导入** —
      `python -c "from mewcode.mcp import MCPClient, MCPToolAdapter, start_all, register_to, shutdown_all, ServerConfig; print('ok')"`

## 配置加载与 ${VAR} 展开（spec F1 / F2 / F3）

- [ ] **AC1 配置文件解析** —
      `tests/test_mcp_config.py::test_parse_server_合法stdio`、
      `_合法http` 通过：合法 YAML → ServerConfig 对象

- [ ] **AC2 两层合并** —
      `test_load_all_两层合并` + `test_load_all_同名覆盖` 通过：
      项目级 server 完整覆盖用户级；不同名取并集；缺失文件不报错

- [ ] **AC3 ${VAR} 展开成功** —
      `test_expand_vars_成功` 通过：`${TEST_VAR}` →
      `os.environ["TEST_VAR"]`

- [ ] **AC4 ${VAR} 缺失跳过 Server** —
      `test_parse_server_变量缺失跳过` 通过：未设置的 `${MISSING_VAR}` →
      返回 None + warning 含变量名

- [ ] **缺字段错误提示清晰** —
      `test_parse_server_缺command` / `_缺url` 通过：warning 含 server 名

## JSON-RPC 协议（spec F4）

- [ ] **AC5 JSON-RPC 编解码** —
      `tests/test_mcp_protocol.py::test_encode_request` +
      `test_encode_notification` 通过

- [ ] **AC6 id 异步配对** —
      `test_pending_resolve_OK_响应` + `test_resolve_找不到id返回False` +
      `test_alloc_id_自增` 通过

- [ ] **AC7 JSON-RPC 错误响应** —
      `test_pending_resolve_error响应` 通过：error 字段 →
      future.set_exception(MCPProtocolError)

- [ ] **AC8 JSON-RPC 超时** —
      transport 层覆盖：`test_call_timeout` 通过

- [ ] **fail_all 收尾** —
      `test_fail_all` 通过：传输关闭时所有 pending 都被 set_exception

## stdio 传输（spec F6）

- [ ] **AC9 stdio 启动** —
      `tests/test_mcp_transport_stdio.py::test_start_启动子进程` 通过：
      用一个最小 echo Python 脚本作为 server，verify proc 创建成功

- [ ] **AC10 stdio 读写** —
      `test_call_收发消息` 通过：写一条 JSON-RPC，echo server 回响应，
      reader 路由到 future

- [ ] **AC11 stdio 关闭** —
      `test_shutdown_关闭子进程` 通过：调 shutdown 后 proc.returncode 不为 None

- [ ] **stdio 启动失败抛错** —
      `test_start_命令不存在` 通过：FileNotFoundError 抛出

- [ ] **Windows 进程组** —
      代码层验证 `creationflags=CREATE_NEW_PROCESS_GROUP`（仅 Windows）

## HTTP 传输（spec F7）

- [ ] **AC12 HTTP JSON 响应** —
      `tests/test_mcp_transport_http.py::test_call_json响应` 通过：
      mock httpx 返回 `Content-Type: application/json`

- [ ] **AC13 HTTP SSE 响应** —
      `test_call_sse响应` 通过：mock 返回 `Content-Type: text/event-stream` +
      `data: {...}\n\n`，解析首帧

- [ ] **HTTP 4xx 抛 MCPProtocolError** —
      `test_call_http错误` 通过

- [ ] **HTTP error 字段** —
      `test_call_response含error` 通过：响应含 error → 抛 MCPProtocolError

- [ ] **未知 Content-Type** —
      `test_call_未知content_type` 通过

- [ ] **headers 注入** —
      `test_call_headers注入` 通过：自定义 headers 传给 httpx

## MCPClient 三步流程（spec F5）

- [ ] **AC14 initialize** —
      `tests/test_mcp_client.py::test_initialize_三步流程` 通过：
      transport.start + initialize 请求 + notifications/initialized

- [ ] **协议版本 = 2025-03-26** —
      `test_initialize_protocol_version` 通过：请求 params.protocolVersion
      字段为 "2025-03-26"

- [ ] **AC15 tools/list** —
      `test_list_tools_解析` 通过：返回 list[ToolInfo]，name/description/
      input_schema 字段正确

- [ ] **AC16 tools/call** —
      `test_call_tool_text` + `test_call_tool_image占位` +
      `test_call_tool_isError` 通过

- [ ] **shutdown 调 transport.shutdown** —
      `test_shutdown` 通过

## 适配层（spec F8 / F9 / F10）

- [ ] **AC17 适配层 name 前缀** —
      `tests/test_mcp_adapter.py::test_name_前缀` 通过：
      `MCPToolAdapter(client(name="fs"), original_name="read_file", ...).name`
      == `"mcp__fs__read_file"`

- [ ] **AC18 inputSchema 透传** —
      `test_parameters_schema_透传` 通过：MCP 的 inputSchema 直接进
      Tool.parameters_schema

- [ ] **AC19 适配层响应转换** —
      `test_execute_text` + `test_execute_image_占位` +
      `test_execute_isError` 通过

- [ ] **danger_level / readonly 默认值** —
      `test_默认SAFE_readonly_False` 通过

- [ ] **超时返回 ToolResult** —
      `test_execute_超时` 通过：error_category="MCP 超时"

- [ ] **MCPProtocolError 返回 ToolResult** —
      `test_execute_protocol_error` 通过

## 生命周期管理（spec F11 / F12 / F14）

- [ ] **AC20 并发启动** —
      `tests/test_mcp_manager.py::test_start_all_并发` 通过：3 个 Server
      → asyncio.gather → 全部就绪后返回 dict

- [ ] **AC21 单 Server 失败跳过** —
      `test_start_all_单失败跳过` 通过：3 个中 1 个抛异常 → 其他 2 个仍
      正常返回 + warning 含 server 名

- [ ] **AC22 退出清理** —
      `test_shutdown_all` 通过：所有 client.shutdown 被调，gather 收集

- [ ] **AC23 注册到 ToolRegistry** —
      `test_register_to` 通过：返回总数；registry 含
      `mcp__<server>__<tool>` 所有工具

## main 集成

- [ ] **横幅打印** —
      手工启动：含 `🔌 已加载 MCP Server: <name> (N 工具)`（仅当配置
      存在且至少一个 Server 启动成功）

- [ ] **AC25 无 MCP 配置时行为不变** —
      无 mcp_servers.yaml 时启动正常，不打 🔌 横幅

- [ ] **退出清理** —
      在 REPL 里 /exit 退出 → 子进程被正确关闭（手工观察 ps / Task Manager）

## 权限系统集成（spec N9 / D13）

- [ ] **MCP 工具进入 ask 路径** —
      默认 mode=default + 无规则 → 调用 MCP 工具触发人在回路询问

- [ ] **Mcp() 规则匹配** —
      `python -c "from mewcode.permissions.rules import parse_rule;
      r = parse_rule('Mcp(mcp__fs__*)');
      print(r.matches('mcp__fs__read', 'mcp__fs__read'))"` → True

- [ ] **/permissions allow Mcp(...)** —
      在 REPL 里 `/permissions allow "Mcp(mcp__fs__*)"` 后调用对应工具
      不再询问

## 模块边界（plan I1）

- [ ] **I1 单向依赖** —
      阅读代码确认：
      - `mewcode/mcp/*.py` 不 import chat / providers / render / commands
      - 仅依赖 stdlib + httpx + yaml + mewcode.tools.base
      - chat / providers / commands 不 import mewcode.mcp

- [ ] **I2 中文优先** —
      抽查 7 个新文件 docstring + 用户可见提示均为中文

- [ ] **I3 不引入新依赖** —
      pyproject.toml dependencies 仍 4 项（prompt_toolkit / rich /
      PyYAML / httpx）

## 不退化（spec N5 / AC26）

- [ ] **AC26a 已有单测全过** —
      `pytest tests/ -q` ~290 passed（238 已有 + 50 新增）

- [ ] **AC26b 已有端到端不退化** —
      以下脚本仍通过：
      - `verify_t9.py / verify_t18.py / verify_t19.py`
      - `verify_round_loop.py / verify_agent_loop.py`
      - `verify_plan_mode.py / verify_cache_hit.py / verify_permissions.py`

- [ ] **第五阶段命令不变** —
      `/exit /quit /help /clear /think /plan /do /provider /providers
      /permissions` 行为与第五阶段一致

- [ ] **第三阶段 AgentEvent 不变** — 不变

## 端到端真实集成（AC24）

- [ ] **AC24 verify_mcp.py 端到端** —
      `python scripts/verify_mcp.py` 通过：
      - 启动一个最小测试 MCP Server（Python 脚本，实现 echo / list_tools）
      - initialize / tools/list / 调用一次 echo 工具
      - 打印 "✓ MCP 端到端通过"

## Windows 兼容（spec N6）

- [ ] **Windows 终端兼容** —
      所有脚本在 Windows PowerShell 5.x 下运行无 traceback 渗漏

- [ ] **Windows stdio 子进程** —
      manual 验证：起一个 stdio MCP Server，正常 initialize 与
      tools/list

## 待手工验证

- [ ] **真实 MCP Server 接入** —
      创建 `~/.mewcode/mcp_servers.yaml`：
      ```yaml
      servers:
        time:
          type: stdio
          command: python
          args: ["-c", "..."]  # 一个本地 echo / time server
      ```
      启动 mewcode → 看到 🔌 横幅 → 在 REPL 中让模型调用工具
      → 模型透过权限询问后成功调用

- [ ] **退出清理** —
      启动后查看 task manager 子进程数 → /exit → 1 秒内子进程消失

## 自动验证小计

预计 **~30 项可自动验证**（配置 / 协议 / 传输 / 客户端 / 适配 /
管理 / 不退化）。

## 失败处理

任何项失败 → 定位到对应 T 任务 → 修复 → 重跑 → 更新 acceptance-report.md。
全部通过后 close 第六阶段。
