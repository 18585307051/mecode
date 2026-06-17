# MewCode 第一阶段——T20 烟雾测试与 T21 全量验收手册

> 自动可验证的部分（C1-C5、AC2-AC4、AC9-AC10、AC24、I1-I5、D1-D2）
> 已经在开发过程中跑通；本手册列出**必须在真实交互式终端**里手工
> 验证的项目，全部应在 Git Bash + tmux 环境下进行。
>
> 项目根：`e:\AI\vscode_project\mecode`
> 启动命令推荐：`python -m mewcode`（避免 PATH 配置）
> 也可以启动：`mewcode`（前提：`%APPDATA%\Python\Python313\Scripts`
> 已加入 PATH）

---

## 一、自动验证总结（开发期间已跑通）

| 编号 | 项目 | 验证方式 | 状态 |
|------|------|---------|------|
| C1 | pip install -e . 成功 | T1 | ✅ |
| C2 | mewcode 包可导入，版本 0.1.0 | T1 | ✅ |
| C3 | 31 个单元测试全过 | `pytest tests/ -q` | ✅ |
| C4 | 全部源文件语法合法 | `python -m compileall mewcode/` | ✅ |
| AC2 | 缺失配置文件→ 退出码 1 | scripts/verify_t18_config_errors.py | ✅ |
| AC3 | 非法配置（default 指向不存在）→ 退出码 1 | 同上 | ✅ |
| AC4 | api_key 不回显 | 同上（用 sk-LEAK-TEST 验证） | ✅ |
| AC9 | Anthropic 协议流式可用 | scripts/verify_t9.py | ✅ |
| AC10 | OpenAI 协议流式可用 | scripts/verify_t11.py | ✅ |
| AC13 (脚本版) | thinking 流式拿到 215 思考块 | scripts/verify_t10.py | ✅ |
| AC24 | 协议扩展性可见 | tests/test_provider_registry.py | ✅ |
| I2 | rich 仅在 renderer.py 与 main.py 出现 | grep | ✅ |
| I4 | pyproject.toml 不含 anthropic / openai | grep | ✅ |
| I5 | mewcode.yaml 被 .gitignore 屏蔽 | `git check-ignore` | ✅ |
| D1 | 依赖列表精简 | pyproject.toml 审阅 | ✅ |
| D2 | requires-python = ">=3.10" | pyproject.toml 审阅 | ✅ |

剩余 16 个 AC 必须在真实终端中手工验证（下面 T20 + T21 流程）。

---

## 二、T20 烟雾测试（Git Bash + tmux）

### 准备

如果还没装 tmux，参考 task.md T20 步骤 2（scoop 装 msys2 → pacman 装 tmux）。
或退而求其次用 Windows Terminal（spec N4 已包含），但 AC23 验收报告
中需注明"未在 tmux 内验证"。

### 启动

```bash
cd /e/AI/vscode_project/mecode
tmux new -s mewcode-test
python -m mewcode
```

### 烟雾场景（5 项）

#### S1 启动可见
- 看到横幅含 `deepseek-anthropic / anthropic / deepseek-v4-pro[1m]`
- 看到 `> ` 提示符

#### S2 单轮对话 + Markdown
- 输入：`用一句话介绍 Python，给一个示例代码`
- 观察：流式逐字打印（不是整段一次性出现）
- 观察：标题/代码块带样式（不是裸的 `#`、` ``` `）
- 观察：回复结束后出现一行灰字 `↑ N tokens · ↓ M tokens`

#### S3 多轮上下文
- 第 1 轮：`我叫小明`
- 第 2 轮：`今天天气怎么样` （随便答即可）
- 第 3 轮：`我刚才说我叫什么？`
- AI 第 3 轮回复中含"小明"

#### S4 切协议 + thinking
- `/provider deepseek-openai` → 看到切换信息
- `/think on` → 应提示"当前协议（openai）不支持 extended thinking"
- `/provider deepseek-anthropic` → 切换信息
- `/think on` → "extended thinking 已开启"
- 输入：`证明素数有无穷多个，简要说明思路`
- 观察：先出现 `▎思考中…` + 灰色斜体思考流
- 观察：思考结束后空行分隔
- 观察：再出现正常样式的最终回复

#### S5 退出
- `/exit`
- 在 Git Bash 中 `echo $?` 输出 `0`

### 任何失败场景定位回对应 T 任务修复，回头重跑 S1-S5。

---

## 三、T21 全量验收（按 checklist.md）

> 在烟雾测试通过的基础上，按 checklist.md 逐项跑。下面给出每条
> 的具体操作命令。

### 编译与测试基础

```bash
# C1
pip install -e .

# C2
python -c "import mewcode; print(mewcode.__version__)"

# C3
pytest tests/ -v

# C4
python -m compileall mewcode/ -q

# C5
where mewcode || python -m mewcode --help 2>&1 | head -5
```

### 启动与配置

```bash
# AC1（手工）：在含合法 mewcode.yaml 的目录跑 mewcode，观察横幅 + > 提示符
python -m mewcode

