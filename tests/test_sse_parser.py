"""mewcode.transport.sse.iter_sse_frames 的单元测试。

通过手写一个简单的字节流 helper 模拟 httpx 的 aiter_bytes，
覆盖 task.md T5 列出的 6 个验证场景。
"""

from collections.abc import AsyncIterator

import pytest

from mewcode.transport import SSEFrame, iter_sse_frames

# ---------- 辅助：把固定字节列表包装成异步迭代器 ----------


async def _abytes(*chunks: bytes) -> AsyncIterator[bytes]:
    """把若干 bytes chunk 包装成异步迭代器，模拟 httpx 流式响应。"""
    for c in chunks:
        yield c


async def _collect(stream: AsyncIterator[bytes]) -> list[SSEFrame]:
    """跑完 iter_sse_frames，收集所有产出的 SSEFrame。"""
    return [frame async for frame in iter_sse_frames(stream)]


# ---------- 测试用例 ----------


@pytest.mark.asyncio
async def test_单帧基础() -> None:
    """单个完整帧应解析出 event 和 data。"""
    frames = await _collect(_abytes(b"event: foo\ndata: hello\n\n"))
    assert len(frames) == 1
    assert frames[0].event == "foo"
    assert frames[0].data == "hello"


@pytest.mark.asyncio
async def test_无event字段() -> None:
    """没有 event 行时 frame.event 应为 None。"""
    frames = await _collect(_abytes(b"data: hello\n\n"))
    assert len(frames) == 1
    assert frames[0].event is None
    assert frames[0].data == "hello"


@pytest.mark.asyncio
async def test_多行data() -> None:
    """多行 data 应用换行符连接。"""
    frames = await _collect(_abytes(b"data: line1\ndata: line2\n\n"))
    assert len(frames) == 1
    assert frames[0].data == "line1\nline2"


@pytest.mark.asyncio
async def test_注释行被忽略() -> None:
    """以 ':' 开头的注释行应被忽略。"""
    frames = await _collect(_abytes(b": this is a comment\ndata: ok\n\n"))
    assert len(frames) == 1
    assert frames[0].data == "ok"


@pytest.mark.asyncio
async def test_chunk边界跨帧() -> None:
    """两个帧被任意拆成 3 个 chunk 喂入，仍应产出 2 个完整 frame。"""
    raw = b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"
    # 拆点选在帧中间和帧边界
    chunks = (raw[:6], raw[6:18], raw[18:])
    frames = await _collect(_abytes(*chunks))
    assert len(frames) == 2
    assert (frames[0].event, frames[0].data) == ("a", "1")
    assert (frames[1].event, frames[1].data) == ("b", "2")


@pytest.mark.asyncio
async def test_data为DONE() -> None:
    """OpenAI 协议结束哨兵 [DONE] 应原样保留在 data 字段。"""
    frames = await _collect(_abytes(b"data: [DONE]\n\n"))
    assert len(frames) == 1
    assert frames[0].data == "[DONE]"


@pytest.mark.asyncio
async def test_crlf行尾() -> None:
    """\\r\\n 行尾的 SSE 流也应正确解析。"""
    frames = await _collect(_abytes(b"event: foo\r\ndata: hello\r\n\r\n"))
    assert len(frames) == 1
    assert frames[0].event == "foo"
    assert frames[0].data == "hello"
