"""第十阶段斜杠命令系统端到端验证（spec 第十阶段 AC1-AC17）。

不依赖真实 LLM，不依赖真实文件系统。9 节验证：

1. 注册与导入                — register_builtins 成功；CommandType 三常量
2. 三类分组覆盖              — LOCAL / STATEFUL / PROMPT 三段均非空
3. 撞名 panic                — 与 /help 同名注册抛 CommandRegistrationError
4. 大小写不敏感              — /HELP 命中 /help
5. /help 三段输出            — print_command_groups 被调；隐藏命令不出现
6. /status 六节              — print_status 收到六节标题
7. /review 三态              — 空 / 无参 / 有参三种 prompt_text 形态
8. Completer 候选            — /se 单匹配；隐藏不补；空 prefix 不补
9. PLAN 前缀                 — _make_prompt 在 plan/do/默认下返回正确字符串
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 把项目根加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mewcode.commands import (  # noqa: E402
    COMMANDS,
    Command,
    CommandContext,
    CommandRegistrationError,
    CommandType,
    commands_by_type,
    dispatch,
    register,
    register_builtins,
    unregister_all,
    visible_command_names,
)
from mewcode.repl.completer import SlashCommandCompleter  # noqa: E402
from mewcode.repl.main_loop import _make_prompt  # noqa: E402


class _StubRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record

    def has(self, m: str) -> bool:
        return any(c[0] == m for c in self.calls)

    def first(self, m: str):
        for c in self.calls:
            if c[0] == m:
                return c
        raise AssertionError(f"renderer 未调用 {m}")


class _StubProvider:
    protocol = "anthropic"
    model = "m"

    async def stream_chat(self, *_a, **_kw):  # pragma: no cover
        if False:
            yield


class _StubSession:
    def __init__(self, messages=None) -> None:
        self.provider = _StubProvider()
        self.current_provider_name = "alpha"
        self.thinking_enabled = False
        self.mode = "do"
        self.session_id = "20260623-101530-aaaa"
        self.messages = messages or []

    def clear(self) -> None:
        self.messages.clear()
        self.session_id = "20260623-200000-bbbb"


class _FakeDocument:
    def __init__(self, text: str) -> None:
        self.text_before_cursor = text


def _ctx(session=None, renderer=None) -> CommandContext:
    return CommandContext(
        session=session or _StubSession(),
        app_config=None,  # type: ignore[arg-type]
        renderer=renderer or _StubRenderer(),  # type: ignore[arg-type]
    )


# ---------- 9 节 ----------


def section_1_register() -> None:
    print("[1] 注册与导入...")
    unregister_all()
    register_builtins()
    register_builtins()  # 幂等再调
    assert CommandType.LOCAL == "local"
    assert CommandType.STATEFUL == "stateful"
    assert CommandType.PROMPT == "prompt"
    # 关键命令都注册了
    for n in (
        "help", "status", "session", "memory", "permission",
        "clear", "plan", "do", "compact", "review", "exit",
    ):
        assert n in COMMANDS, f"缺命令 /{n}"
    # 旧名别名兼容
    assert COMMANDS["permissions"] is COMMANDS["permission"]
    assert COMMANDS["quit"] is COMMANDS["exit"]
    print(f"    COMMANDS keys 数: {len(COMMANDS)}")
    print("    幂等再注册不抛错 ✓")


def section_2_grouping() -> None:
    print("[2] 三类分组覆盖...")
    grouped = commands_by_type()
    assert grouped[CommandType.LOCAL], "LOCAL 桶不应为空"
    assert grouped[CommandType.STATEFUL], "STATEFUL 桶不应为空"
    assert grouped[CommandType.PROMPT], "PROMPT 桶不应为空"
    print(
        f"    LOCAL: {[c.name for c in grouped[CommandType.LOCAL]]}"
    )
    print(
        f"    STATEFUL: {[c.name for c in grouped[CommandType.STATEFUL]]}"
    )
    print(
        f"    PROMPT: {[c.name for c in grouped[CommandType.PROMPT]]}"
    )
    # 隐藏命令不在分组里
    all_names = {c.name for b in grouped.values() for c in b}
    for h in ("think", "provider", "providers", "instructions"):
        assert h not in all_names, f"{h} 不应可见"
    print("    隐藏命令 (think/provider/providers/instructions) 已过滤 ✓")


def section_3_dup_panic() -> None:
    print("[3] 撞名 panic...")
    try:
        register(Command(
            name="help",
            aliases=(),
            description="duplicate",
            handler=lambda c: None,  # type: ignore[arg-type]
            type=CommandType.LOCAL,
        ))
    except CommandRegistrationError as e:
        print(f"    抛 CommandRegistrationError: {e} ✓")
        return
    raise AssertionError("应抛 CommandRegistrationError 但未抛")


async def section_4_case_insensitive() -> None:
    print("[4] 大小写不敏感...")
    ctx = _ctx()
    result = await dispatch("/HELP", ctx)
    assert result is not None
    assert ctx.renderer.has("print_command_groups"), "/HELP 应命中 /help handler"
    print("    /HELP -> /help handler ✓")


async def section_5_help_groups() -> None:
    print("[5] /help 三段输出...")
    ctx = _ctx()
    await dispatch("/help", ctx)
    call = ctx.renderer.first("print_command_groups")
    grouped = call[1][0]
    assert CommandType.LOCAL in grouped
    assert CommandType.STATEFUL in grouped
    assert CommandType.PROMPT in grouped
    # /review 在 PROMPT 段
    assert any(c.name == "review" for c in grouped[CommandType.PROMPT])
    print("    三段标题齐 + /review 在 PROMPT 段 ✓")


async def section_6_status_sections() -> None:
    print("[6] /status 六节...")
    ctx = _ctx()
    await dispatch("/status", ctx)
    snap = ctx.renderer.first("print_status")[1][0]
    expected = {"供应商", "模式", "会话", "权限", "长期记忆", "项目指令"}
    missing = expected - set(snap.keys())
    assert not missing, f"缺节: {missing}"
    print(f"    六节: {sorted(snap.keys())} ✓")


async def section_7_review_three_states() -> None:
    print("[7] /review 三态...")
    # 空 session
    ctx1 = _ctx(session=_StubSession(messages=[]))
    r1 = await dispatch("/review", ctx1)
    assert r1.prompt_text is None
    infos = [c[1][0] for c in ctx1.renderer.calls if c[0] == "print_info"]
    assert any("尚无内容" in s for s in infos)
    print("    空 session 拒绝 ✓")

    # 非空 + 无参
    ctx2 = _ctx(session=_StubSession(messages=["m1"]))
    r2 = await dispatch("/review", ctx2)
    assert r2.prompt_text is not None
    assert "1. 修改是否完成" in r2.prompt_text
    assert "本次额外重点关注" not in r2.prompt_text
    print("    非空 + 无参 注入预设 5 条要点 ✓")

    # 非空 + 有参
    ctx3 = _ctx(session=_StubSession(messages=["m1"]))
    r3 = await dispatch("/review 关注 SQL 注入", ctx3)
    assert r3.prompt_text is not None
    assert "本次额外重点关注：关注 SQL 注入" in r3.prompt_text
    print("    非空 + 有参 追加额外重点 ✓")


def section_8_completer() -> None:
    print("[8] Completer 候选...")
    comp = SlashCommandCompleter()

    def cands(text: str) -> list[str]:
        return [c.text for c in comp.get_completions(_FakeDocument(text), None)]

    # /se → 单匹配 session
    assert cands("/se") == ["session"], f"/se 候选: {cands('/se')}"
    # /p → 含 permission / plan
    p = cands("/p")
    assert "permission" in p and "plan" in p
    # 隐藏不补
    assert "think" not in cands("/t")
    # 参数区不补
    assert cands("/help xxx") == []
    # 非斜杠不补
    assert cands("hello") == []
    # 空 prefix 不补
    assert cands("/") == []
    print("    单/多匹配/隐藏/参数区/非斜杠/空prefix 全部符合 ✓")


def section_9_plan_prefix() -> None:
    print("[9] PLAN prompt 前缀...")
    from types import SimpleNamespace

    assert _make_prompt(SimpleNamespace(mode="plan")) == "[PLAN] > "
    assert _make_prompt(SimpleNamespace(mode="do")) == "> "
    assert _make_prompt(SimpleNamespace(mode="default")) == "> "
    assert _make_prompt(SimpleNamespace()) == "> "  # 缺 mode 字段兜底
    print("    plan -> '[PLAN] > '；其他 -> '> ' ✓")


async def _amain() -> None:
    section_1_register()
    section_2_grouping()
    section_3_dup_panic()
    await section_4_case_insensitive()
    await section_5_help_groups()
    await section_6_status_sections()
    await section_7_review_three_states()
    section_8_completer()
    section_9_plan_prefix()


def main() -> int:
    try:
        asyncio.run(_amain())
    except AssertionError as e:
        print(f"\n✗ 验证失败: {e}")
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ 异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    print("\n✓ 命令系统端到端通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
