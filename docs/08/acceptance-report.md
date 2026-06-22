# MewCode 第七阶段验收报告

> 按 `docs/08/checklist.md` 逐项验证。
> 验证日期：2026-06-17
> 环境：Windows + PowerShell 5.x + Anaconda Python 3.13.9

---

## 一、自动验证结果

### 编译与测试基础

- [x] **C1 项目可安装** — 继承前阶段
- [x] **C2 包可导入** — `import mewcode` → `0.1.0`
- [x] **C3 单元测试全部通过** — `pytest tests/ -q` 输出 **320 passed**
      （297 已有 + 23 第七阶段新增）
- [x] **C4 全部源文件语法合法** — `python -m py_compile mewcode/main.py
      mewcode/repl/main_loop.py mewcode/commands/builtin.py
      mewcode/commands/registry.py` 通过
- [x] **C5 命令行入口可调用** — `python -m mewcode` 启动正常
- [x] **C6 mewcode.instructions 可导入** —
      `from mewcode.instructions import InstructionsLoader` 正常

### 文件查找（spec F1 / Q1）

- [x] **AC1 找到第一个匹配** — `test_find_AGENTS_md_first` 通过：
      同时存在 AGENTS.md + CLAUDE.md → 加载 AGENTS.md
- [x] **AC2 候选都不存在** — `test_no_candidates_returns_none` 通过
- [x] **AC17 多名兼容** — `test_find_CLAUDE_md_when_no_AGENTS` +
      `test_find_mewcoderc_fallback` 通过

### 三层加载与拼接（spec F2 / F3）

- [x] **AC3 三层全空 → None** — `test_no_candidates_returns_none` 通过
- [x] **AC4 三层顺序拼接** — `test_three_layers_order` 通过：
      用户级 → 项目级 → 本地级 顺序 + H3 标题 + framing
- [x] **AC5 缺一层不出空标题** — `test_only_project_layer` 通过

### 文件大小与编码（spec F5 / F6）

- [x] **AC6 8KB 限制** — `test_8kb_truncation` 通过：
      9KB 文件 → 截断 + warning + 截断标记
- [x] **AC7 读取失败容错** — `test_oserror_skipped` 通过：
      mock PermissionError → warning + 视为空
- [x] **AC8 非 UTF-8 容错** — `test_non_utf8_skipped` 通过：
      GBK 字节 → warning + 视为空

### /instructions 命令（spec F8 / F9 / F10）

- [x] **AC9 show 有内容** — `test_show_有内容` 通过
- [x] **AC10 show 全空** — `test_show_无内容` 通过：
      提示 "未加载任何项目指令"
- [x] **AC11 reload 未变** — `test_reload_未变化` 通过：
      hash 相同 → 不触发 rebuild
- [x] **AC12 reload 已变** — `test_reload_内容变化_触发rebuild` 通过：
      改文件后 reload → rebuild callable 被调一次

### 启动横幅（spec F7）

- [x] **AC13 横幅显示** — `verify_instructions.py` 模拟用户级 + 项目级
      → 横幅内容含 `用户级 (X.XKB) + 项目级 (X.XKB)`
- [x] **AC14 全空不打印** — 当前项目未创建 AGENTS.md → 启动不打印 📋

### 集成（spec F4 / F11）

- [x] **AC15 注入 system_prompt** — `verify_instructions.py` 通过：
      `## 自定义指令` 出现在 `## 当前环境` 之后
      `### 项目规则` H3 标题正确

### 端到端（AC15）

- [x] `python scripts/verify_instructions.py` 通过：
      ```
      [1] 临时项目级 AGENTS.md 写入...
      [2] InstructionsLoader.load_all... text 长度: 120 字符 含 H3 标题: ✓
      [3] build_system_prompt 注入... sys_prompt 长度: 2229 字符 含 ## 自定义指令: ✓
      [4] reload_and_check（内容未变）... changed=False ✓
      [5] 改文件后 reload_and_check... changed=True ✓ 新内容含 trio ✓
      [6] 多层拼接（用户级 + 项目级）... 用户级 → 项目级 顺序 ✓
      ✓ 项目指令端到端通过
      ```

### 不退化（spec N5 / AC16）

