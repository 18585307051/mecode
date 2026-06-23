"""Session 存档与恢复（spec 第九阶段 F3-F11）。

sessions 子包负责把 chat.Session 的消息历史以 JSONL 形式落盘到
`<cwd>/.mewcode/sessions/<session_id>.jsonl`，并在新启动时自动恢复
最近一个未过期会话。

模块划分：
- codec   ：Message / ContentBlock 与 JSON dict 的可逆转换。
- archive ：SessionArchive，负责追加写、扫描、恢复、清理。
"""

from mewcode.sessions.archive import (
    RestoreResult,
    SessionArchive,
    SessionSummary,
)
from mewcode.sessions.codec import (
    block_from_dict,
    block_to_dict,
    message_from_jsonl,
    message_from_record,
    message_to_jsonl,
    message_to_record,
)

__all__ = [
    "RestoreResult",
    "SessionArchive",
    "SessionSummary",
    "block_from_dict",
    "block_to_dict",
    "message_from_jsonl",
    "message_from_record",
    "message_to_jsonl",
    "message_to_record",
]
