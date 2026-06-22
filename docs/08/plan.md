# MewCode 第七阶段 Plan

> 基于已批准的 `docs/08/spec.md`。本 plan 分 6 节：架构图、模块设计、
> 技术决策、时序图、文件清单、与第六阶段的兼容矩阵。

## 1. 架构概览

```
┌────────────────────────────────────────────────────────────────┐
│ main.py 启动流程                                                │
│   ...                                                           │
│   1. 加载 mewcode.yaml + permissions + MCP（前阶段已有）          │
│   2. instructions_loader = InstructionsLoader(cwd)               │
│   3. instructions_text = instructions_loader.load_all()          │
│      ← 拼接好的 H3 标题字符串 / 或 None                            │
│   4. sys_prompt = build_system_prompt(                          │
│         cwd, tools,                                             │
│         custom_instructions=instructions_text,  ← 第七阶段接通    │
│      )                                                          │
│   5. session = Session(..., system_prompt=sys_prompt)            │
│   6. 横幅打印（如果非 None）                                      │
│      📋 项目指令: 项目级 (1.2KB)                                  │
│   7. run_repl(..., instructions=instructions_loader)            │
└──────────────┬─────────────────────────────────────────────────┘
               │
               ▼
        ┌──────────────────────────┐
        │ commands.builtin          │
        │   /instructions show      │ → ctx.instructions.current_text()
        │   /instructions reload    │ → ctx.instructions.reload(session, renderer)
        └──────────────────────────┘

┌──────────────────────────────────────┐
│ mewcode/instructions/loader.py        │
│                                       │
│ class InstructionsLoader:             │
│   __init__(cwd)                       │
│   load_all() -> str | None            │
│     按 用户→项目→本地 顺序加载         │
│     三层各自查找 AGENTS.md / CLAUDE.md │
│     拼接为 H3 标题字符串                │
│     缓存 _last_text + _last_hash       │
│                                       │
│   reload(session, renderer)            │
│     重新调 load_all                    │
│     hash 比对决定是否更新 system_prompt│
│                                       │
│   current_text() -> str | None         │
│     返回最近一次加载结果                │
│                                       │
│   loaded_layers() -> list[(name, kb)]  │
│     给横幅用                           │
└──────────────────────────────────────┘
```

### 模块依赖

```
mewcode/instructions/loader.py
  ↓ stdlib only (hashlib + pathlib)
不依赖任何 mewcode 业务模块。

main.py / commands/builtin.py
  ↓
依赖 instructions/loader + system_prompt/builder（已有）
```

## 2. 模块设计

### 2.1 mewcode/instructions/loader.py

