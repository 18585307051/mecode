# MewCode 第五阶段 Checklist

> 每一项通过运行代码或观察终端行为来验证。
> 验证环境：Windows + PowerShell 5.x，项目根 `e:\AI\vscode_project\mecode`，
> 启动命令 `python -m mewcode`。
> 全部通过后第五阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装** — 继承前阶段
- [ ] **C2 包可导入** — `python -c "import mewcode; print(mewcode.__version__)"` → `0.1.0`
- [ ] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **~175 passed**
      （145 已有 + 约 30 第五阶段新增）
- [ ] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q`
- [ ] **C5 命令行入口可调用** — `python -m mewcode`

## 黑名单层（spec F2）

- [ ] **AC1 黑名单硬拦截** —
  通过 `tests/test_blocklist.py`：
  - `match_blocklist("rm -rf /")` 返回非 None
  - `match_blocklist("rm -rf ~")` 返回非 None
  - `match_blocklist(":(){:|:&};:")` 返回非 None
  - `match_blocklist("curl http://x | sh")` 返回非 None
  - `match_blocklist("dd of=/dev/sda")` 返回非 None
  - `match_blocklist("git status")` 返回 None
  - `match_blocklist("rm tmp/x.txt")` 返回 None（普通 rm 不拦）

- [ ] **AC2 黑名单不可被 yolo 绕过** —
  通过 `test_permissions_policy.py`：
  - `PermissionPolicy(cwd, mode_override="yolo").check("run", {"command":"rm -rf /"})`
    返回 `Decision(action="deny", error_category="黑名单拦截")`

## 沙箱 + TOCTOU（spec F3）

- [ ] **AC3 TOCTOU 防御** —
  通过 `test_sandbox_toctou.py`：
  - mock `os.fstat` 返回 inode A、`os.lstat` 返回 inode B
  - `sandbox.safe_open(path) as f` 抛 `PathRaceConditionError`

- [ ] **AC4 沙箱仍生效** —
  通过 `test_sandbox.py`（继承第二阶段）：
  - read / write / edit 调用 cwd 外路径仍拒绝
  - safe_open 越界路径抛 PathOutOfSandboxError

- [ ] **safe_open 正常读写** —
  通过单测：tmp 文件下 `with sandbox.safe_open(path, "r") as f: f.read()` 正常

## 规则解析（spec F4）

- [ ] **AC5 规则 YAML 解析** —
  通过 `test_permissions_rules.py`：
  - `parse_rule("Bash(git *)")` → `Rule(tool="run", pattern="git *", ...)`
  - `parse_rule("Read(**/*.py)")` → `Rule(tool="read", pattern="**/*.py", ...)`
  - `parse_rule("invalid")` → None
  - `parse_rule("UnknownTool(*)")` → None
  - `parse_rule("Bash()")` → None（空模式）

- [ ] **AC6 前缀匹配语义** —
  通过单测：
  - `Rule("run", "git *", "...").matches("run", "git status")` → True
  - `Rule("run", "git *", "...").matches("run", "git push origin main")` → True
  - `Rule("run", "git *", "...").matches("run", "cd /tmp && git status")` → False
  - `Rule("read", "src/**/*.py", "...").matches("read", "src/a/b.py")` → True

## 三层合并（spec F5）

- [ ] **AC7 三层合并优先级** —
  通过 `test_permissions_loader.py`：构造 tmp 三层文件：
  - 用户级 mode=strict / 项目级空 / 本地级 mode=yolo
  - `load_all` 返回的 `PermissionConfig.mode == "yolo"`
  - allow/deny 按 本地→项目→用户 顺序拼接

- [ ] **缺失文件不报错** —
  通过单测：仅本地级文件存在 → load_all 不抛异常 → mode 取本地或默认

- [ ] **YAML 解析失败给警告** —
  通过单测：写入非法 YAML（如 `mode: [invalid`）→ load_layer 打印 warning
  → 返回空规则

## 权限模式（spec F6）

- [ ] **AC8 yolo 模式放行** —
  通过单测：mode=yolo + 任意非黑名单命令 → Decision("allow")
  （但 rm -rf / 仍 deny）

- [ ] **AC9 strict 模式行为** —
  通过单测：mode=strict + 命中 allow 的命令 → 仍走人在回路 ask
  （strict 比 default 更严格——本阶段实现可暂用 default 行为，即命中 allow 直接通过；
   strict 与 default 的实际区别在 AC8 的实现细节中体现，**本验收宽松**）

- [ ] **AC18 yolo 警告横幅** —
  启动 mewcode 时 mode=yolo（任意层 YAML 设置）→ banner 打印
  `⚠️ 权限模式：YOLO（除致命黑名单外全部放行）`

## 人在回路（spec F7）

- [ ] **AC10 四种回答行为** —
  通过 `test_permissions_interactive.py` mock 输入：
  - `y` → "once"
  - `s` → "session"
  - `a` → "forever" + 写入 local YAML
  - `n` → "deny"
  - 回车 → "deny"
  - EOF → "deny"
  - Ctrl+C → 抛 ConfirmCancelled

- [ ] **a 选项写盘** —
  通过单测：a 后 `.mewcode/permissions.local.yaml` 存在且 allow 列表含新规则

- [ ] **a 选项去重** —
  通过单测：连续两次 a 添加同一规则 → 文件中只出现一次

## 拒绝不终止 Loop（spec F8）

- [ ] **AC11 拒绝不终止 Loop** —
  通过 `test_chat_round_loop.py::test_权限拒绝不终止Loop`：
  - stub policy 第一个工具调用 deny
  - run_turn 完成（return True）
  - messages 含 `ToolResultBlock(is_error=True, content="权限拒绝...")`
  - R2 模型答复正常

- [ ] **拒绝时的错误文本含引导** —
  Decision.reason 含 `如需允许此操作，请告诉用户运行 /permissions allow ...`

## /permissions 命令（spec F9）

- [ ] **AC12 /permissions show** —
  执行命令后 renderer 收到含规则列表的 print_info 调用

- [ ] **AC13 /permissions allow / deny** —
  通过 `test_permissions_command.py`：
  - `/permissions allow "Bash(test*)"` → policy.session_allow 含新规则
  - `/permissions deny "Bash(rm*)"` → policy.session_deny 含新规则

- [ ] **AC14 /permissions mode** —
  - `/permissions mode yolo` → policy.mode == "yolo"
  - `/permissions mode strict` → policy.mode == "strict"
  - `/permissions mode invalid` → 错误提示

- [ ] **AC15 /permissions reload** —
  添加 session 规则后调 reload → session 清空 + 重读 YAML

- [ ] **AC16 /permissions init** —
  - 不存在时 → 创建 `.mewcode/permissions.yaml` + 默认模板内容
  - 已存在 → 提示"文件已存在，未覆盖"

- [ ] **AC20 init 加 .gitignore** —
  执行 init 后 `.gitignore` 含 `.mewcode/permissions.local.yaml`
  （已存在则不重复添加）

## 默认安全（spec N7）

- [ ] **AC17 默认无文件最严格** —
  通过单测：tmp 路径无任何 YAML →
  - mode = "default"
  - `policy.check("run", {"command":"git status"})` 返回 `Decision("ask", ...)`
  - 即每个未匹配工具调用都进入人在回路

## 模块边界（plan I1）

- [ ] **I1 permissions 模块独立** —
  阅读代码确认：
  - `mewcode/permissions/*.py` 不 import chat / providers / render
  - 不 import tools 业务模块（仅依赖 stdlib + PyYAML + prompt_toolkit）
  - chat.engine 通过 `policy.check` + `asker.ask` 调用

- [ ] **I2 中文优先** —
  抽查 5 个新文件 docstring + 用户可见提示均为中文

- [ ] **I3 不引入新依赖** —
  pyproject.toml dependencies 仍仅 4 项

- [ ] **I4 大小写规则** —
  - `Bash(git *)` 与 `bash(git *)` 都被识别为 run 工具的规则
  - glob 匹配按平台大小写敏感性（linux 敏感，win 不敏感）

## 不退化（spec N5）

- [ ] **AC19a 已有单测全过** —
  `pytest tests/ -q` ~175 passed（145 已有 + 30 新增）

- [ ] **AC19b 已有端到端不退化** —
  以下脚本仍通过：
  - `verify_t9.py` （第一阶段纯对话）
  - `verify_t18.py` （Anthropic 工具调用）
  - `verify_t19.py` （OpenAI 工具调用）
  - `verify_round_loop.py` （单轮闭环）
  - `verify_agent_loop.py` （Agent Loop 多轮）
  - `verify_plan_mode.py` （Plan Mode）
  - `verify_cache_hit.py` （prompt cache）
  - **注**：脚本测试时启用 yolo 或预设 allow 规则避免被人在回路阻塞

- [ ] **第四阶段命令不变** —
  `/exit /quit /help /clear /think /plan /do /provider /providers` 行为
  与第四阶段一致

- [ ] **第三阶段 AgentEvent 不变** —
  IterationStart/End/ToolBatchStart/ToolCall/ToolResultEvent/Stopped/UsageTotal
  7 种事件类型不变

## 真实 API 端到端

- [ ] **verify_permissions.py 端到端** —
  `python scripts/verify_permissions.py` 通过：
  - 场景 1：allow 规则生效 → 模型调 git status 通过
  - 场景 2：deny 规则生效 → 模型调 rm 被拒，R2 答复含 "权限拒绝"
  - 场景 3：黑名单拦截 → 模型调 rm -rf / 被拦，error_category="黑名单拦截"
  - stderr 干净

## Windows 兼容

- [ ] **Windows 终端兼容** —
  所有脚本在 Windows PowerShell 5.x 下运行无 traceback 渗漏；
  `⚠️ ●` 等 Unicode 字符正常显示

- [ ] **Windows 黑名单** —
  `match_blocklist("format C:")` 返回非 None；
  `match_blocklist("rmdir /s /q C:")` 返回非 None

## 待手工验证

- [ ] **交互流程** —
  在 REPL 中实际跑一次：
  - 启动 → 输入 prompt 触发未匹配规则 → 看到询问 UI
  - 输入 y → 工具执行 → 下次相同调用再问
  - 输入 s → 工具执行 → 下次相同调用直接通过
  - 输入 a → 工具执行 → 看 `.mewcode/permissions.local.yaml` 含新规则
  - 输入 n → 工具不执行 → 模型 R2 答复含错误
  - Ctrl+C 在 prompt 中 → 整个 turn 取消

- [ ] **yolo 警告横幅** —
  把 mode 改为 yolo 启动 → 看到 `⚠️ YOLO` 横幅

## 自动可验证小计

预计 **~28 项可自动验证**（黑名单 / TOCTOU / 规则 / 合并 / 模式 /
人在回路 / 命令 / 不退化 / 端到端）。

## 失败处理

任何项失败 → 定位到对应 T 任务 → 修复 → 重跑 → 更新 acceptance-report.md。
全部通过后 close 第五阶段。
