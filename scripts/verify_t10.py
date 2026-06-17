"""T10 真实端到端验证脚本：AnthropicProvider 带 thinking。

期望：先收到若干 ThinkingDelta，再收到若干 TextDelta，
最后 Usage（带 thinking_tokens 字段）+ Done。

运行：
    set MEWCODE_DEBUG_ANTHROPIC=1
    python scripts/verify_t10.py
（前者打开原始 SSE 帧打印，用于确认思考 token 字段名）
"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mewcode.config import load
from mewcode.providers import (
    Done,
    Message,
    TextDelta,
    ThinkingDelta,
    Usage,
    build_provider,
)


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-anthropic"])
    print(f"[provider] protocol={prov.protocol} model={prov.model}")

    text_count = 0
    thinking_count = 0
    usage: Usage | None = None
    done_seen = False

    print("\n>>> thinking 阶段：")
    async for ev in prov.stream_chat(
        [Message.text("user", "证明素数有无穷多个，简要说明思路")],
        thinking=True,
    ):
        if isinstance(ev, ThinkingDelta):
            if thinking_count == 0:
                print("\n[thinking 开始]\n", flush=True)
            thinking_count += 1
            print(ev.text, end="", flush=True)
        elif isinstance(ev, TextDelta):
            if text_count == 0:
                print("\n\n[正文开始]\n", flush=True)
            text_count += 1
            print(ev.text, end="", flush=True)
        elif isinstance(ev, Usage):
            usage = ev
        elif isinstance(ev, Done):
            done_seen = True

    print(
        f"\n\n[summary] thinking_chunks={thinking_count} "
        f"text_chunks={text_count} done={done_seen}"
    )
    if usage is not None:
        print(
            f"[usage] input={usage.input_tokens} output={usage.output_tokens} "
            f"thinking={usage.thinking_tokens}"
        )

    assert thinking_count > 0, "应该收到至少 1 个 ThinkingDelta"
    assert text_count > 0, "应该收到至少 1 个 TextDelta"
    assert done_seen, "应该收到 Done"


if __name__ == "__main__":
    asyncio.run(main())
