"""验证 system prompt 让模型在 Windows 下用 cmd 命令而不是 pwd/ls。

发起一个让模型主动用 run 工具查看当前目录的 prompt，期望模型按 Windows
shell 语义选用 'cd' 或 'echo %cd%' 而不是 'pwd'。
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
    print(f"[system prompt 长度] {len(sys_prompt)} 字符")
    print(f"[system prompt 预览]\n{sys_prompt[:300]}...\n")

    session = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
    )
    renderer = Renderer(Console())

    prompt = "用 run 工具帮我查看当前在哪个目录"
    ok = await run_turn(
        session, prompt, renderer,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
    )
    print(f"\n[run_turn] ok={ok}")

    # 找到 R1 中的 ToolUseBlock，看模型选了什么命令
    for m in session.messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock) and b.name == "run":
                cmd = b.input.get("command", "")
                print(f"\n[模型选用的命令] {cmd!r}")
                # 不应包含 pwd（Linux 命令）
                assert "pwd" not in cmd.lower(), (
                    f"模型仍在 Windows 下用 pwd 命令：{cmd!r}"
                )
                # 应当包含 cd 或 echo（Windows 风格）
                lower = cmd.lower()
                assert "cd" in lower or "echo" in lower, (
                    f"期望 Windows 风格命令（cd/echo），实际：{cmd!r}"
                )
                print("✓ 模型按 Windows shell 选了正确命令")
                return

    print("⚠️ R1 未含 run 工具调用")


if __name__ == "__main__":
    asyncio.run(main())
