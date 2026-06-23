"""长期记忆（spec 第九阶段 F12-F18）。

模块划分：
- notes   ：MemoryNote 数据结构、frontmatter 读写、原子写入。
- index   ：根据 notes 重建受限 index.md（≤200 行 / ≤25KB）。
- updater ：调 LLM 输出 create / update / delete / noop 操作。
- manager ：运行时入口，向 system_prompt 注入记忆 + 调度后台更新。
"""

from mewcode.memory.index import (
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    build_index,
    read_index,
    rebuild_index,
)
from mewcode.memory.manager import MemoryContext, MemoryManager
from mewcode.memory.notes import (
    MemoryNote,
    delete_note_safe,
    list_notes,
    new_note_id,
    note_from_markdown,
    note_to_markdown,
    scope_root,
    write_note_atomic,
)
from mewcode.memory.updater import (
    MemoryOperation,
    parse_operations,
    propose_memory_operations,
)

__all__ = [
    "MAX_INDEX_BYTES",
    "MAX_INDEX_LINES",
    "MemoryContext",
    "MemoryManager",
    "MemoryNote",
    "MemoryOperation",
    "build_index",
    "delete_note_safe",
    "list_notes",
    "new_note_id",
    "note_from_markdown",
    "note_to_markdown",
    "parse_operations",
    "propose_memory_operations",
    "read_index",
    "rebuild_index",
    "scope_root",
    "write_note_atomic",
]
