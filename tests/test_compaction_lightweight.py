"""第一层预防单测（spec AC3 / AC4 / AC5 / AC6 / AC7）。"""

from pathlib import Path

import pytest

from mewcode.compaction.lightweight import (
    SINGLE_MSG_LIMIT,
    SINGLE_TOOL_LIMIT,
    STASHED_MARKER,
    apply_lightweight,
)
from mewcode.providers import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


SESSION_ID = "20260101_120000"


def _make_tool_results_msg(blocks: list[ToolResultBlock]) -> Message:
    return Message(role="user", content=blocks)


def _make_user_msg(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _make_assistant_msg() -> Message:
    return Message(
        role="assistant",
        content=[ToolUseBlock(id="t1", name="read", input={})],
    )


# ---------- 单工具阈值 ----------


def test_单工具_5KB_不动(tmp_path: Path) -> None:
    """spec AC5：单工具 < 10KB → 不存盘。"""
    block = ToolResultBlock(
        tool_use_id="t1",
        content="x" * 5000,
        is_error=False,
    )
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg([block])]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    assert events == []
    assert msgs[2].content[0].content == "x" * 5000


def test_单工具_12KB_存盘(tmp_path: Path) -> None:
    """spec AC3：单工具 12KB → 写文件 + 替换预览。"""
    big = "line\n" * 3000  # ~15000 chars
    assert len(big.encode("utf-8")) > SINGLE_TOOL_LIMIT
    block = ToolResultBlock(
        tool_use_id="abc",
        content=big,
        is_error=False,
    )
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg([block])]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    assert len(events) == 1
    new_content = msgs[2].content[0].content
    assert new_content.startswith(STASHED_MARKER)

    # 文件已落盘
    target = (
        tmp_path / ".mewcode" / "transcripts" / SESSION_ID / "tool_2_abc.txt"
    )
    assert target.exists()
    assert target.read_text(encoding="utf-8") == big


# ---------- 单消息阈值 ----------


def test_单消息_排序存盘(tmp_path: Path) -> None:
    """spec AC4：3 个 7KB 工具，总 21KB < 25KB → 不动。"""
    blocks = [
        ToolResultBlock(tool_use_id=f"t{i}", content="x" * 7000, is_error=False)
        for i in range(3)
    ]
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg(blocks)]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    assert events == []  # 总 21KB < 25KB


def test_单消息_总和_排序存盘(tmp_path: Path) -> None:
    """spec AC4：单工具 ≤ 10KB 但和 > 25KB → 排序后存盘最大。

    注意：这里用多行内容，避免“≤25行不截取”规则让预览不降大小。
    """
    # 4 个约 8KB 工具，总 32KB > 25KB；每个都是多行，存盘后预览会缩小
    multi_line = "\n".join(f"line {j}" for j in range(900))
    assert len(multi_line.encode("utf-8")) < SINGLE_TOOL_LIMIT
    blocks = [
        ToolResultBlock(
            tool_use_id=f"t{i}", content=multi_line, is_error=False
        )
        for i in range(4)
    ]
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg(blocks)]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    # 至少存盘一个直到 ≤ 25KB
    assert len(events) >= 1
    # 检查总剩余 ≤ 25KB
    total = sum(
        len(b.content.encode("utf-8"))
        for b in msgs[2].content
        if isinstance(b, ToolResultBlock)
    )
    assert total <= SINGLE_MSG_LIMIT


# ---------- 预览格式 ----------


def test_预览_前20后5(tmp_path: Path) -> None:
    """spec AC6：30 行内容 → 含前 20 + 后 5 行截取标记。"""
    lines = [f"line {i}" for i in range(30)]
    big = "\n".join(lines) * 1000  # 放大确保超阈值
    block = ToolResultBlock(tool_use_id="t1", content=big, is_error=False)
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg([block])]
    apply_lightweight(msgs, tmp_path, SESSION_ID)
    new_content = msgs[2].content[0].content
    assert "—— 前 20 行 ——" in new_content
    assert "—— 后 5 行 ——" in new_content
    assert "完整内容请用 read 工具读取" in new_content


def test_预览_短内容不截(tmp_path: Path) -> None:
    """spec AC7：行数 ≤ 25 → 不截取，完整保留。"""
    # 字节超阈值但行数少：用一行很长的字符串
    content = "x" * 12000  # 1 行 12KB
    block = ToolResultBlock(tool_use_id="t1", content=content, is_error=False)
    msgs = [_make_user_msg("hi"), _make_assistant_msg(), _make_tool_results_msg([block])]
    apply_lightweight(msgs, tmp_path, SESSION_ID)
    new_content = msgs[2].content[0].content
    # 不含截取标记
    assert "—— 前 20 行 ——" not in new_content
    # 但仍含路径标记
    assert STASHED_MARKER in new_content


# ---------- 消息查找 ----------


def test_无tool_results消息_跳过(tmp_path: Path) -> None:
    """没有 tool_results 消息时 → 不报错，返回空 events。"""
    msgs = [_make_user_msg("hi")]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    assert events == []


def test_仅最后一条tool_results被处理(tmp_path: Path) -> None:
    """有多条 tool_results 消息时只处理最后一条（最近的）。"""
    # 早期 tool_results 消息（不应处理）
    early = _make_tool_results_msg(
        [ToolResultBlock(tool_use_id="early", content="x" * 12000, is_error=False)]
    )
    # 后续真实用户消息
    user = _make_user_msg("ok")
    # 最新 tool_results 消息（应处理）
    recent = _make_tool_results_msg(
        [ToolResultBlock(tool_use_id="recent", content="y" * 12000, is_error=False)]
    )
    msgs = [early, user, _make_assistant_msg(), recent]
    events = apply_lightweight(msgs, tmp_path, SESSION_ID)
    assert len(events) == 1
    assert events[0].tool_use_id == "recent"
    # 早期那条未变
    assert msgs[0].content[0].content == "x" * 12000
