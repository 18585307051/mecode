"""记忆索引重建（spec 第九阶段 F16）。

把 list_notes() 得到的 MemoryNote 列表组织成一份精简的 index.md，
供 system_prompt 注入使用。

限制：
- 行数 ≤ 200 行
- 字节数 ≤ 25KB

超限策略：
1. 按分类优先级排序：correction > preference > project_knowledge > reference
2. 同类按 updated_at 倒序
3. 逐条试加，加完后任一限制超出则不加，并在结尾标注「省略」

每条索引行格式：
- `[<note_id>] <body 第一行 / 摘要> (updated: YYYY-MM-DD, tags: a,b)`
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from mewcode.memory.notes import (
    INDEX_FILENAME,
    MemoryNote,
    Scope,
    list_notes,
)

MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25 * 1024

# 分类优先级：从高到低
_CATEGORY_ORDER: tuple[str, ...] = (
    "correction",
    "preference",
    "project_knowledge",
    "reference",
)

_CATEGORY_TITLE_ZH: dict[str, str] = {
    "correction": "纠正反馈",
    "preference": "用户偏好",
    "project_knowledge": "项目知识",
    "reference": "参考资料",
}

_SCOPE_TITLE: dict[str, str] = {
    "user": "用户全局记忆索引",
    "project": "项目记忆索引",
}


def _summarize_body(body: str, limit: int = 140) -> str:
    """从 body 提取一行摘要。"""
    body = body.strip()
    if not body:
        return "(空)"
    first_line = body.splitlines()[0].strip()
    if not first_line:
        first_line = body[:limit]
    if len(first_line) > limit:
        first_line = first_line[: limit - 1] + "…"
    # 行内禁止出现换行
    return first_line


def _note_to_index_line(note: MemoryNote) -> str:
    summary = _summarize_body(note.body)
    date = note.updated_at.strftime("%Y-%m-%d")
    tags = ",".join(note.tags) if note.tags else "-"
    return f"- [{note.id}] {summary}（updated: {date}, tags: {tags}）"


def _grouped(notes: list[MemoryNote]) -> dict[str, list[MemoryNote]]:
    out: dict[str, list[MemoryNote]] = {c: [] for c in _CATEGORY_ORDER}
    for note in notes:
        if note.category in out:
            out[note.category].append(note)
    for c in _CATEGORY_ORDER:
        out[c].sort(key=lambda n: n.updated_at, reverse=True)
    return out


def build_index(notes: list[MemoryNote], scope: Scope) -> str:
    """构造 index.md 文本，自动遵守 200 行 / 25KB 限制。"""
    title = _SCOPE_TITLE.get(scope, "记忆索引")

    if not notes:
        return f"# {title}\n\n（暂无）\n"

    groups = _grouped(notes)

    header_lines = [
        f"# {title}",
        "",
        "下面按分类列出已记录的长期记忆条目，括号内为 note id，可在后续",
        "提示中引用以更新或删除。条目越靠前优先级越高。",
        "",
    ]

    # 预留尾部 2 行用于"省略"提示，确保即便触发裁剪也不会越过 MAX_INDEX_LINES。
    _RESERVED_TAIL = 2
    line_limit = MAX_INDEX_LINES - _RESERVED_TAIL

    body_lines: list[str] = []
    truncated = False

    def _within_limits(extra_lines: list[str]) -> bool:
        candidate = header_lines + body_lines + extra_lines
        if len(candidate) > line_limit:
            return False
        candidate_text = "\n".join(candidate) + "\n"
        if len(candidate_text.encode("utf-8")) > MAX_INDEX_BYTES:
            return False
        return True

    for category in _CATEGORY_ORDER:
        items = groups.get(category, [])
        if not items:
            continue
        section_header = [
            f"## {_CATEGORY_TITLE_ZH[category]}",
            "",
        ]
        if not _within_limits(section_header):
            truncated = True
            break
        body_lines.extend(section_header)

        for note in items:
            line = _note_to_index_line(note)
            if not _within_limits([line]):
                truncated = True
                break
            body_lines.append(line)

        if truncated:
            break

        # 段落空行
        body_lines.append("")

    final_lines = header_lines + body_lines
    if truncated:
        final_lines.append("")
        final_lines.append("（部分条目因索引大小限制被省略）")

    text = "\n".join(line.rstrip() for line in final_lines).rstrip() + "\n"
    return text


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        shutil.move(tmp, str(path))
    except OSError:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def rebuild_index(root: Path, scope: Scope) -> str:
    """根据 root 下的 notes/ 重建 index.md，返回新文本。"""
    notes = list_notes(root)
    text = build_index(notes, scope)
    target = root / INDEX_FILENAME
    try:
        _atomic_write(target, text)
    except OSError as e:
        print(f"⚠️ 写入 {target} 失败：{e}")
    return text


def read_index(root: Path) -> str | None:
    """读取已存在的 index.md，找不到返回 None。"""
    path = root / INDEX_FILENAME
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"⚠️ 读取 {path} 失败（已忽略）：{e}")
        return None
    if len(text.encode("utf-8")) > MAX_INDEX_BYTES:
        print(f"⚠️ {path} 超过 25KB，将截断使用前 25KB")
        text = text.encode("utf-8")[:MAX_INDEX_BYTES].decode(
            "utf-8", errors="ignore"
        )
    return text