```python
"""项目指令文件加载（spec F1-F7, F10, F11）。

三层文件按 用户→项目→本地 顺序加载，每层独立查找候选名（AGENTS.md →
CLAUDE.md → .mewcoderc），找到第一个就停。

错误容错：所有错误都不阻塞启动，只 warning 并跳过该层。

InstructionsLoader 既负责加载也持有"当前生效的指令文本"状态——
/instructions reload 时通过 hash 比对决定是否需要重建 system prompt。
"""

import hashlib
from pathlib import Path
from typing import NamedTuple


_FILE_LIMIT_BYTES = 8 * 1024  # 8KB 上限（spec F5）

_USER_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_PROJECT_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_LOCAL_CANDIDATES = ["AGENTS.local.md", "CLAUDE.local.md"]


class LayerInfo(NamedTuple):
    """一层加载的元信息（供横幅与 show 命令）。"""
    name: str        # "用户级" / "项目级" / "本地级"
    path: Path       # 实际加载到的文件
    display_path: str  # 用于标题显示的相对路径
    text: str        # 文件内容（可能已截断）
    bytes_len: int   # 字节数（截断后）


def _read_layer(
    dir_path: Path,
    candidates: list[str],
    layer_name: str,
    display_prefix: str,
) -> LayerInfo | None:
    """在某一层目录下查找候选文件并加载。

    Args:
        dir_path: 目录绝对路径
        candidates: 候选文件名（按优先级）
        layer_name: "用户级" / "项目级" / "本地级"
        display_prefix: 标题显示前缀（如 "~/.mewcode/" / "./" / "./.mewcode/"）

    Returns:
        LayerInfo 或 None（该层无内容）
    """
    for name in candidates:
        path = dir_path / name
        if not path.exists():
            continue
        try:
            raw_bytes = path.read_bytes()
        except (PermissionError, OSError) as e:
            print(f"⚠️ 项目指令文件 {path} 读不了（已跳过）：{e}")
            return None

        truncated = False
        if len(raw_bytes) > _FILE_LIMIT_BYTES:
            raw_bytes = raw_bytes[:_FILE_LIMIT_BYTES]
            truncated = True
            print(f"⚠️ 项目指令文件 {path} 超过 8KB，已截断")

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            print(f"⚠️ 项目指令文件 {path} 非 UTF-8 编码（已跳过）")
            return None

        if truncated:
            text += "\n\n[... 内容已截断（超过 8KB 上限）...]\n"

        return LayerInfo(
            name=layer_name,
            path=path,
            display_path=display_prefix + name,
            text=text.rstrip() + "\n",
            bytes_len=len(raw_bytes),
        )
    return None


class InstructionsLoader:
    """项目指令加载器。"""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._home = Path.home()
        self._last_text: str | None = None
        self._last_hash: str = ""
        self._last_layers: list[LayerInfo] = []

    def load_all(self) -> str | None:
        """加载三层文件，返回拼接后的 custom_instructions 字符串。

        spec F2 / F3：用户 → 项目 → 本地 顺序拼接，H3 标题包装来源。
        三层全空 → 返回 None。
        """
        layers: list[LayerInfo] = []
        for layer_name, dir_path, candidates, prefix in [
            ("用户级", self._home / ".mewcode", _USER_CANDIDATES, "~/.mewcode/"),
            ("项目级", self._cwd, _PROJECT_CANDIDATES, "./"),
            ("本地级", self._cwd / ".mewcode", _LOCAL_CANDIDATES, "./.mewcode/"),
        ]:
            info = _read_layer(dir_path, candidates, layer_name, prefix)
            if info is not None:
                layers.append(info)

        self._last_layers = layers

        if not layers:
            self._last_text = None
            self._last_hash = ""
            return None

        # 拼接（spec F3）
        parts = ["以下是用户在项目中明确写出的工作规则，应当严格遵守：\n"]
        title_map = {
            "用户级": "用户全局规则",
            "项目级": "项目规则",
            "本地级": "本地规则",
        }
        for layer in layers:
            title = title_map[layer.name]
            parts.append(
                f"### {title}（来自 {layer.display_path}）\n{layer.text}"
            )
        text = "\n".join(parts)

        self._last_text = text
        self._last_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return text

    def current_text(self) -> str | None:
        """返回最近一次 load_all 的结果（不重新加载）。"""
        return self._last_text

    def current_hash(self) -> str:
        return self._last_hash

    def loaded_layers(self) -> list[LayerInfo]:
        """返回最近一次 load_all 加载到内容的层（供横幅 / show 用）。"""
        return list(self._last_layers)

    def reload_and_check(self) -> tuple[bool, str | None]:
        """重新加载，返回 (内容是否变化, 新文本)。

        spec F10 / D9：hash 比对决定是否需要重建 system_prompt。
        """
        old_hash = self._last_hash
        new_text = self.load_all()
        new_hash = self._last_hash
        return (old_hash != new_hash), new_text
```

### 2.2 mewcode/instructions/__init__.py

```python
"""项目指令文件加载（第七阶段）。"""
from mewcode.instructions.loader import InstructionsLoader, LayerInfo

__all__ = ["InstructionsLoader", "LayerInfo"]
```

### 2.3 main.py 集成

在 `_amain` 函数加载 MCP 之后（即位于权限策略与 MCP 启动之后）：

```python
# 第七阶段：加载项目指令文件
from mewcode.instructions import InstructionsLoader

instructions_loader = InstructionsLoader(sandbox.cwd)
instructions_text = instructions_loader.load_all()

# 关键：把 instructions_text 传给已经构造好的 session.system_prompt
# 第四阶段 build_system_prompt 已留接口，重新构造一次
if instructions_text is not None:
    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd,
        tools=sorted(t.name for t in registry),
        custom_instructions=instructions_text,
    )
    session.system_prompt = sys_prompt

    layers = instructions_loader.loaded_layers()
    parts = [
        f"{layer.name} ({layer.bytes_len/1024:.1f}KB)"
        for layer in layers
    ]
    renderer.print_info(f"📋 项目指令: {' + '.join(parts)}")
```

