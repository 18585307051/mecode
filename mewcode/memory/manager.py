"""记忆运行时入口（spec 第九阶段 F17 / F18 / N4）。

职责：
- load_context()：读取 user/project index.md，拼接成可注入 system_prompt
  的 `## 长期记忆` 段落，并计算 hash 用于增量更新。
- refresh_system_prompt_if_changed(...)：如果 hash 变化，调用上层
  rebuild callable 让 system_prompt 同步刷新；hash 不变则不打扰。
- update_once(...) / schedule_update(...)：natural stop 后调度记忆
  更新；后台任务异常只 warning，不影响主对话。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from mewcode.memory.index import rebuild_index, read_index
from mewcode.memory.notes import (
    ALLOWED_CATEGORIES,
    ALLOWED_SCOPES,
    MemoryNote,
    Scope,
    default_scope_for,
    delete_note_safe,
    list_notes,
    new_note_id,
    scope_root,
    write_note_atomic,
)
from mewcode.memory.updater import (
    MemoryOperation,
    propose_memory_operations,
)


@dataclass
class MemoryContext:
    """注入 system_prompt 的长期记忆上下文。"""

    text: str | None
    hash: str
    user_index: str | None
    project_index: str | None


def _compose_memory_text(
    user_index: str | None, project_index: str | None
) -> str | None:
    """把两份 index 拼成可注入 system_prompt 的段落。"""
    if not user_index and not project_index:
        return None

    lines: list[str] = []
    lines.append(
        "以下是已记录的用户偏好和项目知识。"
        "项目级记忆优先于用户级记忆；如与当前用户明确指示冲突，"
        "以当前用户指示为准。"
    )
    lines.append("")

    if project_index:
        lines.append("### 项目记忆")
        lines.append(project_index.rstrip())
        lines.append("")
    if user_index:
        lines.append("### 用户记忆")
        lines.append(user_index.rstrip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _hash_text(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# rebuild_system_prompt callable 类型：
#   (custom_instructions=..., memory=...) -> None
RebuildSystemPrompt = Callable[..., None]


class MemoryManager:
    """记忆运行时管理。

    本类面向 main / chat 两个调用方：
    - main 在启动时构造，传给 build_system_prompt 拿到初始记忆段。
    - chat.engine 在 natural stop 后调用 schedule_update。
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._user_root = scope_root(cwd, "user")
        self._project_root = scope_root(cwd, "project")
        self._last_hash = ""

    # ---- 路径辅助 ----

    def root_for(self, scope: Scope) -> Path:
        return self._user_root if scope == "user" else self._project_root

    # ---- 读取 ----

    def load_context(self) -> MemoryContext:
        """读取 user/project index 并拼成注入文本。"""
        user_index = read_index(self._user_root)
        project_index = read_index(self._project_root)
        text = _compose_memory_text(user_index, project_index)
        h = _hash_text(text)
        self._last_hash = h
        return MemoryContext(
            text=text,
            hash=h,
            user_index=user_index,
            project_index=project_index,
        )

    # ---- 与 main 协作的 system_prompt 刷新 ----

    def refresh_system_prompt_if_changed(
        self,
        rebuild_system_prompt: RebuildSystemPrompt | None,
    ) -> bool:
        """检查 index 是否变化；变化则调用 rebuild。

        rebuild callable 必须接受 `memory=<text>` 关键字参数；不接受时
        本函数静默跳过。
        """
        # 直接读盘 + 拼接 + hash，避免 load_context 顺手刷新 _last_hash
        # 导致后续比较永远相等。
        user_index = read_index(self._user_root)
        project_index = read_index(self._project_root)
        text = _compose_memory_text(user_index, project_index)
        h = _hash_text(text)
        if h == self._last_hash:
            return False
        if rebuild_system_prompt is None:
            self._last_hash = h
            return False
        try:
            rebuild_system_prompt(memory=text)
        except TypeError:
            # callable 不支持 memory 关键字 → 视为不支持记忆刷新
            self._last_hash = h
            return False
        except Exception as e:
            print(f"⚠️ 重建 system_prompt 失败（已忽略）：{e}")
            return False
        self._last_hash = h
        return True

    # ---- operation 应用 ----

    def _normalize_op(self, op: MemoryOperation) -> MemoryOperation:
        """把 LLM 输出的 op 做一次安全规范化。"""
        category = op.category if op.category in ALLOWED_CATEGORIES else None
        scope = op.scope if op.scope in ALLOWED_SCOPES else None
        if category and scope is None:
            scope = default_scope_for(category)
        # category/scope 冲突时按默认 scope 修正（spec F18）
        if category and scope is not None:
            forced = default_scope_for(category)
            # 仅当 scope 与 category 默认值显式冲突且没人坚持要保留时才纠正：
            # 这里采用强一致策略——直接以 category 默认 scope 为准。
            scope = forced
        return MemoryOperation(
            op=op.op,
            scope=scope,
            category=category,
            id=op.id,
            body=op.body,
            tags=list(op.tags),
            reason=op.reason,
        )

    def _apply_operation(
        self, op: MemoryOperation, session_id: str
    ) -> tuple[set[Scope], bool]:
        """应用单条 operation。

        Returns:
            (changed_scopes, ok) —— changed_scopes 用于决定哪些 index 需要重建。
        """
        op = self._normalize_op(op)
        changed: set[Scope] = set()

        if op.op == "noop":
            return changed, True

        if op.op == "create":
            if not op.body or not op.category:
                return changed, False
            scope = op.scope or default_scope_for(op.category)
            now = datetime.now().astimezone()
            note = MemoryNote(
                id=new_note_id(now),
                scope=scope,  # type: ignore[arg-type]
                category=op.category,  # type: ignore[arg-type]
                created_at=now,
                updated_at=now,
                source_session=session_id,
                tags=list(op.tags),
                body=op.body.strip() + "\n",
            )
            try:
                write_note_atomic(note, self.root_for(scope))
                changed.add(scope)
                return changed, True
            except (OSError, ValueError) as e:
                print(f"⚠️ 写入笔记失败（已忽略）：{e}")
                return changed, False

        if op.op == "update":
            if not op.id:
                return changed, False
            existing = self._find_note(op.id)
            if existing is None:
                # 找不到原 note 时，退化为 create（如果给了 body+category）
                if op.body and op.category:
                    return self._apply_operation(
                        MemoryOperation(
                            op="create",
                            scope=op.scope,
                            category=op.category,
                            body=op.body,
                            tags=op.tags,
                        ),
                        session_id,
                    )
                return changed, False

            new_body = op.body.strip() + "\n" if op.body else existing.body
            new_category = op.category or existing.category
            new_scope = op.scope or existing.scope
            new_scope = default_scope_for(new_category)  # 强制按 category 修正
            new_tags = list(op.tags) if op.tags else list(existing.tags)
            updated_note = MemoryNote(
                id=existing.id,
                scope=new_scope,  # type: ignore[arg-type]
                category=new_category,  # type: ignore[arg-type]
                created_at=existing.created_at,
                updated_at=datetime.now().astimezone(),
                source_session=session_id or existing.source_session,
                tags=new_tags,
                body=new_body,
            )
            try:
                # scope 切换时先删旧再写新
                if new_scope != existing.scope:
                    delete_note_safe(existing.id, self.root_for(existing.scope))
                    changed.add(existing.scope)
                write_note_atomic(updated_note, self.root_for(new_scope))
                changed.add(new_scope)  # type: ignore[arg-type]
                return changed, True
            except (OSError, ValueError) as e:
                print(f"⚠️ 更新笔记失败（已忽略）：{e}")
                return changed, False

        if op.op == "delete":
            if not op.id:
                return changed, False
            existing = self._find_note(op.id)
            if existing is None:
                return changed, False
            ok = delete_note_safe(existing.id, self.root_for(existing.scope))
            if ok:
                changed.add(existing.scope)
            return changed, ok

        return changed, False

    def _find_note(self, note_id: str) -> MemoryNote | None:
        for scope in ("user", "project"):
            for note in list_notes(self.root_for(scope)):  # type: ignore[arg-type]
                if note.id == note_id:
                    return note
        return None

    # ---- 后台更新 ----

    async def update_once(
        self,
        session,
        recent_messages: list,
    ) -> int:
        """执行一次完整的记忆更新（同步等待）。

        返回应用成功的 operation 数。
        """
        ctx = self.load_context()
        try:
            ops = await propose_memory_operations(
                provider=session.provider,
                recent_messages=recent_messages,
                user_index=ctx.user_index,
                project_index=ctx.project_index,
                session_id=session.session_id,
            )
        except Exception as e:
            print(f"⚠️ 记忆更新失败（已忽略）：{e}")
            return 0

        if not ops:
            return 0

        changed_scopes: set[Scope] = set()
        applied = 0
        for op in ops:
            changes, ok = self._apply_operation(op, session.session_id)
            if ok and op.op != "noop":
                applied += 1
            changed_scopes |= changes

        for scope in changed_scopes:
            try:
                rebuild_index(self.root_for(scope), scope)
            except Exception as e:
                print(f"⚠️ 重建 {scope} index 失败（已忽略）：{e}")

        return applied

    def schedule_update(
        self,
        session,
        recent_messages: list,
        renderer=None,
        rebuild_system_prompt: RebuildSystemPrompt | None = None,
    ) -> asyncio.Task | None:
        """natural stop 后调度后台记忆更新（spec F14）。

        异常只 warning，不向上传播。
        """

        async def _run() -> None:
            try:
                applied = await self.update_once(session, recent_messages)
            except Exception as e:
                print(f"⚠️ 记忆更新任务异常（已忽略）：{e}")
                return
            if applied > 0 and renderer is not None:
                try:
                    renderer.print_info(f"🧠 记忆已更新（{applied} 条）")
                except Exception:
                    pass
                # 触发 system_prompt 刷新（hash 不变则跳过）
                self.refresh_system_prompt_if_changed(rebuild_system_prompt)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return loop.create_task(_run())
