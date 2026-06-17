"""ToolRegistry 的单元测试。

不依赖具体工具实现（read/write/...），用 stub Tool 子类验证：
- 注册与按名查找
- 重复注册覆盖
- 迭代
- 协议格式输出（Anthropic / OpenAI）
- 扩展性结构（spec AC4）
- register_builtins 串通 6 个内置工具（T16 之后才能跑通，本文件先标记跳过）
"""

from collections.abc import AsyncIterator
from typing import Iterator

import pytest

from mewcode.tools import (
    DangerLevel,
    Tool,
    ToolRegistry,
    ToolResult,
)


# ---------- stub 工具 ----------


class _StubTool(Tool):
    """测试用最小 Tool：execute 立即返回固定 ToolResult。"""

    name = "echo"
    description = "测试工具：返回参数中的 text 字段"
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    danger_level = DangerLevel.SAFE

    async def execute(self, params: dict, sandbox) -> ToolResult:
        return ToolResult(success=True, text=str(params.get("text", "")))


class _AnotherStub(Tool):
    name = "noop"
    description = "无操作"
    parameters_schema = {"type": "object", "properties": {}}
    danger_level = DangerLevel.DANGEROUS

    async def execute(self, params: dict, sandbox) -> ToolResult:
        return ToolResult(success=True, text="")


# ---------- 测试用例 ----------


def test_注册与按名查找() -> None:
    """register 后 get / __getitem__ / in 都能找到。"""
    r = ToolRegistry()
    t = _StubTool()
    r.register(t)

    assert r.get("echo") is t
    assert r["echo"] is t
    assert "echo" in r
    assert r.get("nonexistent") is None
    with pytest.raises(KeyError):
        _ = r["nonexistent"]


def test_重复注册覆盖() -> None:
    """同名工具后注册覆盖前者。"""
    r = ToolRegistry()
    t1 = _StubTool()
    t2 = _StubTool()
    r.register(t1)
    r.register(t2)
    assert r.get("echo") is t2
    assert len(r) == 1


def test_迭代与all() -> None:
    """__iter__ 与 all() 按注册顺序返回。"""
    r = ToolRegistry()
    a = _StubTool()
    b = _AnotherStub()
    r.register(a)
    r.register(b)

    assert list(r) == [a, b]
    assert r.all() == [a, b]
    assert len(r) == 2


def test_to_anthropic_format() -> None:
    """每项含 name / description / input_schema 三键，符合 Anthropic 协议。"""
    r = ToolRegistry()
    r.register(_StubTool())
    fmt = r.to_anthropic_format()
    assert len(fmt) == 1
    item = fmt[0]
    assert set(item.keys()) == {"name", "description", "input_schema"}
    assert item["name"] == "echo"
    assert item["input_schema"]["type"] == "object"
    assert "text" in item["input_schema"]["properties"]


def test_to_openai_format() -> None:
    """每项形如 {type:'function', function:{name, description, parameters}}。"""
    r = ToolRegistry()
    r.register(_StubTool())
    fmt = r.to_openai_format()
    assert len(fmt) == 1
    item = fmt[0]
    assert item["type"] == "function"
    fn = item["function"]
    assert set(fn.keys()) == {"name", "description", "parameters"}
    assert fn["name"] == "echo"


def test_扩展性可见() -> None:
    """spec AC4：注册新工具立即出现在两种协议格式输出中。"""
    r = ToolRegistry()
    r.register(_StubTool())
    assert "echo" in {it["name"] for it in r.to_anthropic_format()}

    # 注册新工具后立即可见
    r.register(_AnotherStub())
    anthropic_names = {it["name"] for it in r.to_anthropic_format()}
    openai_names = {it["function"]["name"] for it in r.to_openai_format()}
    assert {"echo", "noop"} <= anthropic_names
    assert {"echo", "noop"} <= openai_names


def test_注册空名应报错() -> None:
    """name 为空的 Tool 实例无法注册。"""

    class _Bad(_StubTool):
        name = ""

    r = ToolRegistry()
    with pytest.raises(ValueError):
        r.register(_Bad())


# ---------- register_builtins 串通测试（T16 后启用） ----------


def test_register_builtins_六个内置工具() -> None:
    """spec F2：register_builtins 应一次性注册全部 6 个内置工具。

    依赖 T10-T15 完成；当任一工具未实现时 import 会失败，本测试自动
    跳过。T16 完成后此测试应通过。
    """
    try:
        from mewcode.tools.registry import register_builtins
    except ImportError as e:
        pytest.skip(f"register_builtins 暂未就绪：{e}")

    r = ToolRegistry()
    try:
        register_builtins(r)
    except ImportError as e:
        pytest.skip(f"内置工具尚未全部实现：{e}")

    names = sorted(t.name for t in r)
    assert names == ["edit", "glob", "read", "run", "search", "write"]
    assert len(r.to_anthropic_format()) == 6
    assert len(r.to_openai_format()) == 6