注：原来在 `main.py` 阶段 2 中 sys_prompt 已经构造好了，第七阶段在
`_amain` 里**第二次构造覆盖**它。这保持职责分离：
- 阶段 2（同步段）：构造基础 sys_prompt（不含 instructions）
- `_amain` 内（async 段）：如果有 instructions，重新构造覆盖

简单清晰。如果三层全空，instructions_text 为 None，跳过整段，行为完全
等同第六阶段。

### 2.4 run_repl 透传 instructions_loader

`repl/main_loop.py`：

```python
async def run_repl(
    session, app_config, renderer,
    registry, sandbox, confirmer,
    *,
    policy=None, asker=None,
    instructions=None,   # ← 第七阶段新增
) -> int:
    ...
    # CommandContext 携带
    ctx = CommandContext(
        session=session,
        app_config=app_config,
        args=[],
        renderer=renderer,
        policy=policy,
        instructions=instructions,
    )
```

### 2.5 commands/registry.py 增加字段

```python
@dataclass
class CommandContext:
    ...
    policy: object = field(default=None)
    instructions: object = field(default=None)  # 第七阶段
```

### 2.6 commands/builtin.py 新增 /instructions 命令

```python
async def _handle_instructions(ctx: CommandContext) -> CommandResult:
    """/instructions [show|reload]"""
    loader = ctx.instructions
    if loader is None:
        ctx.renderer.print_info("项目指令系统未启用（启动时未注入 loader）。")
        return CommandResult()

    sub = ctx.args[0] if ctx.args else "show"

    if sub == "show":
        return await _instructions_show(ctx)
    if sub == "reload":
        return await _instructions_reload(ctx)

    ctx.renderer.print_info(f"未知子命令：{sub}")
    ctx.renderer.print_info("用法：/instructions [show|reload]")
    return CommandResult()


async def _instructions_show(ctx) -> CommandResult:
    text = ctx.instructions.current_text()
    if not text:
        ctx.renderer.print_info(
            "当前未加载任何项目指令。建议在项目根创建 AGENTS.md 写明工作规则。"
        )
        return CommandResult()
    ctx.renderer.print_info(text)
    return CommandResult()


async def _instructions_reload(ctx) -> CommandResult:
    """重新加载并按 hash 比对决定是否替换 system_prompt（spec F10）。"""
    from mewcode.system_prompt import build_system_prompt
    from mewcode.tools import ToolRegistry  # 防循环

    changed, new_text = ctx.instructions.reload_and_check()
    if not changed:
        ctx.renderer.print_info(
            "指令未变化，未重新构造 system prompt（cache 仍生效）。"
        )
        return CommandResult()

    # 内容变了 → 重建 system_prompt
    # 注意：reload 时我们不知道 cwd / tool 名（CommandContext 没传），
    # 简化：从 session.system_prompt 上下文中已知信息，直接拼接 instructions
    # 段——但更稳妥是在 main 注入时把"重建函数"也传进来。
    #
    # 决策：在 CommandContext 加 rebuild_system_prompt callable 字段，
    # main 注入。

    if hasattr(ctx, "rebuild_system_prompt") and callable(ctx.rebuild_system_prompt):
        ctx.rebuild_system_prompt(new_text)

    if new_text:
        size_kb = len(new_text.encode("utf-8")) / 1024
        ctx.renderer.print_info(
            f"已重新加载（{size_kb:.1f}KB）。下次请求会重新建立 prompt cache。"
        )
    else:
        ctx.renderer.print_info("已重新加载（三层均无内容）。")
    return CommandResult()
```

注意：reload 需要重建 system_prompt 但 CommandContext 不知道 cwd /
tool 名。**决策**（D6）：CommandContext 增加一个 callable 字段
`rebuild_system_prompt: callable[[str | None], None]`，由 main 注入：

```python
# main.py 准备 ctx 时
def _rebuild(new_instructions: str | None) -> None:
    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd,
        tools=sorted(t.name for t in registry),
        custom_instructions=new_instructions,
    )
    session.system_prompt = sys_prompt

ctx.rebuild_system_prompt = _rebuild
```

为了让 CommandContext 能透传这个 callable，registry.py 加字段。

### 2.7 注册 /instructions

```python
# commands/builtin.py 末尾
register(Command(
    name="instructions",
    aliases=(),
    description="管理项目指令文件（show/reload）",
    handler=_handle_instructions,
))
```

