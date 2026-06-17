"""Anthropic 协议 cache_control 与 cache 字段解析的单元测试。

覆盖 spec AC4 / AC10 / AC11。
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.events import (
    Done,
    StreamEvent,
    Usage,
)
from mewcode.tools import ToolRegistry, register_builtins


# ---------- ToolRegistry.to_anthropic_format_with_cache ----------


def test_to_anthropic_format_with_cache_最后一项含cache_control() -> None:
    """spec AC11：with_cache 版本最后一项含 cache_control。"""
    r = ToolRegistry()
    register_builtins(r)
    items = r.to_anthropic_format_with_cache()
    assert "cache_control" in items[-1]
    assert items[-1]["cache_control"] == {"type": "ephemeral"}


def test_to_anthropic_format_with_cache_其他项不含() -> None:
    """spec AC11：仅最后一项是 cache breakpoint。"""
    r = ToolRegistry()
    register_builtins(r)
    items = r.to_anthropic_format_with_cache()
    for item in items[:-1]:
        assert "cache_control" not in item


def test_to_anthropic_format_不变() -> None:
    """旧方法行为不变：每项都不含 cache_control。"""
    r = ToolRegistry()
    register_builtins(r)
    items = r.to_anthropic_format()
    for item in items:
        assert "cache_control" not in item


def test_to_anthropic_format_with_cache_空列表() -> None:
    """空 registry 返回空列表，不报错。"""
    r = ToolRegistry()
    items = r.to_anthropic_format_with_cache()
    assert items == []


# ---------- AnthropicProvider 请求体格式 ----------


def _make_anthropic_provider() -> AnthropicProvider:
    cfg = ProviderConfig(
        name="t",
        protocol="anthropic",  # type: ignore[arg-type]
        model="m",
        base_url="https://example.com",
        api_key="sk-test",
    )
    return AnthropicProvider(cfg)


@pytest.mark.asyncio
async def test_AnthropicProvider_system列表形式_含cache_control() -> None:
    """spec AC4：system 非空时，请求体的 system 字段是列表形式且含 cache_control。"""
    captured: dict[str, Any] = {}

    async def _stub_stream_post(url, headers, body):
        captured["body"] = body
        # 立即结束流（无 SSE 数据）
        if False:
            yield b""
        return

    with patch("mewcode.providers.anthropic.stream_post", _stub_stream_post):
        prov = _make_anthropic_provider()
        # 消费整个流
        async for _ in prov.stream_chat([], thinking=False, system="HELLO SYSTEM"):
            pass

    body = captured["body"]
    assert isinstance(body["system"], list), "system 应当是列表形式"
    assert len(body["system"]) == 1
    item = body["system"][0]
    assert item["type"] == "text"
    assert item["text"] == "HELLO SYSTEM"
    assert item["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_AnthropicProvider_system为空时不加cache_control() -> None:
    """spec checklist：system 为 None 时不应在请求体中出现 system 字段。"""
    captured: dict[str, Any] = {}

    async def _stub_stream_post(url, headers, body):
        captured["body"] = body
        if False:
            yield b""
        return

    with patch("mewcode.providers.anthropic.stream_post", _stub_stream_post):
        prov = _make_anthropic_provider()
        async for _ in prov.stream_chat([], thinking=False, system=None):
            pass

    assert "system" not in captured["body"]


@pytest.mark.asyncio
async def test_AnthropicProvider_tools_format_透传() -> None:
    """tools_format 由 chat 层准备好（含 cache_control），Provider 透传。"""
    captured: dict[str, Any] = {}

    async def _stub_stream_post(url, headers, body):
        captured["body"] = body
        if False:
            yield b""
        return

    tools = [
        {"name": "read", "description": "...", "input_schema": {}},
        {
            "name": "write",
            "description": "...",
            "input_schema": {},
            "cache_control": {"type": "ephemeral"},
        },
    ]

    with patch("mewcode.providers.anthropic.stream_post", _stub_stream_post):
        prov = _make_anthropic_provider()
        async for _ in prov.stream_chat(
            [], thinking=False, tools_format=tools
        ):
            pass

    assert captured["body"]["tools"] == tools
    assert captured["body"]["tools"][-1]["cache_control"] == {
        "type": "ephemeral"
    }


# ---------- AnthropicProvider SSE cache 字段解析 ----------


@pytest.mark.asyncio
async def test_AnthropicProvider_解析cache字段_message_start() -> None:
    """spec AC10：message_start 中含 cache 字段时，Usage 事件携带它们。"""
    import json

    # 模拟 SSE 帧序列
    frames = [
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"x","usage":'
        b'{"input_tokens":10,'
        b'"cache_creation_input_tokens":100,'
        b'"cache_read_input_tokens":200}}}\n\n',
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":50}}\n\n',
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n',
    ]

    async def _stub_stream_post(url, headers, body):
        for f in frames:
            yield f

    with patch("mewcode.providers.anthropic.stream_post", _stub_stream_post):
        prov = _make_anthropic_provider()
        usages: list[Usage] = []
        async for ev in prov.stream_chat([], thinking=False):
            if isinstance(ev, Usage):
                usages.append(ev)

    assert len(usages) == 1
    u = usages[0]
    assert u.input_tokens == 10
    assert u.output_tokens == 50
    assert u.cache_creation_input_tokens == 100
    assert u.cache_read_input_tokens == 200


@pytest.mark.asyncio
async def test_AnthropicProvider_无cache字段时为None() -> None:
    """后端不返回 cache 字段时，Usage.cache_* 应保持 None。"""
    frames = [
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n\n',
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":50}}\n\n',
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n',
    ]

    async def _stub_stream_post(url, headers, body):
        for f in frames:
            yield f

    with patch("mewcode.providers.anthropic.stream_post", _stub_stream_post):
        prov = _make_anthropic_provider()
        usages: list[Usage] = []
        async for ev in prov.stream_chat([], thinking=False):
            if isinstance(ev, Usage):
                usages.append(ev)

    assert len(usages) == 1
    u = usages[0]
    assert u.cache_creation_input_tokens is None
    assert u.cache_read_input_tokens is None
