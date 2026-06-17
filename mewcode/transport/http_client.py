"""httpx 流式 POST 客户端。

封装 httpx 的异步流式 API，把网络层异常和 HTTP 状态码错误统一映射为
mewcode.providers.errors 中的相应错误类型，便于上层用单一 except
分支处理。

约定：
- 2xx 响应：以 chunk 字节为单位异步产出。
- 4xx/5xx：抛 HTTPStatusError（401/403 抛 AuthError，作为更细分类别）。
- 连接/超时/DNS 类问题：抛 NetworkError。
- 错误信息中包含响应体片段，但敏感字段（如 Authorization 值）会被
  脱敏（spec N9）。
"""

import re
from collections.abc import AsyncIterator

import httpx

from mewcode.providers.errors import (
    AuthError,
    HTTPStatusError,
    NetworkError,
)

# 响应体片段最大字节数。流式响应可能很大，截断后展示足以定位问题。
_BODY_SNIPPET_LIMIT = 200

# 用于把 api_key 类敏感字段从错误片段中遮蔽。
_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{4,})", re.IGNORECASE),
    re.compile(r'("api_key"\s*:\s*")[^"]+(")'),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
]


async def stream_post(
    url: str,
    headers: dict[str, str],
    json_body: dict,
    timeout: float = 60.0,
) -> AsyncIterator[bytes]:
    """对给定 URL 发起流式 POST 请求，按 chunk 异步产出字节。

    Args:
        url:       完整请求 URL。
        headers:   HTTP 请求头。
        json_body: 请求体，将以 JSON 编码发送。
        timeout:   单次"无字节到达"的最大等待秒数。

    Yields:
        响应体的字节 chunk。

    Raises:
        AuthError:        HTTP 401 / 403。
        HTTPStatusError:  其他 4xx / 5xx 状态码。
        NetworkError:     连接、超时、DNS 等网络层异常。
    """
    # httpx 的 Timeout 同时控制 connect / read / write / pool；这里把
    # connect 留默认，把 read 设为传入值（流式场景下"无字节到达"的最长等待）。
    timeout_cfg = httpx.Timeout(timeout, read=timeout)

    try:
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            async with client.stream(
                "POST", url, headers=headers, json=json_body
            ) as resp:
                # 状态码检查（在读取流体之前）
                if resp.status_code in (401, 403):
                    snippet = await _read_body_snippet(resp)
                    raise AuthError(resp.status_code, snippet)
                if resp.status_code >= 400:
                    snippet = await _read_body_snippet(resp)
                    raise HTTPStatusError(resp.status_code, snippet)

                async for chunk in resp.aiter_bytes():
                    yield chunk
    except httpx.RequestError as e:
        # 含 ConnectError / TimeoutException / ReadError 等
        raise NetworkError(str(e)) from e


async def _read_body_snippet(
    resp: httpx.Response, limit: int = _BODY_SNIPPET_LIMIT
) -> str:
    """读取响应体的前若干字节作为错误片段，脱敏后返回字符串。"""
    raw = b""
    try:
        async for chunk in resp.aiter_bytes():
            raw += chunk
            if len(raw) >= limit:
                break
    except httpx.RequestError:
        # 读取失败时也容忍，至少返回已收到的部分
        pass

    text = raw[:limit].decode("utf-8", errors="replace")
    return _redact_secrets(text)


def _redact_secrets(text: str) -> str:
    """把已知的敏感模式遮蔽为 ***。"""
    redacted = text
    for pat in _SECRET_PATTERNS:
        if pat.groups == 2:
            redacted = pat.sub(r"\1***\2", redacted)
        else:
            redacted = pat.sub(r"\1***", redacted)
    return redacted
