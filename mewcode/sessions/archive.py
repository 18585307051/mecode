"""会话存档：JSONL 追加写、扫描、恢复、清理（spec 第九阶段 F3-F11）。

存储位置：`<cwd>/.mewcode/sessions/<session_id>.jsonl`
ID 格式：`YYYYMMDD-HHMMSS-xxxx`，xxxx 为 4 位十六进制随机串，防止
同秒撞车。

恢复流程（restore_latest / restore）：
1. 清理 30 天以上的旧 JSONL（cleanup_expired）。
2. 扫描所有 JSONL，逐行 json.loads + codec.message_from_record；
   坏行跳过并计数。
3. 检查工具调用配对，未配对部分截断到上一条完整边界。
4. 距离最后一条消息 > 24h 时，追加一条 system-reminder 并写回 JSONL。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from mewcode.providers import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.sessions.codec import (
    message_from_record,
    message_to_jsonl,
    message_to_record,
)

if TYPE_CHECKING:
    from mewcode.chat.session import Session

# 默认会话目录（相对 cwd）
SESSIONS_DIRNAME = ".mewcode/sessions"

# spec F11：30 天过期清理
DEFAULT_EXPIRE_DAYS = 30

# spec F10：长间隔提醒阈值
GAP_REMINDER_THRESHOLD = timedelta(hours=24)
GAP_REMINDER_TAG = "<system-reminder>"


@dataclass
class SessionSummary:
    """单个会话 JSONL 文件的扫描摘要（spec F5）。

    所有字段都是从 JSONL 现场计算出来，不依赖单独的 meta 文件。
    """

    session_id: str
    path: Path
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    bad_lines: int = 0


@dataclass
class RestoreResult:
    """会话恢复结果，main 装配阶段使用。"""

    session_id: str
    path: Path
    messages: list[Message] = field(default_factory=list)
    summary: SessionSummary | None = None
    bad_lines: int = 0
    truncated: bool = False
    inserted_gap_reminder: bool = False
    restored: bool = False


def _now() -> datetime:
    """带本地时区的当前时间。"""
    return datetime.now().astimezone()


def _format_session_id(now: datetime) -> str:
    """生成 `YYYYMMDD-HHMMSS-xxxx` 格式的 session_id。"""
    base = now.strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)  # 4 位十六进制
    return f"{base}-{suffix}"


def _is_text_message(message: Message) -> bool:
    """判断是否纯文本 user 消息（用于提取标题）。"""
    if message.role != "user":
        return False
    return all(isinstance(b, TextBlock) for b in message.content) and bool(
        message.content
    )


def _extract_title(messages: list[Message]) -> str:
    """从 messages 中取第一条用户文本作为标题（spec F5）。"""
    for msg in messages:
        if not _is_text_message(msg):
            continue
        text = "".join(
            b.text for b in msg.content if isinstance(b, TextBlock)
        ).strip()
        # 过滤掉系统级提醒（gap reminder 等）作为标题
        if text.startswith(GAP_REMINDER_TAG):
            continue
        if not text:
            continue
        # 取首行前 40 个字符
        first_line = text.splitlines()[0]
        return first_line[:40]
    return "未命名会话"


def _has_tool_use(message: Message) -> list[str]:
    """返回 assistant 消息中的 tool_use_id 列表（按出现顺序）。"""
    if message.role != "assistant":
        return []
    return [b.id for b in message.content if isinstance(b, ToolUseBlock)]


def _has_tool_results(message: Message) -> list[str]:
    """返回 user 消息中的 tool_result tool_use_id 列表（按出现顺序）。"""
    if message.role != "user":
        return []
    return [
        b.tool_use_id
        for b in message.content
        if isinstance(b, ToolResultBlock)
    ]


def _truncate_incomplete_tool_pairing(
    messages: list[Message],
) -> tuple[list[Message], bool]:
    """裁剪未配对的 tool_use / tool_result（spec F8 / AC8）。

    遍历消息：
    - assistant 含 tool_use → 下一条必须是 user，且其 tool_results 覆盖
      所有 tool_use_id；否则截断到该 assistant 之前。
    - user 含 tool_results → 上一条必须是 assistant，且其 tool_use 包含
      全部 tool_use_id；否则截断到该 user 之前。
    """
    out: list[Message] = []
    truncated = False
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        tool_uses = _has_tool_use(msg)
        tool_results_here = _has_tool_results(msg)

        if tool_uses:
            # 必须有匹配的下一条 user tool_results
            if i + 1 >= n:
                truncated = True
                break
            next_msg = messages[i + 1]
            next_results = _has_tool_results(next_msg)
            if not next_results or set(tool_uses) - set(next_results):
                truncated = True
                break
            out.append(msg)
            out.append(next_msg)
            i += 2
            continue

        if tool_results_here:
            # 必须有匹配的上一条 assistant tool_use
            if not out or out[-1].role != "assistant":
                truncated = True
                break
            prev_uses = _has_tool_use(out[-1])
            if set(tool_results_here) - set(prev_uses):
                truncated = True
                break
            # 已在前一分支配对处理过；走到这里说明 assistant 没有 tool_use
            # 但 user 有 tool_results → 截断
            truncated = True
            break

        out.append(msg)
        i += 1

    return out, truncated


def _format_gap(delta: timedelta) -> str:
    """把时间差格式化为'X 天 Y 小时'/'Y 小时 Z 分钟'。"""
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days} 天 {hours} 小时"
    if hours > 0:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


def _make_gap_reminder_text(delta: timedelta) -> str:
    return (
        f"{GAP_REMINDER_TAG}\n"
        f"距离上次会话已过去 {_format_gap(delta)}。"
        "请先根据已恢复的上下文继续，不确定的信息应重新读取文件确认。\n"
        "</system-reminder>"
    )


def _is_gap_reminder(message: Message) -> bool:
    if not _is_text_message(message):
        return False
    text = "".join(
        b.text for b in message.content if isinstance(b, TextBlock)
    )
    return text.startswith(GAP_REMINDER_TAG)


class SessionArchive:
    """会话存档器。

    用法：
        archive = SessionArchive(cwd)
        archive.cleanup_expired()
        result = archive.restore_latest()
        archive.attach(session, result)
        ...
        # session.append_xxx() 内部会自动调用 archive.append_message()
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._dir = cwd / SESSIONS_DIRNAME

    @property
    def directory(self) -> Path:
        return self._dir

    # ---- ID 与路径 ----

    def new_session_id(self) -> str:
        """生成新的 session_id。"""
        return _format_session_id(_now())

    def session_path(self, session_id: str) -> Path:
        """返回 session_id 对应的 JSONL 文件路径。"""
        return self._dir / f"{session_id}.jsonl"

    # ---- 写入 ----

    def append_message(self, session_id: str, message: Message) -> None:
        """把单条 message 追加到 JSONL 文件末尾。

        - 自动创建目录。
        - UTF-8 + ensure_ascii=False。
        - 写入失败 warning 但不抛出，避免影响主对话。
        """
        if not session_id:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            line = message_to_jsonl(message)
            path = self.session_path(session_id)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        except OSError as e:
            print(f"⚠️ 会话存档写入失败（已忽略）：{e}")

    # ---- 扫描 ----

    def _read_messages(
        self, path: Path
    ) -> tuple[list[Message], list[datetime], int]:
        """逐行解析 JSONL，返回 (messages, timestamps, bad_lines)。

        坏行只跳过并计数，不阻断扫描。
        """
        messages: list[Message] = []
        timestamps: list[datetime] = []
        bad = 0
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        msg, ts = message_from_record(data)
                    except (ValueError, json.JSONDecodeError):
                        bad += 1
                        continue
                    messages.append(msg)
                    timestamps.append(ts)
        except (OSError, UnicodeDecodeError) as e:
            print(f"⚠️ 会话文件 {path} 读不了（已跳过）：{e}")
        return messages, timestamps, bad

    def _summarize(self, path: Path) -> SessionSummary | None:
        """扫描单个 JSONL 文件计算摘要。"""
        if not path.is_file():
            return None
        messages, timestamps, bad = self._read_messages(path)
        if not messages:
            try:
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).astimezone()
            except OSError:
                mtime = _now()
            return SessionSummary(
                session_id=path.stem,
                path=path,
                title="未命名会话",
                message_count=0,
                created_at=mtime,
                updated_at=mtime,
                bad_lines=bad,
            )
        return SessionSummary(
            session_id=path.stem,
            path=path,
            title=_extract_title(messages),
            message_count=len(messages),
            created_at=timestamps[0] if timestamps else _now(),
            updated_at=timestamps[-1] if timestamps else _now(),
            bad_lines=bad,
        )

    def scan_summaries(self) -> list[SessionSummary]:
        """扫描所有 JSONL 文件，按 updated_at 倒序返回。"""
        if not self._dir.is_dir():
            return []
        summaries: list[SessionSummary] = []
        for path in self._dir.glob("*.jsonl"):
            summary = self._summarize(path)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    # ---- 清理 ----

    def cleanup_expired(self, days: int = DEFAULT_EXPIRE_DAYS) -> int:
        """删除 updated_at 超过 days 天的 JSONL 文件。

        Returns:
            实际删除的文件数。
        """
        if not self._dir.is_dir():
            return 0
        cutoff = _now() - timedelta(days=days)
        removed = 0
        for path in self._dir.glob("*.jsonl"):
            summary = self._summarize(path)
            if summary is None:
                continue
            if summary.updated_at < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError as e:
                    print(f"⚠️ 清理过期会话 {path} 失败（已忽略）：{e}")
        return removed

    # ---- 恢复 ----

    def restore_latest(self) -> RestoreResult:
        """恢复 updated_at 最新且仍存在的会话。

        没有可恢复会话时，返回 restored=False 的占位结果，
        session_id 为新生成的 ID。
        """
        summaries = self.scan_summaries()
        if not summaries:
            sid = self.new_session_id()
            return RestoreResult(
                session_id=sid,
                path=self.session_path(sid),
                restored=False,
            )
        latest = summaries[0]
        return self.restore(latest.session_id)

    def restore(self, session_id: str) -> RestoreResult:
        """恢复指定 session_id 的会话。

        负责坏行跳过、孤儿工具调用截断、>24h 间隔提醒插入。
        """
        path = self.session_path(session_id)
        if not path.is_file():
            return RestoreResult(
                session_id=session_id,
                path=path,
                restored=False,
            )

        messages, timestamps, bad = self._read_messages(path)
        if not messages:
            return RestoreResult(
                session_id=session_id,
                path=path,
                bad_lines=bad,
                restored=False,
            )

        # spec F8：截断未配对工具调用
        truncated_messages, truncated = _truncate_incomplete_tool_pairing(
            messages
        )

        # spec F10：>24h 间隔提醒
        inserted_gap = False
        last_ts = timestamps[-1] if timestamps else None
        if (
            truncated_messages
            and last_ts is not None
            and not _is_gap_reminder(truncated_messages[-1])
        ):
            delta = _now() - last_ts
            if delta >= GAP_REMINDER_THRESHOLD:
                reminder_text = _make_gap_reminder_text(delta)
                reminder_msg = Message.text("user", reminder_text)
                truncated_messages.append(reminder_msg)
                # 写回 JSONL，避免下次恢复重复插入
                self.append_message(session_id, reminder_msg)
                inserted_gap = True

        summary = self._summarize(path)
        # 如果发生了截断，summary 里 message_count 仍是文件原值；调用方
        # 主要使用 messages 长度，不依赖 summary 精确值。
        return RestoreResult(
            session_id=session_id,
            path=path,
            messages=truncated_messages,
            summary=summary,
            bad_lines=bad,
            truncated=truncated,
            inserted_gap_reminder=inserted_gap,
            restored=bool(truncated_messages),
        )

    # ---- 与 Session 协作 ----

    def attach(self, session: Session, result: RestoreResult) -> None:
        """把恢复结果绑到 Session 上。

        - 设置 session_id。
        - 替换 messages。
        - 安装 archive 引用，让 Session._persist_last 能写盘。
        - 记录 restored_needs_compaction_check，让恢复后第一次请求
          额外触发一次压缩检查（spec F9）。
        """
        session.session_id = result.session_id
        # 用 list 替换，保持 dataclass 字段一致
        session.messages = list(result.messages)
        session.archive = self
        session.restored_needs_compaction_check = result.restored

    def rotate(self, session: Session) -> str:
        """换发新的 session_id（供 Session.clear / switch_provider 使用）。"""
        sid = self.new_session_id()
        session.session_id = sid
        return sid

    # ---- 第十阶段：/session resume 显式入口 ----

    def load_by_id(self, session_id: str) -> RestoreResult:
        """按精确 session_id 加载会话。

        薄封装 `restore(session_id)`，语义对齐 /session resume 命令：
        命中 → 返回与启动恢复一致的 RestoreResult（含坏行计数 / 截断
        标志 / >24h 间隔提醒）。
        未命中 → restored=False 的占位结果。
        """
        return self.restore(session_id)

    def find_by_prefix(self, prefix: str) -> list[str]:
        """按 session_id 前缀模糊匹配，返回命中的完整 session_id 列表。

        prefix 为空时返回空列表；目录不存在时返回空列表。
        多匹配时由调用方提示用户给出更具体的 id。
        """
        if not prefix or not self._dir.is_dir():
            return []
        prefix_lc = prefix.lower()
        out: list[str] = []
        for path in self._dir.glob("*.jsonl"):
            sid = path.stem
            if sid.lower().startswith(prefix_lc):
                out.append(sid)
        out.sort()
        return out
