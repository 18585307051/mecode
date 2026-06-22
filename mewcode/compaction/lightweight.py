"""第一层：轻量预防（spec F3 / F4 / F5 / F6 / D3 / D4）。

每次请求前对最新一条 tool_results 消息处理：
- 单工具结果 > 10KB → 存盘 + 替换为预览
- 单消息总和 > 25KB → 排序后依次存盘直到剩余 ≤ 25KB

仅修改 ToolResultBlock.content；保留 message 结构。Message 是 frozen
dataclass，需要构造新对象替换。

存盘文件路径：
  <cwd>/.mewcode/transcripts/<session_id>/tool_<msg_idx>_<tool_use_id>.txt
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# 阈值（spec F3 / Q2）
SINGLE_TOOL_LIMIT = 10 * 1024   # 10KB
SINGLE_MSG_LIMIT = 25 * 1024    # 25KB

# 预览格式（spec F6 / Q4）
PREVIEW_HEAD_LINES = 20
PREVIEW_TAIL_LINES = 5

# 存盘标记前缀，用于识别已存盘的 ToolResultBlock 避免重复处理
STASHED_MARKER = "[工具结果已存盘到 "


@dataclass
class StashEvent:
    """一次存盘事件，供 UI 提示用。"""

    tool_use_id: str
    file_path: Path
    original_size: int


def _build_preview(content: str, display_path, size: int) -> str:
    """生成替换 ToolResultBlock.content 的预览文本（spec F6）。

    行数 ≤ 25 时不截取（spec AC7），避免反向放大。
    """
    size_kb = size / 1024
    lines = content.splitlines()

    if len(lines) <= PREVIEW_HEAD_LINES + PREVIEW_TAIL_LINES:
        return (
            f"{STASHED_MARKER}{display_path} ({size_kb:.1f}KB)]\n\n"
            f"{content}\n\n"
            f"完整内容请用 read 工具读取上述文件路径。"
        )

    head = "\n".join(lines[:PREVIEW_HEAD_LINES])
    tail = "\n".join(lines[-PREVIEW_TAIL_LINES:])
    return (
        f"{STASHED_MARKER}{display_path} ({size_kb:.1f}KB)]\n\n"
        f"—— 前 {PREVIEW_HEAD_LINES} 行 ——\n{head}\n\n"
        f"—— 后 {PREVIEW_TAIL_LINES} 行 ——\n{tail}\n\n"
        f"完整内容请用 read 工具读取上述文件路径。"
    )


def _stash_block(
    block,
    msg_idx: int,
    cwd: Path,
    session_id: str,
) -> tuple[str, StashEvent]:
    """把 block.content 写盘 + 返回 (预览, event)。"""
    target_dir = cwd / ".mewcode" / "transcripts" / session_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # tool_use_id 可能含特殊字符，保守处理：仅保留字母数字下划线连字符
    safe_id = "".join(
        ch if ch.isalnum() or ch in "_-" else "_"
        for ch in (block.tool_use_id or "noid")
    )
    file_path = target_dir / f"tool_{msg_idx}_{safe_id}.txt"

    content = block.content or ""
    file_path.write_text(content, encoding="utf-8")

    # 路径用相对 cwd 的形式（更友好显示）
    try:
        display_path = file_path.relative_to(cwd)
    except ValueError:
        display_path = file_path

    size = len(content.encode("utf-8"))
    preview = _build_preview(content, display_path, size)
    return preview, StashEvent(
        tool_use_id=block.tool_use_id,
        file_path=file_path,
        original_size=size,
    )


def _block_size(block) -> int:
    """ToolResultBlock 的字节大小。"""
    return len(block.content.encode("utf-8")) if block.content else 0


def _is_stashed(block) -> bool:
    """该 block 是否已经被存盘（避免重复处理）。"""
    return bool(block.content) and block.content.startswith(STASHED_MARKER)


def apply_lightweight(
    messages: list,
    cwd: Path,
    session_id: str,
) -> list[StashEvent]:
    """对最新一条 tool_results 消息应用第一层（spec F3 + F4）。

    Args:
        messages: session.messages（会被原地修改）
        cwd: 工作目录
        session_id: 会话 id（存盘目录名）

    Returns:
        本次发生的存盘事件列表
    """
    from mewcode.providers import Message, ToolResultBlock

    if not messages:
        return []

    # 找最后一条含 ToolResultBlock 的 user 消息（通常是末尾）
    target_msg_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and any(
            isinstance(b, ToolResultBlock) for b in m.content
        ):
            target_msg_idx = i
            break

    if target_msg_idx < 0:
        return []

    msg = messages[target_msg_idx]
    events: list[StashEvent] = []
    new_blocks = list(msg.content)
    changed = False

    # 阶段 1：单工具 > 10KB 直接存盘（spec F3）
    for i, block in enumerate(new_blocks):
        if not isinstance(block, ToolResultBlock):
            continue
        if _is_stashed(block):
            continue
        if _block_size(block) <= SINGLE_TOOL_LIMIT:
            continue
        preview, ev = _stash_block(block, target_msg_idx, cwd, session_id)
        new_blocks[i] = ToolResultBlock(
            tool_use_id=block.tool_use_id,
            content=preview,
            is_error=block.is_error,
        )
        events.append(ev)
        changed = True

    # 阶段 2：单消息总和 > 25KB 排序+依次存盘（spec F4）
    def total_size():
        return sum(
            _block_size(b)
            for b in new_blocks
            if isinstance(b, ToolResultBlock)
        )

    if total_size() > SINGLE_MSG_LIMIT:
        # 构造可存盘候选：未存盘的 ToolResultBlock
        candidates = [
            (i, b)
            for i, b in enumerate(new_blocks)
            if isinstance(b, ToolResultBlock) and not _is_stashed(b)
        ]
        # 按 size 从大到小
        candidates.sort(key=lambda pair: -_block_size(pair[1]))

        for i, block in candidates:
            if total_size() <= SINGLE_MSG_LIMIT:
                break
            preview, ev = _stash_block(block, target_msg_idx, cwd, session_id)
            new_blocks[i] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=preview,
                is_error=block.is_error,
            )
            events.append(ev)
            changed = True

    if changed:
        # Message 是 frozen，构造新对象替换
        messages[target_msg_idx] = Message(role=msg.role, content=new_blocks)

    return events
