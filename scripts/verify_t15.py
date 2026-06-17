"""T15 真实端到端验证脚本：chat.run_turn 协程。

期望：流式输出 + 灰字用量行；len(session.messages) == 2。
"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.chat import Session, run_turn
from mewcode.config import load
from mewcode.providers import build_provider
from mewcode.render import Renderer


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers[cfg.default])
    session = Session(provider=prov, current_provider_name=cfg.default)
    renderer = Renderer(Console())

    print("[turn 1]")
    ok = await run_turn(session, "用一句话介绍 Python", renderer)
    assert ok, "turn 1 应该成功"
    assert len(session.messages) == 2, f"期望 2 条消息，实际 {len(session.messages)}"

    print(f"\n[messages] count={len(session.messages)}")
    print(f"  [0] {session.messages[0].role}: {session.messages[0].content[:40]}...")
    print(f"  [1] {session.messages[1].role}: {session.messages[1].content[:60]}...")

    # 多轮对话验证（spec AC7）：第 2 轮引用第 1 轮的内容
    print("\n[turn 2 - 验证多轮上下文]")
    ok = await run_turn(session, "刚才你说的语言名字是什么？", renderer)
    assert ok
    assert len(session.messages) == 4, f"期望 4 条消息，实际 {len(session.messages)}"

    print(f"\n[messages] count={len(session.messages)}")


if __name__ == "__main__":
    asyncio.run(main())
