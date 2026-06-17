"""SSE（Server-Sent Events）帧解析。

把字节流切分为一系列 SSEFrame 对象，供 Provider 层进一步把 JSON
data 解析成 StreamEvent。

简化规则（足够覆盖 Anthropic 与 OpenAI 协议）：
- 帧之间以空行（`\\n\\n` 或 `\\r\\n\\r\\n`）分隔。
- 行 `event: <name>` 填入 SSEFrame.event。
- 行 `data: <text>` 累加到 SSEFrame.data，多行 data 用 `\\n` 连接。
- 以 `:` 开头的行是注释，忽略。
- 空 frame（仅有空行）跳过，不产出。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class SSEFrame:
    """一个完整的 SSE 帧。

    Attributes:
        event: `event:` 字段值；当帧只含 data 时为 None。
        data:  `data:` 字段值（多行 data 已用 `\\n` 连接）。
    """

    event: str | None
    data: str


async def iter_sse_frames(
    byte_stream: AsyncIterator[bytes],
) -> AsyncIterator[SSEFrame]:
    """把字节流切分为 SSE 帧。

    Args:
        byte_stream: 异步字节迭代器（一般来自 httpx 的 aiter_bytes）。

    Yields:
        每个完整 SSE 帧对应一个 SSEFrame。
    """
    buf = b""

    async for chunk in byte_stream:
        buf += chunk

        # 按空行（兼容 \n\n 和 \r\n\r\n）切分帧块
        while True:
            sep_idx = _find_separator(buf)
            if sep_idx is None:
                break
            block, buf = buf[: sep_idx[0]], buf[sep_idx[1] :]
            frame = _parse_block(block)
            if frame is not None:
                yield frame

    # 流结束时缓冲区可能还有最后一帧（没有以空行结尾的情况）
    if buf.strip():
        frame = _parse_block(buf)
        if frame is not None:
            yield frame


def _find_separator(buf: bytes) -> tuple[int, int] | None:
    """在 buf 中查找帧分隔符（空行）。

    Returns:
        (start, end) ：分隔符在 buf 中的起止索引；找不到时返回 None。
        切分时 `buf[:start]` 是当前帧块，`buf[end:]` 是剩余字节。
    """
    # 优先匹配较长的 \r\n\r\n
    idx = buf.find(b"\r\n\r\n")
    if idx != -1:
        return idx, idx + 4
    idx = buf.find(b"\n\n")
    if idx != -1:
        return idx, idx + 2
    return None


def _parse_block(block: bytes) -> SSEFrame | None:
    """解析一个帧块（不含尾部空行）为 SSEFrame。

    返回 None 表示该帧块无有效内容（空块或全是注释），调用方应跳过。
    """
    text = block.decode("utf-8")
    event: str | None = None
    data_lines: list[str] = []

    for raw_line in text.split("\n"):
        # 去掉行末可能的 \r（处理 \r\n 行尾）
        line = raw_line.rstrip("\r")

        if not line:
            continue
        if line.startswith(":"):
            # SSE 注释行
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].lstrip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
        # 其他字段（id、retry 等）当前阶段不需要，忽略

    if event is None and not data_lines:
        return None

    return SSEFrame(event=event, data="\n".join(data_lines))
