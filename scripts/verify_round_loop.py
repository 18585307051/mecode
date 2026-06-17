"""完整闭环真实端到端验证（spec AC22）。

不依赖 REPL 交互——直接调 chat.run_turn 跑：
    用户 prompt → R1 (含 tool_use) → 执行工具 → R2 (文本答复)

构造 stub Confirmer 自动批准 DANGEROUS 工具（避免脚本卡住）；read 是
SAFE 工具，本场景实际无需 confirm。

成功标志：
- session.messages 末尾 4 条：user / R1 / tool_results / R2
- R2 答复中包含 README 第一行的内容
- stderr 完全干净
"""

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.chat import Session, run_turn
from mewcode.config import load
from mewcode.providers import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    build_provider,
)
from mewcode.render import Renderer
from mewcode.tools import Sandbox, ToolRegistry, register_builtins


class _AutoYesConfirmer:
    """脚本测试用：DANGEROUS 工具自动批准。生产仍用 prompt_toolkit Confirmer。"""

    async def ask(self, tool_name: str) -> bool:
        print(f"[auto-confirm] {tool_name}: y", flush=True)
        return True


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-anthropic"])
    print(f"[provider] protocol={prov.protocol} model={prov.model}")

    registry = ToolRegistry()
    register_builtins(registry)
    sandbox = Sandbox(cwd=Path.cwd())
    confirmer = _AutoYesConfirmer()

    session = Session(provider=prov, current_provider_name="deepseek-anthropic")
    renderer = Renderer(Console())

    prompt = "请用 read 工具读取当前工作目录下的 README.md 文件，然后告诉我项目的标题是什么。"
    ok = await run_turn(
        session, prompt, renderer,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
    )
    print(f"\n\n[run_turn returned] ok={ok}")

    # 验证消息历史结构
    print(f"[messages count] {len(session.messages)}")
    for i, m in enumerate(session.messages):
        kinds = [type(b).__name__ for b in m.content]
        print(f"  [{i}] role={m.role}  blocks={kinds}")

    # 应有 4 条：user / assistant(R1 含 tool_use) / user(tool_results) / assistant(R2)
    assert len(session.messages) == 4, f"期望 4 条消息，实际 {len(session.messages)}"
    # R1 含 ToolUseBlock
    r1 = session.messages[1]
    assert any(isinstance(b, ToolUseBlock) for b in r1.content), "R1 应含 ToolUseBlock"
    # tool_results
    tr_msg = session.messages[2]
    assert all(isinstance(b, ToolResultBlock) for b in tr_msg.content), "应是 ToolResult 列表"
    # R2 含 TextBlock，且文本中应提到 MewCode（README.md 第一行 "# MewCode"）
    r2 = session.messages[3]
    r2_text = "".join(b.text for b in r2.content if isinstance(b, TextBlock))
    print(f"\n[R2 text]\n{r2_text}\n")
    assert "MewCode" in r2_text, f"R2 答复中应含 'MewCode'，实际：{r2_text!r}"

    print("\n✓ 完整闭环验证通过")


if __name__ == "__main__":
    asyncio.run(main())
