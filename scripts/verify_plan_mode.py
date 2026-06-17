"""真实 API 端到端验证：Plan Mode 两段式（spec AC10）。

1. Plan Mode 下发起"读+写"prompt → 模型只能调只读工具
2. 切换到 Do Mode 后 → 模型可调 write
"""

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.chat import Session, run_turn
from mewcode.config import load
from mewcode.providers import build_provider, ToolUseBlock
from mewcode.render import Renderer
from mewcode.system_prompt import build_system_prompt
from mewcode.tools import Sandbox, ToolRegistry, register_builtins


class _AutoYesConfirmer:
    async def ask(self, tool_name: str) -> bool:
        return True


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-anthropic"])

    registry = ToolRegistry()
    register_builtins(registry)
    sandbox = Sandbox(cwd=Path.cwd())
    confirmer = _AutoYesConfirmer()

    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd, tools=sorted(t.name for t in registry)
    )

    # ---- 1. Plan Mode ----
    print("=" * 60)
    print("Phase 1: Plan Mode")
    print("=" * 60)

    session = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
        mode="plan",
    )
    renderer = Renderer(Console())

    prompt = "读 README.md 的第一行，然后写一个 test_plan.txt 文件内容是 'hello'"
    ok = await run_turn(
        session, prompt, renderer,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
    )
    print(f"\n[Plan Mode run_turn] ok={ok}")

    # Plan Mode 下不应创建 test_plan.txt
    test_file = Path.cwd() / "test_plan.txt"
    plan_mode_created = test_file.exists()
    print(f"[Plan Mode 下 test_plan.txt 是否被创建] {plan_mode_created}")
    assert not plan_mode_created, "Plan Mode 下不应创建文件"

    # 检查 Plan Mode 下：即便模型尝试调 write（可能因 system prompt 误导），
    # 实际文件也不应被创建（因为 tools_format 物理隔离 + 执行前拦截）
    write_attempts = 0
    for m in session.messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock):
                print(f"  [Plan Mode tool_use] name={b.name}")
                if b.name == "write":
                    write_attempts += 1
    if write_attempts > 0:
        print(f"\n⚠️  模型在 Plan Mode 下仍尝试调 write × {write_attempts} 次（被运行时拦截）")
    else:
        print(f"\n✓ 模型在 Plan Mode 下没有尝试调 write")

    print("\n✓ Plan Mode 物理隔离验证通过（文件未被创建）")

    # ---- 2. Do Mode ----
    print("\n" + "=" * 60)
    print("Phase 2: Do Mode")
    print("=" * 60)

    session2 = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
        mode="do",
    )
    renderer2 = Renderer(Console())

    ok2 = await run_turn(
        session2, prompt, renderer2,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
    )
    print(f"\n[Do Mode run_turn] ok={ok2}")

    do_mode_created = test_file.exists()
    print(f"[Do Mode 下 test_plan.txt 是否被创建] {do_mode_created}")
    assert do_mode_created, "Do Mode 下应能创建文件"

    # 清理
    if test_file.exists():
        test_file.unlink()

    print("\n✓ Do Mode 验证通过")
    print("\n✓ Plan Mode 两段式验证全部通过")


if __name__ == "__main__":
    asyncio.run(main())
