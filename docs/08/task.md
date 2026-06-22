# MewCode 第七阶段 Tasks

> 基于已批准的 `docs/08/spec.md` 与 `docs/08/plan.md`。共 10 个任务，
> 覆盖 instructions 子模块、main 集成、命令扩展、单测与端到端验收。

## 文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `mewcode/instructions/__init__.py` |
| 新建 | `mewcode/instructions/loader.py` |
| 修改 | `mewcode/main.py` |
| 修改 | `mewcode/repl/main_loop.py` |
| 修改 | `mewcode/commands/registry.py` |
| 修改 | `mewcode/commands/builtin.py` |
| 新建 | `tests/test_instructions_loader.py` |
| 新建 | `tests/test_instructions_command.py` |
| 新建 | `scripts/verify_instructions.py` |
| 新建 | `docs/08/checklist.md` |
| 新建 | `docs/08/acceptance-report.md` |

共 11 个文件（7 新建 + 4 修改）。

---

## 任务执行顺序图

```
T1 (loader.py) ──┬─→ T3 (main 集成)
T2 (__init__.py)─┘
                  ├─→ T4 (repl 透传)
                  │
T5 (registry 加字段) ─┬─→ T6 (builtin /instructions 命令)
T3 ───────────────────┘
                                ├─→ T7 (test_instructions_loader)
                                ├─→ T8 (test_instructions_command)
                                ├─→ T9 (verify_instructions)
                                └─→ T10 (checklist + acceptance + commit)
```

**关键路径**：T1 → T3 → T6 → T9 → T10（5 步主线）
**可并行**：T2 / T5 / T7 / T8

---

## T1: instructions/loader.py

**文件：** 新建 `mewcode/instructions/loader.py`

**依赖：** 无

**步骤：**
1. 定义常量：_FILE_LIMIT_BYTES=8192、三层 candidates 列表
2. 定义 `LayerInfo` NamedTuple（name/path/display_path/text/bytes_len）
3. 实现 `_read_layer(dir_path, candidates, layer_name, display_prefix)`：
   - 遍历候选，找到第一个存在的文件
   - 读取字节、限制 8KB、UTF-8 解码、错误容错
4. 实现 `InstructionsLoader` 类：
   - `__init__(cwd)`：保存 cwd 与 home
   - `load_all() -> str | None`：三层加载 + H3 标题拼接
   - `current_text() / current_hash() / loaded_layers()`：状态访问
   - `reload_and_check() -> tuple[bool, str|None]`：hash 比对

**验证：**
- `python -c "from mewcode.instructions.loader import InstructionsLoader; from pathlib import Path; print(InstructionsLoader(Path.cwd()).load_all())"` 当前项目无 AGENTS.md → 输出 None

---

## T2: instructions/__init__.py

**文件：** 新建 `mewcode/instructions/__init__.py`

**依赖：** T1

**步骤：**
1. 暴露 `InstructionsLoader` 与 `LayerInfo`
2. `__all__` 列表

**验证：**
- `python -c "from mewcode.instructions import InstructionsLoader; print('ok')"`

---

## T3: main.py 集成

**文件：** 修改 `mewcode/main.py`

**依赖：** T1, T2

