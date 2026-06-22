# MewCode 第七阶段 Checklist

> 验证环境：Windows + PowerShell 5.x，项目根 `e:\AI\vscode_project\mecode`。
> 启动命令 `python -m mewcode`。
> 全部通过后第七阶段完成。

## 编译与测试基础

- [ ] **C1 项目可安装** — 继承前阶段
- [ ] **C2 包可导入** — `import mewcode` → `0.1.0`
- [ ] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 ~309 passed
- [ ] **C4 全部源文件语法合法** — `python -m compileall mewcode/ -q`
- [ ] **C5 命令行入口可调用** — `python -m mewcode`
- [ ] **C6 mewcode.instructions 可导入** —
      `python -c "from mewcode.instructions import InstructionsLoader; print('ok')"`

## 文件查找（spec F1）

- [ ] **AC1 找到第一个匹配** — `test_instructions_loader.py::test_find_AGENTS_first`
      项目级同时有 AGENTS.md + CLAUDE.md → 加载 AGENTS.md
- [ ] **AC2 候选都不存在** — `test_no_candidates_found` 通过
- [ ] **AC17 多名兼容** — 项目级仅 CLAUDE.md → 加载 CLAUDE.md，标题
      显示 `./CLAUDE.md`

## 三层加载与拼接（spec F2 / F3）

- [ ] **AC3 三层全空 → None** — `test_all_empty_returns_none` 通过
- [ ] **AC4 三层顺序拼接** — 三层都有内容 → 输出按"用户全局规则 →
      项目规则 → 本地规则"顺序
- [ ] **AC5 缺一层不出空标题** — 仅项目级有内容 → 输出只有项目规则段

## 文件大小与编码（spec F5 / F6）

- [ ] **AC6 8KB 限制** — 写 9KB 文件 → 加载后字节 ≤ 8KB + 含截断标记
- [ ] **AC7 读取失败容错** — mock PermissionError → warning + 视为空
- [ ] **AC8 非 UTF-8 容错** — GBK 文件 → warning + 视为空

## /instructions 命令

- [ ] **AC9 show 有内容** — `test_instructions_command.py::test_show`
      通过
- [ ] **AC10 show 全空** — 提示"未加载任何项目指令"
- [ ] **AC11 reload 未变** — `test_reload_no_change` 通过
- [ ] **AC12 reload 已变** — `test_reload_changed` 通过：rebuild
      callable 被调用

## 启动横幅（spec F7）

- [ ] **AC13 横幅显示** — 项目级有 AGENTS.md → 启动看到
      `📋 项目指令: 项目级 (X.XKB)`
- [ ] **AC14 全空不打印** — 无任何 AGENTS.md → 不打印 📋 横幅

## 集成（spec F4 / F11）

- [ ] **AC15 注入 system_prompt** — `verify_instructions.py` 验证
      session.system_prompt 含 `## 自定义指令` + H3 标题

## 不退化（spec N5 / AC16）

- [ ] **AC16a 已有单测全过** — pytest tests/ -q ~309 passed
- [ ] **AC16b 已有端到端不退化** —
      verify_t9 / verify_t18 / verify_t19 / verify_round_loop /
      verify_agent_loop / verify_plan_mode / verify_cache_hit /
      verify_permissions / verify_mcp 全过
- [ ] **第六阶段命令不变** — /clear /provider /think /plan /do
      /permissions /exit /help /quit 行为不变
- [ ] **MCP 子模块不变** — 不动 mewcode/mcp/

## 端到端真实集成

- [ ] **verify_instructions.py 通过** —
      `python scripts/verify_instructions.py`：
      - 临时项目级 AGENTS.md → load_all 返回含 H3 标题字符串
      - build_system_prompt 注入后含 `## 自定义指令`
      - reload_and_check：相同时返回 (False, ...)
      - reload_and_check：改文件后返回 (True, ...)
      - 最末打印 `✓ 项目指令端到端通过`

## 模块边界（plan I1）

- [ ] **I1 instructions 模块独立** — `mewcode/instructions/*.py`
      不依赖 chat / providers / render / commands / permissions / mcp
- [ ] **I2 中文优先** — 错误提示与命令文档全中文
- [ ] **I3 不引入新依赖** — pyproject.toml dependencies 仍 4 项

## Windows 兼容

- [ ] **Windows 路径** — `Path.home() / Path.cwd()` 跨平台正常工作
- [ ] **Windows 编码** — 启动横幅 `📋` emoji 正常显示

## 待手工验证

- [ ] **真实 AGENTS.md 接入** — 项目根创建 AGENTS.md，启动 mewcode
      看到 📋 横幅；模型对话中遵守规则
- [ ] **改文件 + /instructions reload** — 改 AGENTS.md → /instructions
      reload → 看到"已重新加载"提示，session.system_prompt 已更新
- [ ] **/instructions show** — 完整打印当前生效指令文本

## 自动验证小计

预计约 19 项可自动验证（加载 / 拼接 / 文件限制 / 命令 / 集成 / 不退化）。

## 失败处理

任何项失败 → 定位到对应 T 任务 → 修复 → 重跑 → 更新 acceptance-report.md。
全部通过后 close 第七阶段。