## 3. 技术决策

### D1. 为什么不用 frontmatter

**决策**：纯 Markdown，不解析 frontmatter。

**理由**：
- AGENTS.md / CLAUDE.md 业界都是纯 Markdown
- frontmatter 增加学习成本（YAML 嵌入 Markdown 容易踩坑）
- 元数据（priority / scope）当前没有真实用例
- 后续如有需要再加，向后兼容简单

### D2. 为什么三层是"拼接"而非"覆盖"

**决策**：三层都生效，按用户→项目→本地拼接（不像权限规则那样覆盖）。

**理由**：
- 指令是"叠加"的：用户级写"我偏好简洁"+项目级写"用 Python 3.13" =
  两条都该生效
- 权限规则是"选择"的：项目级允许 git → 用户级 deny git 应当被覆盖
- 不同的合并语义对应不同的语义层

### D3. 为什么用 H3 标题包装来源

**决策**：每层前加 H3 标题（`### 用户全局规则（来自 ~/.mewcode/AGENTS.md）`）。

**理由**：
- 让模型清楚每段来源（"项目里说的" vs "用户全局说的"）
- H3（不是 H2）避免与 system prompt 主结构（H2 模块）冲突
- 显示路径是给模型 + 给用户 show 命令时看，双重价值
- 模型对"严格遵守"框架敏感，加一句开头说明效果显著

### D4. 为什么单文件 8KB 上限

**决策**：单文件最多 8KB，超限截断 + warning。

**理由**：
- 8KB ≈ 2000 token，三层合计约 6000 token，对 200K context 影响小
- 防止用户不小心把整个 README 塞进去
- 截断不阻塞启动（warning + 继续）
- 8KB 这个数字可未来调整，对功能无影响

### D5. 为什么 reload 用 hash 比对

**决策**：reload 时 SHA-256 比对新旧内容，相同则不替换 system_prompt。

**理由**：
- prompt cache 命中依赖 system 内容稳定
- 用户可能频繁 reload 但内容不变（reload 完忘了改文件）
- hash 比对零成本（< 1ms）
- 内容变了再替换是真正的"破坏 cache"——这是预期成本

### D6. 为什么 reload 通过 callable 注入而非内置

**决策**：CommandContext 加 `rebuild_system_prompt` callable，由 main
注入。

**理由**：
- reload 命令 handler 在 commands/builtin.py，里面不应该 import
  build_system_prompt（破坏模块边界）
- main.py 是装配处，知道 cwd / tools / session，由它准备 callable
- 闭包捕获装配时的状态，handler 调时直接用
- 与第五阶段权限系统的 policy 注入同模式

### D7. 为什么三层全空时返回 None 而非空字符串

**决策**：三层都没找到内容 → load_all 返回 None；调用方判断 None
跳过整段。

**理由**：
- None 在 build_system_prompt 里被忽略（不出现 `## 自定义指令` 段）
- 空字符串会出现一个空段，让模型困惑
- None 语义是"完全没有"，更准确

### D8. 为什么不实现 init / edit 命令

**决策**：本阶段只 show + reload。

**理由**：
- init 是一次性需求，用户用 IDE 自己写更顺手
- edit 是"打开默认编辑器"——跨平台麻烦（Win 用 notepad / Linux 用
  $EDITOR / Mac 用 open -t），低 ROI
- show + reload 已经覆盖"查看当前生效"+ "改完立即生效"两个核心场景

### D9. 为什么本地级文件名是 AGENTS.local.md 而非 AGENTS.md

**决策**：本地级目录是 `.mewcode/` 子目录，文件名加 `.local.` 后缀。

**理由**：
- 项目根的 `AGENTS.md` 应该入 git（团队共享）
- 本地级是"本机不入 git"，物理上隔离到 .mewcode/ 子目录 + 文件名加
  .local. 后缀
- 与第五阶段 permissions.local.yaml 模式一致

### D10. 为什么 main.py 第二次构造 system_prompt

**决策**：main.py 阶段 2 先构造一次基础 sys_prompt；_amain 加载完
instructions 后再构造一次覆盖。

**理由**：
- 阶段 2 是同步段，无法 await（虽然加载 instructions 不需要 await，
  但为了与 MCP 加载结构一致，统一放 _amain）
