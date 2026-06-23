"""自动笔记数据结构与读写（spec 第九阶段 F13 / F16 / F18）。

每条笔记一个 Markdown 文件，路径形如：
- 项目级：`<cwd>/.mewcode/memory/notes/<id>.md`
- 用户级：`~/.mewcode/memory/notes/<id>.md`

文件结构（YAML-like frontmatter + body）：

    ---
    id: mem_20260623_101530_a3f9
    scope: project
    category: project_knowledge
    created_at: 2026-06-23T10:15:30+08:00
    updated_at: 2026-06-23T10:15:30+08:00
    source_session: 20260623-101530-a3f9
    tags: [testing, mcp]
    ---

    项目的 MCP 验证脚本是 `python scripts/verify_mcp.py`...

为了避免依赖外部 YAML 包，frontmatter 用一组朴素 `key: value` 行手写
解析；tags 列表用紧凑形式 `[a, b, c]`。
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

# spec F12 四类自动笔记
Category = Literal["preference", "correction", "project_knowledge", "reference"]
Scope = Literal["user", "project"]

ALLOWED_CATEGORIES: tuple[Category, ...] = (
    "preference",
    "correction",
    "project_knowledge",
    "reference",
)

ALLOWED_SCOPES: tuple[Scope, ...] = ("user", "project")

# 默认 scope 由 category 决定（spec F18）
_DEFAULT_SCOPE_BY_CATEGORY: dict[str, Scope] = {
    "preference": "user",
    "correction": "user",
    "project_knowledge": "project",
    "reference": "project",
}

NOTES_DIRNAME = "notes"
INDEX_FILENAME = "index.md"

_NOTE_ID_RE = re.compile(r"^mem_[0-9A-Za-z_\-]+$")

# 单条笔记 body 上限（字节）：避免单条记忆吃光索引预算
NOTE_BODY_LIMIT = 4 * 1024


@dataclass
class MemoryNote:
    """单条自动笔记。"""

    id: str
    scope: Scope
    category: Category
    created_at: datetime
    updated_at: datetime
    source_session: str
    tags: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


def default_scope_for(category: str) -> Scope:
    """根据 category 推导默认 scope（spec F18）。"""
    return _DEFAULT_SCOPE_BY_CATEGORY.get(category, "project")


def new_note_id(now: datetime | None = None) -> str:
    """生成 `mem_YYYYMMDD_HHMMSS_xxxx` 格式的笔记 ID。"""
    now = now or datetime.now().astimezone()
    base = now.strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(2)
    return f"mem_{base}_{suffix}"


def scope_root(cwd: Path, scope: Scope) -> Path:
    """返回 scope 对应的 memory 根目录。

    - project: `<cwd>/.mewcode/memory`
    - user:    `~/.mewcode/memory`
    """
    if scope == "user":
        return Path.home() / ".mewcode" / "memory"
    return cwd / ".mewcode" / "memory"


def _format_tags(tags: list[str]) -> str:
    safe = [t.replace(",", " ").replace("[", "").replace("]", "").strip() for t in tags]
    safe = [t for t in safe if t]
    return "[" + ", ".join(safe) + "]"


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def _format_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


def _parse_iso(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError:
        return datetime.fromtimestamp(0).astimezone()


def note_to_markdown(note: MemoryNote) -> str:
    """把 MemoryNote 序列化为 Markdown 字符串。"""
    lines = [
        "---",
        f"id: {note.id}",
        f"scope: {note.scope}",
        f"category: {note.category}",
        f"created_at: {_format_iso(note.created_at)}",
        f"updated_at: {_format_iso(note.updated_at)}",
        f"source_session: {note.source_session}",
        f"tags: {_format_tags(note.tags)}",
        "---",
        "",
        note.body.rstrip() + "\n",
    ]
    return "\n".join(lines)


def note_from_markdown(path: Path) -> MemoryNote:
    """从 Markdown 文件解析 MemoryNote。

    解析失败抛 ValueError；调用方按"坏文件跳过"处理。
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("缺少 frontmatter")

    # 切分 frontmatter
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("frontmatter 格式不完整")
    fm_text = parts[1].strip()
    body = parts[2].lstrip("\n")

    fm: dict[str, str] = {}
    for raw_line in fm_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip()

    nid = fm.get("id", "").strip()
    scope = fm.get("scope", "").strip()
    category = fm.get("category", "").strip()

    if not nid or not _NOTE_ID_RE.match(nid):
        raise ValueError(f"非法 note id：{nid!r}")
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"非法 scope：{scope!r}")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError(f"非法 category：{category!r}")

    created_at = _parse_iso(fm.get("created_at", ""))
    updated_at = _parse_iso(fm.get("updated_at", ""))
    if updated_at.timestamp() == 0:
        updated_at = created_at

    source_session = fm.get("source_session", "").strip()
    tags = _parse_tags(fm.get("tags", "[]"))

    return MemoryNote(
        id=nid,
        scope=scope,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        created_at=created_at,
        updated_at=updated_at,
        source_session=source_session,
        tags=tags,
        body=body.rstrip() + "\n",
        path=path,
    )


