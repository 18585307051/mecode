# MewCode 第九阶段 Plan：会话恢复与长期记忆

> 基于 `docs/09/spec.md`。本阶段把第七阶段项目指令、第八阶段上下文压缩与新增的会话存档/自动记忆串起来，让 MewCode 在重启后能恢复上下文，并逐步沉淀用户偏好与项目知识。

## 1. 架构概览

```text
┌────────────────────────────────────────────────────────────────┐
│ main.py 启动装配                                                │
│                                                                │
│  1. 加载 config/provider/tools/permissions/MCP（已有）           │
│  2. InstructionsLoader(cwd).load_all()                         │
│     └─ 三层优先级 + @include 展开                               │
│  3. SessionArchive(cwd)                                        │
│     ├─ cleanup_expired(days=30)                                │
│     ├─ scan_summaries()                                        │
│     └─ restore_latest() -> messages/session_id                 │
│  4. MemoryManager(cwd)                                        │
│     ├─ load_memory_context() -> memory text                    │
│     └─ ensure system_prompt memory 注入                         │
│  5. build_system_prompt(custom_instructions, memory)           │
│  6. Session(..., messages=restored, session_id=...)            │
│  7. Compactor(cwd)                                             │
│  8. run_repl(... archive, memory_manager, compactor ...)       │
└────────────────────────────────────────────────────────────────┘
```

运行时数据流：

```text
用户输入
  ↓
Session.append_user_text
  ├─ 内存追加 messages
  └─ SessionArchive.append_message(JSONL)
  ↓
请求前
  ├─ MemoryManager.refresh_if_changed(session)  # memory hash 变化才重建 system_prompt
  └─ Compactor.before_request(session)          # 第八阶段 + 恢复后首次超限压缩
  ↓
Agent Loop
  ├─ assistant/tool_results 每次 append 后写 JSONL
  └─ natural stop 后 MemoryManager.schedule_update(...)
       └─ 后台 LLM 判断 create/update/delete/noop
           ├─ 写 notes/*.md
           └─ 重建 index.md（≤200 行/≤25KB）
```

## 2. 模块设计

### 2.1 `mewcode/instructions/loader.py` 扩展

在第七阶段 `InstructionsLoader` 基础上做两处变化：

1. **层级顺序改为高优先级在前**：
   - 本地级：`<cwd>/.mewcode/AGENTS.local.md` / `CLAUDE.local.md`
   - 项目级：`<cwd>/AGENTS.md` / `CLAUDE.md` / `.mewcoderc`
   - 用户级：`~/.mewcode/AGENTS.md` / `CLAUDE.md` / `.mewcoderc`
2. **读取文件时支持 include**。

核心接口保持兼容：

```python
class InstructionsLoader:
    def load_all(self) -> str | None: ...
    def current_text(self) -> str | None: ...
    def current_hash(self) -> str: ...
    def loaded_layers(self) -> list[LayerInfo]: ...
    def reload_and_check(self) -> tuple[bool, str | None]: ...
```

新增内部函数：

```python
_INCLUDE_RE = re.compile(r"^@include\s+(.+?)\s*$")
_MAX_INCLUDE_DEPTH = 3


def _read_text_with_limit(path: Path) -> tuple[str | None, int]:
    """读取单文件，UTF-8 解码，8KB 截断，错误 warning 后返回 None。"""


def _resolve_include_path(base_file: Path, raw: str, allowed_root: Path) -> Path | None:
    """相对当前文件解析 include，并确保 resolved path 在 allowed_root 内。"""


def _expand_includes(
    text: str,
    current_file: Path,
    allowed_root: Path,
    depth: int,
    visited: set[Path],
) -> str:
    """逐行展开 @include，深度限制 + visited 防环 + 越界拦截。"""
```

`LayerInfo` 可保持不变，`text` 存放 include 展开后的内容，`bytes_len` 仍记录主文件截断后大小；横幅不用统计 include 总大小，避免复杂化。

