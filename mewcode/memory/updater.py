"""自动笔记更新（spec 第九阶段 F14 / F15）。

每轮 Agent Loop 自然停下后异步调用 LLM，要求其输出对长期记忆的
create / update / delete / noop 操作；本模块负责构造 prompt、调
provider.stream_chat、解析输出。

- thinking=False（用更便宜的回答路径）
- tools_format=None（记忆更新不带工具）
- 解析失败 / Provider 异常 → 返回空列表，不影响主对话
- 去重逻辑全部交给 LLM 判断（spec F15）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mewcode.providers import (
    Message,
    Provider,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.providers.events import TextDelta

MEMORY_UPDATE_SYSTEM = """你是 MewCode 的"记忆整理助手"。基于最近一轮对话与现有的记忆索引，
判断是否需要更新长期记忆。仅记录稳定、可复用的事实，不记录临时计划、本轮的具体执行步骤。

可选操作类型：
- create：新增一条笔记
- update：更新已有笔记（必须给出 id）
- delete：删除已有笔记（必须给出 id），仅当用户明确否定旧记忆时使用
- noop：本轮没有值得记录或更新的事实

四类记忆 category 与默认 scope：
- preference（用户偏好）→ user
- correction（纠正反馈）→ user
- project_knowledge（项目知识）→ project
- reference（参考资料）→ project

去重原则：如果新事实与已有笔记等价，应当 update 或 noop，而不是再 create。

只输出一段 JSON，不要任何额外解释。格式：

{
  "operations": [
    {
      "op": "create" | "update" | "delete" | "noop",
      "scope": "user" | "project",
      "category": "preference" | "correction" | "project_knowledge" | "reference",
      "id": "mem_xxx（update / delete 必填）",
      "body": "笔记正文（create / update 必填）",
      "tags": ["可选标签"],
      "reason": "可选，简短说明"
    }
  ]
}

如果不需要任何更新，返回 {"operations": [{"op": "noop"}]}。
"""


@dataclass
class MemoryOperation:
    """一次记忆操作建议（来自 LLM 输出）。"""

    op: str  # create / update / delete / noop
    scope: str | None = None
    category: str | None = None
    id: str | None = None
    body: str | None = None
    tags: list[str] = field(default_factory=list)
    reason: str = ""


def _block_to_brief(block) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ThinkingBlock):
        return ""  # thinking 不进记忆 prompt
    if isinstance(block, ToolUseBlock):
        return f"[调用工具 {block.name}({json.dumps(block.input, ensure_ascii=False)[:200]})]"
    if isinstance(block, ToolResultBlock):
        return (
            f"[工具结果 {'失败' if block.is_error else '成功'}]"
            f" {block.content[:300]}"
        )
    return ""


def recent_messages_to_text(messages: list[Message], limit: int = 8) -> str:
    """把最近若干条消息打包成一段简明上下文文本。"""
    tail = messages[-limit:]
    lines: list[str] = []
    for msg in tail:
        role = msg.role
        chunks = [_block_to_brief(b) for b in msg.content]
        chunks = [c for c in chunks if c]
        if not chunks:
            continue
        joined = "\n".join(chunks)
        if len(joined) > 1500:
            joined = joined[:1500] + "..."
        lines.append(f"### {role}\n{joined}")
    return "\n\n".join(lines)


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_operations(text: str) -> list[MemoryOperation]:
    """从 LLM 输出文本解析 operations 列表，失败返回空列表。"""
    if not text or not text.strip():
        return []

    candidate = text.strip()
    # 容忍 ```json ... ``` 包裹
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        # 去掉可能的 json 前缀
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]

    # 尝试整体 parse；失败时正则取最后一个 {...}
    data: Any
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(candidate)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

    if not isinstance(data, dict):
        return []
    raw_ops = data.get("operations")
    if not isinstance(raw_ops, list):
        return []

    out: list[MemoryOperation] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op", "")).strip().lower()
        if op not in ("create", "update", "delete", "noop"):
            continue
        scope = raw.get("scope")
        category = raw.get("category")
        nid = raw.get("id")
        body = raw.get("body")
        tags_raw = raw.get("tags", [])
        tags: list[str] = []
        if isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        out.append(
            MemoryOperation(
                op=op,
                scope=str(scope).strip() if isinstance(scope, str) else None,
                category=str(category).strip()
                if isinstance(category, str)
                else None,
                id=str(nid).strip() if isinstance(nid, str) else None,
                body=str(body) if isinstance(body, str) else None,
                tags=tags,
                reason=str(raw.get("reason", "")) if raw.get("reason") else "",
            )
        )
    return out


def _build_user_prompt(
    recent_text: str,
    user_index: str | None,
    project_index: str | None,
    session_id: str,
) -> str:
    parts: list[str] = []
    parts.append(f"会话 ID: {session_id}")
    parts.append("")
    parts.append("## 当前用户记忆索引（user）")
    parts.append(user_index or "（暂无）")
    parts.append("")
    parts.append("## 当前项目记忆索引（project）")
    parts.append(project_index or "（暂无）")
    parts.append("")
    parts.append("## 最近一轮对话")
    parts.append(recent_text or "（无可分析内容）")
    parts.append("")
    parts.append("请基于以上内容，输出 operations JSON。")
    return "\n".join(parts)


async def propose_memory_operations(
    provider: Provider,
    recent_messages: list[Message],
    user_index: str | None,
    project_index: str | None,
    session_id: str,
) -> list[MemoryOperation]:
    """调 LLM 输出记忆更新建议；解析失败 / Provider 异常 → 返回空列表。"""
    recent_text = recent_messages_to_text(recent_messages)
    user_text = _build_user_prompt(
        recent_text, user_index, project_index, session_id
    )

    request = [Message.text("user", user_text)]
    accumulated = ""
    try:
        stream = provider.stream_chat(
            request,
            thinking=False,
            tools_format=None,
            system=MEMORY_UPDATE_SYSTEM,
        )
        async for event in stream:
            if isinstance(event, TextDelta):
                accumulated += event.text
        try:
            await stream.aclose()
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ 记忆更新调用 Provider 失败（已忽略）：{e}")
        return []

    return parse_operations(accumulated)