# AC2-AC4（脚本）：
python scripts/verify_t18_config_errors.py
```

### 多轮对话 / Markdown / 流式（手工）

进 mewcode REPL 后：
- AC5 流式：`用一段话介绍 Python` —— 观察逐字出现
- AC6 Markdown：`用 Markdown 列 3 个 Python 数据结构，每个配示例代码`
- AC7 多轮：`我叫小明` → 任意问题 → `我刚才告诉你我叫什么？` → AI 答"小明"
- AC8 输入历史：连续提交 3 条 prompt，按上方向键应能调出
- N2 流式不阻塞：发"写 1000 字关于猫的文章"，按 Ctrl+C，秒级停止

### Provider 协议

```bash
# AC9 Anthropic
python scripts/verify_t9.py

# AC10 OpenAI
python scripts/verify_t11.py
```

REPL 内手工：
- AC11a `/providers` → `/provider deepseek-openai` → 再 `/providers` 看当前标记移动
- AC11b 切换前说"我叫小明"，切换后问"我刚才说我叫什么"，AI 不知道

### Extended Thinking（REPL 手工）

- AC12 默认状态：发对话，无灰色斜体、无 ▎思考中… 标记
- AC13 `/think on` 后发"证明素数有无穷多个" → 看到思考流 + 正文
- AC14 `/provider deepseek-openai` → `/think on` 应提示"不支持"

### 内置斜杠命令（REPL 手工）

- AC15 `/help` 输出含 7 个命令
- AC16a `/clear` 后 AI 不再记得之前说的
- AC16b `/clear` 后按上方向键仍能调历史 prompt
- AC17 `/foobar` 显示"未知命令"，未发请求
- AC18 `/exit` 与 `/quit` 都能退出，`echo $?` 为 0

### 用量展示（REPL 手工）

- AC19a 任意对话后看灰字一行 `↑ X · ↓ Y`
- AC19b `/think on` 后对话观察是否含"思考 N tokens"
  （DeepSeek 后端实测不返回独立思考 token 字段，所以可能仍只显示 ↑/↓）

### 错误处理（REPL 手工）

- AC20：编辑 mewcode.yaml 把 api_key 改为 sk-invalid，重启
       `python -m mewcode`，发对话，观察红字 `[鉴权失败]` 或 `[HTTP 错误]`，
       不重试，回到 `>` 提示符
- N3：上一项错误信息含具体类别和原因

### 中断语义（REPL 手工）

- AC21：发"写 1000 字关于猫的文章" → 按 Ctrl+C → 立即停止 →
       下一轮 `你刚才说了什么？` → AI 表明不记得
- AC22：在空白 `>` 下 Ctrl+C → 提示再按一次 → 再按一次 → 退出，
       `echo $?` 为 0

### 终端兼容性（tmux 手工）

- AC23：在 tmux 会话中跑完整 10 步流程（task.md T20 列表）

### 模块集成（代码审查 + 自动）

```bash
# I1 启动横幅与 yaml 一致：手工核对
# I2 rich 仅在 renderer / main：
grep -rE "from rich|import rich" mewcode/

# I3 中文注释：抽查 5 个文件
# I4 不引入官方 SDK：
grep -E "anthropic|openai" pyproject.toml

# I5 mewcode.yaml 不进 git：
git check-ignore mewcode.yaml
git status --short | grep mewcode.yaml
```

### 依赖一致性

```bash
# D1 依赖列表精简：审阅
cat pyproject.toml | grep -A 6 "^dependencies"

# D2 Python ≥ 3.10：
python --version
grep requires-python pyproject.toml
```

---

## 四、T21 验收报告模板

完成上述全部条目后，按以下格式生成 `docs/02/acceptance-report.md`：

```markdown
## 验收报告

### 通过（N/M）
- [x] AC1 — 证据：横幅显示 deepseek-anthropic/anthropic/deepseek-v4-pro[1m]
- [x] AC2 — 证据：scripts/verify_t18_config_errors.py 输出 ✓ AC2 PASSED
- ...

### 未通过（如有）
- [ ] ACx — 预期：... 实际：... 修复方案：...

### 端到端
- [x] AC23 tmux 10 步流程 — 全部通过，无控制字符泄漏
```

---

## 五、已知限制

1. **AC19b 思考 token**：DeepSeek 通过 Anthropic 协议端点返回的 usage
   中不单独包含思考 token 字段，所以即便开启 thinking，用量行也只
   显示 ↑/↓ 两项。spec F13 已规定"后端未返回时省略"，符合预期。
   官方 Anthropic API 也未在 message_delta.usage 中返回独立思考 token
   字段（截至 anthropic-version 2023-06-01），属于协议层限制。

2. **mewcode.exe 不在 PATH**：因为 anaconda 用户级 site-packages 写
   入 `%APPDATA%\Python\Python313\Scripts`，该路径未在系统 PATH 中。
   使用 `python -m mewcode` 启动可绕过。