### 2.2 `mewcode/sessions/codec.py`

负责把现有 `Message` / `ContentBlock` 可逆转换为 JSONL 里的 dict。

```python
def block_to_dict(block: ContentBlock) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "text": block.text, "signature": block.signature}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": block.content, "is_error": block.is_error}


def block_from_dict(data: dict) -> ContentBlock:
    """非法类型/缺字段抛 ValueError，调用方按坏行处理。"""


def message_to_record(message: Message, *, ts: datetime) -> dict:
    return {"type": "message", "ts": ts.isoformat(), "role": message.role, "content": [...]}


def message_from_record(record: dict) -> tuple[Message, datetime]:
    """校验 type/role/content/ts，失败抛 ValueError。"""
```

设计要点：

- 不把 provider、system_prompt、usage 写进 JSONL。
- JSON 使用 `ensure_ascii=False`，便于用户直接查看中文。
- 反序列化只接受内部已知 block type。

### 2.3 `mewcode/sessions/archive.py`

负责会话 ID、JSONL 追加写、扫描、恢复、清理。

```python
@dataclass
class SessionSummary:
    session_id: str
    path: Path
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    bad_lines: int = 0


@dataclass
class RestoreResult:
    session_id: str
    path: Path
    messages: list[Message]
    summary: SessionSummary | None
    bad_lines: int = 0
    truncated: bool = False
    inserted_gap_reminder: bool = False
    restored: bool = False


class SessionArchive:
    def __init__(self, cwd: Path) -> None: ...
    def new_session_id(self) -> str: ...
    def session_path(self, session_id: str) -> Path: ...
    def append_message(self, session_id: str, message: Message) -> None: ...
    def scan_summaries(self) -> list[SessionSummary]: ...
    def cleanup_expired(self, days: int = 30) -> int: ...
    def restore_latest(self) -> RestoreResult: ...
    def restore(self, session_id: str) -> RestoreResult: ...
```

恢复流程：

```text
read lines
  ↓
逐行 json.loads + codec.message_from_record
  ├─ 成功 → messages.append
  └─ 失败 → bad_lines += 1
  ↓
_validate_tool_pairing(messages)
  └─ 必要时截断到上一条完整消息
  ↓
_maybe_insert_gap_reminder(messages, updated_at)
  └─ >24h 且尾部不是同类提醒 → append + 写 JSONL
```

工具配对校验：

```python
def _truncate_incomplete_tool_pairing(messages: list[Message]) -> tuple[list[Message], bool]:
    for i, msg in enumerate(messages):
        if assistant_has_tool_use(msg):
            if i + 1 >= len(messages) or not next_user_has_all_results(messages[i + 1], tool_ids):
                return messages[:i], True
        if user_has_tool_results(msg):
            if i == 0 or not prev_assistant_matches(messages[i - 1], result_ids):
                return messages[:i], True
    return messages, False
```

### 2.4 `Session` 持久化 hook

`Session` 目前的 append 方法是所有消息进入历史的集中入口。本阶段让它支持可选归档器：

```python
@dataclass
class Session:
    ...
    archive: object = None  # SessionArchive | None

    def _persist_last(self) -> None:
        if self.archive and self.session_id:
            self.archive.append_message(self.session_id, self.messages[-1])
```

改动：

- `append_user_text` append 后调用 `_persist_last()`。
- `append_assistant` append 后调用 `_persist_last()`。
- `append_tool_results` append 后调用 `_persist_last()`。
- `clear()` 清空内存后创建新 session_id，避免继续写旧会话；如果不想在 `Session` 内生成 ID，可由 `SessionArchive.rotate(session)` 实现。

为了保持模块边界，推荐：

```python
class SessionArchive:
    def attach(self, session: Session, restored: RestoreResult) -> None:
        session.session_id = restored.session_id
        session.messages = restored.messages
        session.archive = self

    def rotate(self, session: Session) -> None:
        session.session_id = self.new_session_id()
```

