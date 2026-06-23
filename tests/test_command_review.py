"""PROMPT 类命令 /review 单测（spec 第十阶段 F12 / AC13）。

三种情形：
1. session 空 → renderer.print_info "尚无内容..."，prompt_text 为 None。
2. session 非空 + 无参 → prompt_text 含五条预设要点。
3. session 非空 + 有参 → prompt_text 末尾含 "本次额外重点关注：..."。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mewcode.commands import (
    COMMANDS,
    CommandContext,
    dispatch,
    register_builtins,
    unregister_all,
)


class _StubRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record

    def infos(self) -> list[str]:
        return [c[1][0] for c in self.calls if c[0] == "print_info"]


class _StubSession:
    def __init__(self, messages=None) -> None:
        self.messages = messages or []


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    snapshot = dict(COMMANDS)
    unregister_all()
    register_builtins()
    try:
        yield
    finally:
        unregister_all()
        COMMANDS.update(snapshot)


def _make_ctx(messages=None, renderer=None) -> CommandContext:
    return CommandContext(
        session=_StubSession(messages),  # type: ignore[arg-type]
        app_config=None,  # type: ignore[arg-type]
        renderer=renderer or _StubRenderer(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_review_rejects_empty_session() -> None:
    """spec F12: messages 为空 → print_info 提示，不返回 prompt_text。"""
    ctx = _make_ctx(messages=[])
    result = await dispatch("/review", ctx)
    assert result is not None
    assert result.prompt_text is None
    infos = ctx.renderer.infos()
    assert any("尚无内容" in s for s in infos)


@pytest.mark.asyncio
async def test_review_default_prompt() -> None:
    """非空 session + 无参 → prompt_text 含五条预设要点。"""
    ctx = _make_ctx(messages=["m1", "m2"])
    result = await dispatch("/review", ctx)
    assert result is not None
    assert result.prompt_text is not None
    text = result.prompt_text
    # 五条要点都在
    assert "1. 修改是否完成" in text
    assert "2. 改动是否引入" in text
    assert "3. 是否有应该写但没写的测试" in text
    assert "4. 代码风格" in text
    assert "5. 是否破坏了现有功能" in text
    assert "风险等级" in text


@pytest.mark.asyncio
async def test_review_appends_extra() -> None:
    """非空 session + 用户参数 → prompt_text 末尾含『本次额外重点关注：...』。"""
    ctx = _make_ctx(messages=["m1"])
    result = await dispatch("/review 重点看 SQL 注入风险", ctx)
    assert result is not None
    assert result.prompt_text is not None
    text = result.prompt_text
    assert "1. 修改是否完成" in text  # 预设还在
    assert "本次额外重点关注：重点看 SQL 注入风险" in text
    # 额外内容应在末尾
    assert text.rstrip().endswith("本次额外重点关注：重点看 SQL 注入风险")


@pytest.mark.asyncio
async def test_review_empty_args_ignored() -> None:
    """空白参数 → 不追加额外要点段落。"""
    ctx = _make_ctx(messages=["m1"])
    result = await dispatch("/review    ", ctx)
    assert result is not None
    assert result.prompt_text is not None
    assert "本次额外重点关注" not in result.prompt_text