- [x] **AC16a 已有单测全过** — `pytest tests/ -q` 320 passed
- [x] **AC16b 已有端到端不退化**：
  - `verify_t9.py`: text_chunks=25 usage=True done=True ✓
  - `verify_t18.py`: tool_starts=1 done=True ✓
  - `verify_t19.py`: tool_starts=1 done=True ✓
  - `verify_round_loop.py`: ↑ 459 tokens · ↓ 133 tokens ✓
  - `verify_mcp.py`: ✓ MCP 端到端通过 ✓
- [x] **第六阶段命令不变** — /clear /provider /think /plan /do
      /permissions 行为与第六阶段一致；新增 /instructions 不影响

### 模块边界（plan I1 / N1）

- [x] **I1 instructions 模块独立** —
      `mewcode/instructions/loader.py` 仅依赖 stdlib（hashlib + pathlib +
      typing），不依赖 chat / providers / render / commands /
      permissions / mcp / tools / system_prompt
- [x] **I2 中文优先** — 错误提示、命令文档、warning 全中文
- [x] **I3 不引入新依赖** — pyproject.toml dependencies 仍 4 项

### Windows 兼容

- [x] **Windows 路径** — `Path.home() / Path.cwd()` 跨平台
- [x] **Windows 编码** — 启动横幅 `📋` emoji 通过 _fix_windows_console 正常显示

---

## 二、关键技术成果

### 1. 第四阶段 hook 接通

第四阶段 `build_system_prompt` 留的 `custom_instructions` 参数终于
有人传值了：

```python
build_system_prompt(
    cwd, tools,
    custom_instructions=instructions_text,  # ← 第七阶段填充
    skills=None,                            # ← 留给后续
    memory=None,                            # ← 留给后续
)
```

后续阶段做 Skills / 长期记忆时，可继续按这个模式扩展。

### 2. 多名兼容 + 三层加载

业界三套规则文件（AGENTS.md / CLAUDE.md / .mewcoderc）都能用，
任意层（用户/项目/本地）都能写。从 Cursor / Claude Code 迁来的项目
不用改文件名。

### 3. 智能 reload + cache 保护

`/instructions reload` 用 SHA-256 比对内容：
- 内容相同 → 不重建 system prompt → prompt cache 仍命中
- 内容变了 → 重建 + 提示 "下次请求会重新建立 prompt cache"

避免无意义的 cache 失效。

### 4. 容错优先的启动

任何加载错误都不阻塞 REPL：
- 文件不存在 → 视为空
- 8KB 超限 → 截断 + 提示
- 非 UTF-8 → 跳过 + 提示
- 三层全空 → 行为完全等同第六阶段

---

## 三、测试统计

```text
pytest tests/ -q
320 passed in 14.19s
```

第七阶段新增 23 个测试：
- test_instructions_loader.py: 15
- test_instructions_command.py: 8

---

## 四、待手工验证

- [ ] **真实 AGENTS.md 接入** — 项目根创建 AGENTS.md，启动 mewcode 看到
      `📋 项目指令: 项目级 (X.XKB)`，模型对话遵守规则
- [ ] **改文件 + /instructions reload** — 改文件后 reload 看到
      "已重新加载（X.XKB）" 提示
- [ ] **/instructions show** — 完整打印当前生效指令文本

---

## 五、整体结论

**第七阶段自动验收通过**：

- 17 个 AC 全部有自动或端到端验证
- 320 单测全过
- 项目指令端到端脚本通过
- 第一至第六阶段功能零退化
- 不引入新依赖
- 第四阶段 `custom_instructions` hook 终于接通

MewCode 现在拥有了"项目级 AI 工作规则"的标准入口。用户在项目根写一份
AGENTS.md，所有协作开发者打开 mewcode 时都自动加载——这是 Cursor /
Claude Code 最常用的能力之一，第七阶段花最小工程量补齐。

下一阶段建议（按之前推荐排序）：

1. **B1 `/mcp` 命令族** —— show / reload / disable，与本阶段
   `/instructions` 同模式
2. **B7 上下文压缩** —— 长会话超 token 时摘要早期消息
3. **B6 跨会话长期记忆** —— `## 长期记忆` 段，第四阶段同样留有 hook