`Session.clear()` 内如果 `archive` 有 `rotate` 方法，则调用它；否则保留第八阶段行为。

### 2.5 `mewcode/memory/notes.py`

定义笔记数据结构与 frontmatter 读写。不引入 PyYAML 以外的新依赖，但项目已有 PyYAML；为保持简单也可手写 frontmatter。

```python
@dataclass
class MemoryNote:
    id: str
    scope: Literal["user", "project"]
    category: Literal["preference", "correction", "project_knowledge", "reference"]
    created_at: datetime
    updated_at: datetime
    source_session: str
    tags: list[str]
    body: str
    path: Path | None = None


def note_to_markdown(note: MemoryNote) -> str: ...
def note_from_markdown(path: Path) -> MemoryNote: ...
def write_note_atomic(note: MemoryNote, root: Path) -> Path: ...
def delete_note_safe(note_id: str, root: Path) -> bool: ...
def list_notes(root: Path) -> list[MemoryNote]: ...
```

路径规则：

- 文件名：`<note.id>.md`。
- 写入前校验目标路径 resolved 后位于 `root / "notes"` 内。
- 写入使用 `tmp` 文件 + `replace`。

### 2.6 `mewcode/memory/index.py`

负责把 notes 生成 `index.md`。

```python
CATEGORY_ORDER = ["correction", "preference", "project_knowledge", "reference"]
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25 * 1024


def build_index(notes: list[MemoryNote], scope: str) -> str:
    """按分类组织，超限时按优先级和 updated_at 裁剪。"""


def rebuild_index(root: Path, scope: str) -> str:
    notes = list_notes(root)
    text = build_index(notes, scope)
    atomic_write(root / "index.md", text)
    return text


def read_index(root: Path) -> str | None: ...
```

index 格式：

```markdown
# MewCode Project Memory Index

## 纠正反馈
- [mem_xxx] 用户纠正：...（updated: 2026-06-23）

## 用户偏好
- [mem_xxx] ...
```

限制策略：

1. 先构造候选条目。
2. 按 category 优先级、updated_at 倒序排序。
3. 逐条加入，直到即将超过 200 行或 25KB。

### 2.7 `mewcode/memory/updater.py`

负责 LLM 记忆更新。

```python
MEMORY_UPDATE_SYSTEM = """..."""

@dataclass
class MemoryOperation:
    op: Literal["create", "update", "delete", "noop"]
    scope: Literal["user", "project"] | None = None
    category: str | None = None
    id: str | None = None
    body: str | None = None
    tags: list[str] = field(default_factory=list)
    reason: str = ""


async def propose_memory_operations(
    provider: Provider,
    recent_messages: list[Message],
    user_index: str | None,
    project_index: str | None,
    session_id: str,
) -> list[MemoryOperation]:
    """调用 LLM，要求输出 JSON，解析失败返回空列表。"""
```

LLM 输出格式：

```json
{
  "operations": [
    {
      "op": "create",
      "scope": "project",
      "category": "project_knowledge",
      "body": "项目使用 pytest tests/ -q 做全量回归。",
      "tags": ["testing"]
    }
  ]
}
```

约束：

- 摘要请求不携带 tools，`tools_format=None`。
- system prompt 明确要求：只记录稳定事实，不记录临时计划；去重由模型判断。
- 输出解析失败只 warning。

### 2.8 `mewcode/memory/manager.py`

运行时总入口。

```python
@dataclass
class MemoryContext:
    text: str | None
    hash: str
    user_index: str | None
    project_index: str | None


class MemoryManager:
    def __init__(self, cwd: Path) -> None: ...
    def load_context(self) -> MemoryContext: ...
    def refresh_system_prompt_if_changed(self, session, rebuild_system_prompt) -> bool: ...
    def schedule_update(self, session, recent_messages: list[Message], renderer=None) -> None: ...
    async def update_once(self, session, recent_messages: list[Message]) -> None: ...
```

`load_context()` 拼接规则：