**步骤：**
1. 在 `_amain` 函数（async 段）的 MCP 加载之后：
   ```python
   from mewcode.instructions import InstructionsLoader

   instructions_loader = InstructionsLoader(sandbox.cwd)
   instructions_text = instructions_loader.load_all()
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
2. 准备 rebuild callable 给 reload 命令用：
   ```python
   def _rebuild_system_prompt(new_instructions):
       sys_prompt = build_system_prompt(
           cwd=sandbox.cwd,
           tools=sorted(t.name for t in registry),
           custom_instructions=new_instructions,
       )
       session.system_prompt = sys_prompt
   ```
3. 透传 `instructions_loader` + `_rebuild_system_prompt` 给 run_repl

**验证：**
- `python -m py_compile mewcode/main.py`
- 无 AGENTS.md 时启动不打 📋 横幅，行为等同第六阶段

---

## T4: repl/main_loop.py 透传

**文件：** 修改 `mewcode/repl/main_loop.py`

**依赖：** T3

**步骤：**
1. `run_repl` 签名加 `instructions=None` 与 `rebuild_system_prompt=None`
2. CommandContext 构造时透传：
   ```python
   ctx = CommandContext(
       ...,
       instructions=instructions,
       rebuild_system_prompt=rebuild_system_prompt,
   )
   ```

**验证：**
- `python -m py_compile mewcode/repl/main_loop.py`

---

## T5: commands/registry.py 加字段

**文件：** 修改 `mewcode/commands/registry.py`

**依赖：** 无

**步骤：**
1. CommandContext dataclass 加：
   ```python
   instructions: object = field(default=None)
   rebuild_system_prompt: object = field(default=None)  # callable | None
   ```

**验证：**
- 现有 `test_permissions_command.py` 通过（CommandContext 字段都是
  default，向后兼容）

---

## T6: commands/builtin.py + /instructions 命令

**文件：** 修改 `mewcode/commands/builtin.py`

**依赖：** T5, T1

**步骤：**
1. 实现 `_handle_instructions(ctx)` 主分发器
2. 实现 `_instructions_show(ctx)`：
   - loader 为 None → 提示未启用
   - current_text() 为 None → 提示未加载任何
   - 否则 print_info 完整文本
3. 实现 `_instructions_reload(ctx)`：
   - 调 loader.reload_and_check()
   - 内容相同 → 提示未变化
   - 内容不同 → 调 ctx.rebuild_system_prompt(new_text) → 提示重建
4. 在 register_builtins 末尾注册 `/instructions` 命令

**验证：**
- `python -c "from mewcode.commands import COMMANDS; from mewcode.commands.builtin import _ensure_registered; _ensure_registered(); print('instructions' in COMMANDS)"` → True

---

## T7: tests/test_instructions_loader.py

**文件：** 新建

**依赖：** T1

**步骤：** 8-10 个测试
- 文件查找：找到 AGENTS.md 第一个匹配
- 文件查找：项目级有 CLAUDE.md → 加载 CLAUDE.md
- 文件不存在视为空
- 三层全空 → load_all 返回 None
- 单层有内容 → 输出含 H3 标题
- 三层都有内容 → 顺序为 用户→项目→本地
- 8KB 限制：写 9KB 文件 → 截断
- 非 UTF-8 文件 → warning + 跳过
- 文件读取失败 → warning + 跳过
- reload_and_check：内容相同返回 (False, ...)
- reload_and_check：内容不同返回 (True, ...)

**验证：**
- `pytest tests/test_instructions_loader.py -v`

---

## T8: tests/test_instructions_command.py

**文件：** 新建

**依赖：** T6

**步骤：** 4-5 个测试
- /instructions show 无内容时提示
- /instructions show 有内容时打印
- /instructions reload 无变化提示
- /instructions reload 内容变化触发 rebuild
- /instructions 无子命令默认 show

**验证：**
- `pytest tests/test_instructions_command.py -v`

---

## T9: verify_instructions.py

**文件：** 新建 `scripts/verify_instructions.py`

**依赖：** T1-T6

**步骤：**
1. 用 tmpdir 模拟项目级 AGENTS.md
2. 调 InstructionsLoader.load_all() → 验证内容含 H3 标题
3. 调 build_system_prompt(custom_instructions=...) → 验证含 ## 自定义指令
4. 改文件 → reload_and_check → 验证 changed=True
5. 不改 → reload_and_check → 验证 changed=False
6. 打印 `✓ 项目指令端到端通过`

**验证：**
- `python scripts/verify_instructions.py`

---

## T10: checklist + acceptance + commit + push

**文件：**
- 新建 `docs/08/checklist.md`
- 新建 `docs/08/acceptance-report.md`

**依赖：** T1-T9

**步骤：**
1. 写 checklist.md（基于 spec AC1-AC17）
2. 全量回归：
   - `pytest tests/ -q`（297 + ~12 ≈ 309）
   - 8 个旧端到端脚本仍通过
   - `python scripts/verify_instructions.py` 通过
3. 写 acceptance-report.md
4. git add + commit + push

**验证：**
- 全部 AC PASSED
- GitHub 上看到第七阶段 commit

---

## 任务汇总

| #   | 任务 | 依赖 | 文件数 | 测试 |
|-----|------|------|--------|------|
| T1  | loader.py | 无 | 1 | - |
| T2  | __init__.py | T1 | 1 | - |
| T3  | main.py 集成 | T1/T2 | 1 修 | - |
| T4  | repl 透传 | T3 | 1 修 | - |
| T5  | registry 加字段 | 无 | 1 修 | - |
| T6  | /instructions 命令 | T5/T1 | 1 修 | - |
| T7  | test_loader | T1 | 1 | ✅ ~10 |
| T8  | test_command | T6 | 1 | ✅ ~5 |
| T9  | verify_instructions | T1-T6 | 1 | 端到端 |
| T10 | checklist + acceptance + push | T7-T9 | 2 + commit | 全量 |

**单测累计**：约 15 个新增 + 297 个已有 = **~312**

---

## 自检结论

- ✅ **plan 覆盖**：plan 6 个新模块/改动都有任务对应
- ✅ **占位符扫描**：无 TBD/TODO
- ✅ **依赖链**：T1-T10 拓扑序合法
- ✅ **验证完整性**：每个任务都有验证步骤
- ✅ **类型一致性**：plan ↔ task 命名一致（InstructionsLoader / LayerInfo
  / load_all / reload_and_check 等）
- ✅ **不退化覆盖**：T10 跑全套回归 + 8 个端到端
- ✅ **API 兼容**：build_system_prompt 签名不变；不创建 AGENTS.md 时
  行为等同第六阶段