- 重新构造一次的成本极小（< 1ms）
- 代码清晰：阶段 2 负责"基础设施"，_amain 负责"动态注入"

## 4. 时序图

### 4.1 启动加载

```
main         loader              builder           session
 │             │                    │                 │
 │ InstructionsLoader(cwd)          │                 │
 ├────────────►│                    │                 │
 │             │                    │                 │
 │ load_all()                        │                 │
 ├────────────►│                    │                 │
 │             │ _read_layer(用户级)                    │
 │             ├──────► AGENTS.md ✓                    │
 │             │ _read_layer(项目级)                    │
 │             ├──────► AGENTS.md ✓                    │
 │             │ _read_layer(本地级)                    │
 │             ├──────► (无)                           │
 │             │ 拼接 H3 标题                           │
 │ ◄───────────┤ instructions_text                     │
 │                                                      │
 │ build_system_prompt(custom_instructions=text)       │
 ├────────────────────────────────►│                  │
 │ ◄──────────────────────────────┤ sys_prompt         │
 │                                                      │
 │ session.system_prompt = sys_prompt                  │
 ├────────────────────────────────────────────────────►│
 │                                                      │
 │ renderer.print_info("📋 项目指令: 用户级 + 项目级")  │
```

### 4.2 /instructions reload 内容未变

```
user      command       loader         session
 │           │            │              │
 │ /instructions reload   │              │
 ├──────────►│            │              │
 │           │ reload_and_check()         │
 │           ├───────────►│              │
 │           │            │ load_all() 重新读           │
 │           │            │ hash 比对：相同              │
 │           │ ◄──────────┤ (False, text)              │
 │           │ "未变化，未重建 system prompt"           │
 │ ◄─────────┤                          │
```

### 4.3 /instructions reload 内容变化

```
user      command       loader        builder       session
 │           │            │              │             │
 │ /instructions reload   │              │             │
 ├──────────►│            │              │             │
 │           │ reload_and_check()         │             │
 │           ├───────────►│              │             │
 │           │            │ load_all 重新读              │
 │           │            │ hash 不同                    │
 │           │ ◄──────────┤ (True, new_text)             │
 │           │ ctx.rebuild_system_prompt(new_text)       │
 │           ├───────────────────────────►│             │
 │           │            │              │ build_system_prompt
 │           │            │              │ session.system_prompt = ...
 │           │            │              ├────────────►│
 │           │ "已重新加载（X.XKB）"                     │
 │ ◄─────────┤                                          │
```

## 5. 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/instructions/__init__.py` |
| 新建 | `mewcode/instructions/loader.py` |
| 修改 | `mewcode/main.py` (加载 + 横幅 + rebuild callable) |
| 修改 | `mewcode/repl/main_loop.py` (透传 instructions) |
| 修改 | `mewcode/commands/registry.py` (CommandContext 加字段) |
| 修改 | `mewcode/commands/builtin.py` (+/instructions 命令) |
| 新建 | `tests/test_instructions_loader.py` |
| 新建 | `tests/test_instructions_command.py` |
| 新建 | `scripts/verify_instructions.py` |

共 9 个文件（5 新建 + 4 修改）。

## 6. 与第六阶段的兼容矩阵

| 第六阶段行为 | 第七阶段是否保留 | 说明 |
|-------------|-----------------|------|
| run_turn 签名 | ✅ 不变 | |
| Provider stream_chat | ✅ 不变 | |
| ToolRegistry | ✅ 不变 | |
| Sandbox | ✅ 不变 | |
| PermissionPolicy | ✅ 不变 | |
| MCP 子模块 | ✅ 不变 | |
| build_system_prompt 签名 | ✅ 不变 | 第四阶段已留 custom_instructions |
| AgentEvent 7 种 | ✅ 不变 | |
| /clear /provider /think /plan /do /permissions | ✅ 不变 | |
| 297 个已有单测 | ✅ 全过 | 新模块独立 |
| 8 个端到端脚本 | ✅ 全过 | 不传 instructions 行为不变 |
| prompt cache 命中 | ✅ 不变 | system 内容相同时仍命中 |

### 不需要适配的已有测试

无——instructions 是纯新增功能：
- 不创建 AGENTS.md 时（spec AC16），mewcode 启动行为完全等同第六阶段
- 已有测试不依赖 custom_instructions 参数（一直传 None）
- CommandContext 新增字段是可选 default=None，不影响现有命令测试
