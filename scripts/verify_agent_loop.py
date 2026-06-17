"""真实 API 端到端验证：Agent Loop 多轮 ReAct 循环（spec AC1）。

构造一个需要多轮工具调用的 prompt，验证：
- 迭代 1：模型并发调 2 个 read
- 迭代 2：模型调 write 创建文件
- 迭代 3：模型给出文本答复
- Stopped("natural", 3)
- 累计用量行
"""

import asyncio
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.chat import Session, run_turn
from mewcode.config import load
from mewcode.providers import build_provider
from mewcode.render import Renderer
from mewcode.system_prompt import build_system_prompt
from mewcode.tools import Sandbox, ToolRegistry, register_builtins


class _AutoYesConfirmer:
    """脚本测试用：DANGEROUS 工具自动批准。"""

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

    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd, tools=sorted(t.name for t in registry)
    )
    session = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
    )
    renderer = Renderer(Console())

    prompt = (
        "请读 README.md 和 pyproject.toml 两个文件，"
        "然后告诉我项目名和依赖列表，"
        "最后把依赖数量写入一个新文件 deps_count.txt"
    )
    ok = await run_turn(
        session, prompt, renderer,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
    )
    print(f"\n\n[run_turn returned] ok={ok}")

    # 验证多轮 Loop
    print(f"[LLM 请求次数] {prov.call_count if hasattr(prov, 'call_count') else 'N/A'}")
    print(f"[messages count] {len(session.messages)}")
    for i, m in enumerate(session.messages):
        kinds = [type(b).__name__ for b in m.content]
        print(f"  [{i}] role={m.role}  blocks={kinds}")

    # 应有多轮（>=4 条消息：user + assistant(R1) + user(tool_results) + assistant(R2) + ...）
    assert len(session.messages) >= 4, f"期望至少 4 条消息，实际 {len(session.messages)}"

    # deps_count.txt 应被创建
    deps_file = Path.cwd() / "deps_count.txt"
    assert deps_file.exists(), "deps_count.txt 应被创建"
    print(f"\n[deps_count.txt 内容]\n{deps_file.read_text(encoding='utf-8')}")

    # 清理测试文件
    deps_file.unlink()

    print("\n✓ Agent Loop 多轮验证通过")


if __name__ == "__main__":
    asyncio.run(main())