```markdown
以下是已记录的用户偏好和项目知识。项目级记忆优先于用户级记忆；如与当前用户明确指示冲突，以当前用户指示为准。

### 项目记忆
<project index>

### 用户记忆
<user index>
```

`refresh_system_prompt_if_changed()`：

- 重新读取 index。
- 与 `_last_hash` 比较。
- hash 不变 → 不重建 system_prompt。
- hash 变化 → 调 main 注入的 rebuild callable，传入当前 instructions + memory。

`update_once()`：

1. 读取当前 user/project index。
2. 调 `propose_memory_operations(...)`。
3. 对 operations 做安全校验和默认 scope 修正。
4. 写 notes / 删除 notes。
5. 对变更过的 scope 重建 index。

### 2.9 `chat.engine` 集成自然停止记忆更新

第九阶段给 `run_turn` 新增可选参数：

```python
async def run_turn(..., compactor=None, memory_manager=None) -> bool:
```

`_agent_loop` 新增 `memory_manager=None`。

在自然停止分支：

```python
if not tool_uses:
    _emit(renderer, Stopped("natural", iteration))
    _emit_usage(...)
    if memory_manager is not None:
        memory_manager.schedule_update(
            session,
            recent_messages=_recent_messages_for_memory(session.messages),
            renderer=renderer,
        )
    return True
```

非自然停止分支不调用。

`_recent_messages_for_memory` 推荐取最近 8 条消息，并确保包含最后一条真实 user 与最后 assistant 文本。

### 2.10 `main.py` / `repl/main_loop.py` 装配

main 装配新增：

```python
archive = SessionArchive(sandbox.cwd)
archive.cleanup_expired(days=30)
restore = archive.restore_latest()
archive.attach(session, restore)

memory_manager = MemoryManager(sandbox.cwd)
memory_context = memory_manager.load_context()

instructions_text = instructions_loader.load_all()
session.system_prompt = build_system_prompt(
    cwd=sandbox.cwd,
    tools=sorted(t.name for t in registry),
    custom_instructions=instructions_text,
    memory=memory_context.text,
)
```

重建 system prompt callable 需要同时捕获 instructions 与 memory：

```python
def _rebuild_system_prompt(new_instructions=None, new_memory=None):
    nonlocal instructions_text, memory_text
    if new_instructions is not _SENTINEL:
        instructions_text = new_instructions
    if new_memory is not _SENTINEL:
        memory_text = new_memory
    session.system_prompt = build_system_prompt(...)
```

为了兼容第七阶段 `/instructions reload`，也可保持一个单参数 callable，但内部从 loader/manager 读取当前值。

`run_repl` 新增透传：

- `archive`
- `memory_manager`

其中 `archive` 主要给命令或未来扩展使用，本阶段不必须新增命令。

### 2.11 恢复后首次超限压缩

实现方案：在 `Session` 增加轻量标记或由 `SessionArchive.attach` 设置：

```python
session.restored_needs_compaction_check = restore.restored
```

在 `chat.engine.run_turn` 的 compactor 调用前：

- 如果 session 是恢复而来，先运行一次 `compactor.before_request(session)`。
- 第八阶段 before_request 已经会按阈值判断是否压缩。
- 执行后清除标记，避免每轮重复用「恢复后」语义。

也可不加新字段，因为每轮 before_request 都会估算并自动压缩；但验收需要「恢复后先压一次」的可观测性，建议加字段用于测试。

## 3. 技术决策

### D1. 为什么会话用 JSONL 而不是 JSON 数组

JSONL 适合追加写：每条消息一行，崩溃最多破坏最后一行。JSON 数组需要回写尾部括号或整体重写，长会话风险更大。

### D2. 为什么不维护 meta 文件

meta 文件会引入同步问题：消息写成功但 meta 更新失败，或者反过来。扫描 JSONL 虽然慢一点，但逻辑单一、状态可信。默认项目会话数量有限，性能可接受。

### D3. 为什么项目级指令优先于用户级且排前面