def _ensure_within(target: Path, root: Path) -> Path:
    """确保 target resolved 后位于 root 内，越界抛 ValueError。"""
    try:
        resolved = target.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
    except (OSError, ValueError) as e:
        raise ValueError(f"路径解析失败：{e}") from e
    try:
        resolved.relative_to(root_resolved)
    except ValueError as e:
        raise ValueError(
            f"路径 {resolved} 越出允许目录 {root_resolved}"
        ) from e
    return resolved


def write_note_atomic(note: MemoryNote, root: Path) -> Path:
    """原子写入笔记到 `<root>/notes/<id>.md`。

    - 自动创建目录。
    - 路径越界检查（防 id 含 `../`）。
    - 写 tmp + os.replace 原子替换。
    - body 超过 NOTE_BODY_LIMIT 时截断 + 标注。
    """
    if not _NOTE_ID_RE.match(note.id):
        raise ValueError(f"非法 note id：{note.id!r}")
    notes_dir = root / NOTES_DIRNAME
    notes_dir.mkdir(parents=True, exist_ok=True)

    target = _ensure_within(notes_dir / f"{note.id}.md", notes_dir)

    body = note.body
    if len(body.encode("utf-8")) > NOTE_BODY_LIMIT:
        # 简单按字符截断；末尾加标注
        body = body[: NOTE_BODY_LIMIT // 2] + "\n\n[... 内容已截断 ...]\n"
        note = MemoryNote(
            id=note.id,
            scope=note.scope,
            category=note.category,
            created_at=note.created_at,
            updated_at=note.updated_at,
            source_session=note.source_session,
            tags=list(note.tags),
            body=body,
            path=note.path,
        )

    text = note_to_markdown(note)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=note.id, suffix=".tmp", dir=str(notes_dir)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
        shutil.move(str(tmp_path), str(target))
    except OSError:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    note.path = target
    return target


def delete_note_safe(note_id: str, root: Path) -> bool:
    """安全删除笔记，越界返回 False。"""
    if not _NOTE_ID_RE.match(note_id):
        return False
    notes_dir = root / NOTES_DIRNAME
    if not notes_dir.is_dir():
        return False
    try:
        target = _ensure_within(notes_dir / f"{note_id}.md", notes_dir)
    except ValueError:
        return False
    if not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def list_notes(root: Path) -> list[MemoryNote]:
    """列出 root 下所有笔记，坏文件 warning 后跳过。"""
    notes_dir = root / NOTES_DIRNAME
    if not notes_dir.is_dir():
        return []
    out: list[MemoryNote] = []
    for path in notes_dir.glob("*.md"):
        try:
            note = note_from_markdown(path)
        except (ValueError, OSError, UnicodeDecodeError) as e:
            print(f"⚠️ 跳过损坏的笔记 {path}：{e}")
            continue
        out.append(note)
    return out
