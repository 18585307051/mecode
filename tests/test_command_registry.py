"""命令注册表单测（spec 第十阶段 F2 / F3 / F4 / F13 + AC2-AC4, AC14）。

覆盖：
- 撞名 / alias 撞 name / alias 撞 alias / 自反 / 非法 type → CommandRegistrationError
- register 幂等（同字段值再次注册视为 noop）
- commands_by_type 分组、隐藏命令过滤、桶内升序
- visible_command_names 只列可见
- 大小写不敏感（dispatch / 注册 / 查询都按 lower 统一）
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mewcode.commands import (
    COMMANDS,
    Command,
    CommandContext,
    CommandRegistrationError,
    CommandResult,
    CommandType,
    commands_by_type,
    dispatch,
    register,
    register_builtins,
    unregister_all,
    visible_command_names,
)


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    """每个测试在干净的 COMMANDS 上跑，跑完恢复 builtins。"""
    snapshot = dict(COMMANDS)
    unregister_all()
    try:
        yield
    finally:
        unregister_all()
        COMMANDS.update(snapshot)


async def _noop(ctx: CommandContext) -> CommandResult:
    return CommandResult()


# ---------- 数据结构存在性 ----------


def test_command_dataclass_fields() -> None:
    """Command 必含 name/aliases/description/handler/type/usage/arg_hint/hidden。"""
    cmd = Command(
        name="x",
        aliases=("y",),
        description="d",
        handler=_noop,
        type=CommandType.LOCAL,
        usage="/x",
        arg_hint="<v>",
        hidden=True,
    )
    assert cmd.name == "x"
    assert cmd.aliases == ("y",)
    assert cmd.type == CommandType.LOCAL
    assert cmd.usage == "/x"
    assert cmd.arg_hint == "<v>"
    assert cmd.hidden is True


def test_command_type_constants() -> None:
    assert CommandType.LOCAL == "local"
    assert CommandType.STATEFUL == "stateful"
    assert CommandType.PROMPT == "prompt"


# ---------- 注册校验 ----------


def test_invalid_type_raises() -> None:
    cmd = Command(
        name="x",
        aliases=(),
        description="",
        handler=_noop,
        type="weird",
    )
    with pytest.raises(CommandRegistrationError, match="type"):
        register(cmd)


def test_empty_name_raises() -> None:
    cmd = Command(
        name="",
        aliases=(),
        description="",
        handler=_noop,
        type=CommandType.LOCAL,
    )
    with pytest.raises(CommandRegistrationError, match="name"):
        register(cmd)


def test_name_with_space_raises() -> None:
    cmd = Command(
        name="a b",
        aliases=(),
        description="",
        handler=_noop,
        type=CommandType.LOCAL,
    )
    with pytest.raises(CommandRegistrationError, match="空格"):
        register(cmd)


def test_self_referential_alias() -> None:
    """spec AC3：alias 含自身 name → CommandRegistrationError。"""
    cmd = Command(
        name="foo",
        aliases=("foo",),
        description="",
        handler=_noop,
        type=CommandType.LOCAL,
    )
    with pytest.raises(CommandRegistrationError, match="自反"):
        register(cmd)


def test_duplicate_alias_in_same_command() -> None:
    """同一命令的 aliases 内部重复 → 报错。"""
    cmd = Command(
        name="foo",
        aliases=("bar", "bar"),
        description="",
        handler=_noop,
        type=CommandType.LOCAL,
    )
    with pytest.raises(CommandRegistrationError, match="重复"):
        register(cmd)


def test_duplicate_name_raises() -> None:
    """spec AC2：同 name 注册第二条不同对象 → CommandRegistrationError。"""
    a = Command("foo", (), "a", _noop, type=CommandType.LOCAL)
    b = Command("foo", (), "b", _noop, type=CommandType.LOCAL)
    register(a)
    with pytest.raises(CommandRegistrationError, match="占用"):
        register(b)


def test_alias_conflict_with_other_name() -> None:
    """A.name == B.aliases 元素 → 报错。"""
    a = Command("foo", (), "a", _noop, type=CommandType.LOCAL)
    b = Command("bar", ("foo",), "b", _noop, type=CommandType.LOCAL)
    register(a)
    with pytest.raises(CommandRegistrationError, match="占用"):
        register(b)


def test_alias_conflict_between_aliases() -> None:
    """A.aliases ∩ B.aliases ≠ ∅ → 报错。"""
    a = Command("foo", ("x",), "a", _noop, type=CommandType.LOCAL)
    b = Command("bar", ("x",), "b", _noop, type=CommandType.LOCAL)
    register(a)
    with pytest.raises(CommandRegistrationError, match="占用"):
        register(b)


def test_register_idempotent_same_object() -> None:
    """同对象再次注册 → 静默 noop。"""
    cmd = Command("foo", (), "a", _noop, type=CommandType.LOCAL)
    register(cmd)
    register(cmd)
    assert COMMANDS["foo"] is cmd


def test_register_idempotent_equal_value() -> None:
    """frozen dataclass 字段全等 → 视为 noop，让 register_builtins 可多次调用。"""
    a = Command("foo", (), "a", _noop, type=CommandType.LOCAL)
    b = Command("foo", (), "a", _noop, type=CommandType.LOCAL)
    register(a)
    register(b)  # 不抛错
    assert COMMANDS["foo"] == a


def test_register_builtins_idempotent() -> None:
    """register_builtins() 连续两次调用不抛错（spec N4 + checklist 注册期）。"""
    register_builtins()
    register_builtins()  # 不抛
    # 关键命令都在
    assert "help" in COMMANDS
    assert "status" in COMMANDS
    assert "review" in COMMANDS


# ---------- 分组与可见命令 ----------


def test_commands_by_type_three_buckets() -> None:
    """三类命令分桶；空桶仍存在但为空 list。"""
    register(Command("a", (), "", _noop, type=CommandType.LOCAL))
    register(Command("b", (), "", _noop, type=CommandType.STATEFUL))
    register(Command("c", (), "", _noop, type=CommandType.PROMPT))
    grouped = commands_by_type()
    assert set(grouped.keys()) == {
        CommandType.LOCAL, CommandType.STATEFUL, CommandType.PROMPT,
    }
    assert [c.name for c in grouped[CommandType.LOCAL]] == ["a"]
    assert [c.name for c in grouped[CommandType.STATEFUL]] == ["b"]
    assert [c.name for c in grouped[CommandType.PROMPT]] == ["c"]


def test_commands_by_type_excludes_hidden() -> None:
    """hidden=True 不出现在 commands_by_type。"""
    register(Command("a", (), "", _noop, type=CommandType.LOCAL))
    register(Command("h", (), "", _noop, type=CommandType.LOCAL, hidden=True))
    grouped = commands_by_type()
    names = [c.name for c in grouped[CommandType.LOCAL]]
    assert names == ["a"]


def test_commands_by_type_bucket_sorted() -> None:
    register(Command("z", (), "", _noop, type=CommandType.LOCAL))
    register(Command("a", (), "", _noop, type=CommandType.LOCAL))
    register(Command("m", (), "", _noop, type=CommandType.LOCAL))
    grouped = commands_by_type()
    assert [c.name for c in grouped[CommandType.LOCAL]] == ["a", "m", "z"]


def test_visible_command_names_excludes_hidden() -> None:
    register(Command("vis", (), "", _noop, type=CommandType.LOCAL))
    register(Command(
        "secret", (), "", _noop, type=CommandType.LOCAL, hidden=True,
    ))
    names = visible_command_names()
    assert "vis" in names
    assert "secret" not in names


def test_visible_command_names_no_aliases() -> None:
    """别名不参与可见命令名列表。"""
    register(Command("foo", ("alias1",), "", _noop, type=CommandType.LOCAL))
    names = visible_command_names()
    assert "foo" in names
    assert "alias1" not in names


# ---------- dispatch 行为 ----------


class _StubRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record


@pytest.mark.asyncio
async def test_case_insensitive() -> None:
    """spec AC4：/HELP /Help /help 都能命中 help。"""
    hit = []

    async def _h(ctx: CommandContext) -> CommandResult:
        hit.append(True)
        return CommandResult()

    register(Command("help", (), "", _h, type=CommandType.LOCAL))
    renderer = _StubRenderer()
    ctx = CommandContext(session=None, app_config=None, renderer=renderer)  # type: ignore[arg-type]

    for line in ("/help", "/HELP", "/Help"):
        hit.clear()
        await dispatch(line, ctx)
        assert hit == [True], f"未命中 {line}"


@pytest.mark.asyncio
async def test_slash_only_unknown() -> None:
    """纯 `/` 走未知命令分支。"""
    register(Command("foo", (), "", _noop, type=CommandType.LOCAL))
    renderer = _StubRenderer()
    ctx = CommandContext(session=None, app_config=None, renderer=renderer)  # type: ignore[arg-type]
    result = await dispatch("/", ctx)
    assert result is not None
    assert result.should_exit is False
    # 调到 print_unknown_command
    assert any(c[0] == "print_unknown_command" for c in renderer.calls)
    # 第一个 arg 是 name=""
    call = next(c for c in renderer.calls if c[0] == "print_unknown_command")
    assert call[1][0] == ""


@pytest.mark.asyncio
async def test_unknown_command_only_lists_visible() -> None:
    """未知命令引导列表里不含 hidden。"""
    register(Command("vis", (), "", _noop, type=CommandType.LOCAL))
    register(Command(
        "secret", (), "", _noop, type=CommandType.LOCAL, hidden=True,
    ))
    renderer = _StubRenderer()
    ctx = CommandContext(session=None, app_config=None, renderer=renderer)  # type: ignore[arg-type]
    await dispatch("/foobar", ctx)
    call = next(c for c in renderer.calls if c[0] == "print_unknown_command")
    available = call[1][1]
    assert "vis" in available
    assert "secret" not in available


@pytest.mark.asyncio
async def test_non_slash_returns_none() -> None:
    renderer = _StubRenderer()
    ctx = CommandContext(session=None, app_config=None, renderer=renderer)  # type: ignore[arg-type]
    result = await dispatch("hello", ctx)
    assert result is None