模型对靠前内容更敏感。项目规范通常比个人偏好更具体，例如项目要求英文 commit、用户偏好中文回复，两者冲突时应先遵守项目规则。

### D4. 为什么 include 限制在允许根目录内

项目指令是会自动注入模型上下文的文本，如果允许 `@include ../../secret.txt`，就可能意外泄露项目外敏感文件。项目级只能读项目内，用户级只能读 `~/.mewcode` 内。

### D5. 为什么自动笔记在 natural stop 后触发

工具调用中间的信息往往不完整；用户取消或错误时也可能是半成品。natural stop 表示模型认为本轮任务完成，此时提取稳定偏好和知识更可靠。

### D6. 为什么记忆索引注入而不是检索

本阶段目标是简单可靠的长期记忆。把 index 控制到 2-3K tokens 后直接注入，比向量检索/RAG 更透明、更易测试，也不引入新依赖。

### D7. 为什么去重交给 LLM

「这条事实是否和旧笔记等价」是语义判断，规则很难写全。程序负责边界安全和格式校验，LLM 负责 create/update/noop 的语义选择。

### D8. 为什么用户级与项目级记忆分开

用户偏好应跨项目生效；项目知识不应污染其他项目。分开存储还能让用户直接删除某个项目的 `.mewcode/memory` 而不影响全局偏好。

## 4. 文件组织

```text
mewcode/
├── chat/
│   ├── engine.py              # run_turn 透传 memory_manager；natural stop 后调度更新
│   └── session.py             # archive hook + 恢复标记
├── instructions/
│   └── loader.py              # 优先级调整 + @include 展开
├── memory/
│   ├── __init__.py
│   ├── notes.py               # Note/frontmatter 读写
│   ├── index.py               # index.md 生成与限制
│   ├── updater.py             # LLM 更新记忆
│   └── manager.py             # 运行时注入与调度
├── sessions/
│   ├── __init__.py
│   ├── codec.py               # Message JSON 编解码
│   └── archive.py             # JSONL 追加/扫描/恢复/清理
├── main.py                    # 装配 archive/memory，恢复会话
└── repl/main_loop.py          # 透传 memory_manager/archive

tests/
├── test_instructions_include.py
├── test_sessions_codec.py
├── test_sessions_archive.py
├── test_memory_notes.py
├── test_memory_index.py
├── test_memory_manager.py
└── test_memory_agent_integration.py

scripts/
└── verify_memory.py
```

## 5. 与第八阶段兼容矩阵

| 第八阶段行为 | 第九阶段是否保留 | 说明 |
|---|---|---|
| `run_turn` 既有参数 | ✅ 兼容 | 新增可选 `memory_manager` |
| `Compactor` | ✅ 保留 | 恢复后仍复用第八阶段压缩 |
| `InstructionsLoader` | ✅ 兼容扩展 | 保持原公开方法 |
| `/instructions reload` | ✅ 保留 | 重建 system_prompt 时同时保留 memory |
| `/compact` | ✅ 保留 | 与恢复压缩互不冲突 |
| `Provider.stream_chat` | ✅ 不变 | 记忆更新用同一接口且不带 tools |
| 权限系统/MCP/tools | ✅ 不变 | 不改工具执行逻辑 |
| 无 AGENTS / sessions / memory | ✅ 接近第八阶段 | 只多一次空目录扫描 |
| prompt cache | ✅ 尽量保留 | instructions/memory hash 不变则不重建 system_prompt |

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| JSONL 坏行导致恢复失败 | 坏行跳过并计数 warning |
| 孤儿 tool_use 让 Provider 拒绝请求 | 恢复后统一截断到完整边界 |
| 自动笔记写入错误事实 | LLM 只在 natural stop 后提取；用户纠正会生成 correction/update |
| index 过大破坏上下文 | 200 行/25KB 双限制 |
| include 泄露项目外文件 | allowed_root + resolved path 校验 |
| 后台记忆更新异常污染主流程 | create_task 包裹 try/except，只 warning |
