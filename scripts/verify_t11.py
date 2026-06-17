"""T11 真实端到端验证脚本：OpenAIProvider。"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mewcode.config import load
from mewcode.providers import (
    Done,
    Message,
    TextDelta,
    Usage,
    build_provider,
)


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-openai"])
    print(f"[provider] protocol={prov.protocol} model={prov.model}")

    text_count = 0
    usage: Usage | None = None
    done_seen = False

    async for ev in prov.stream_chat(
        [Message("user", "你好，用一句话自我介绍")],
        thinking=False,
    ):
        if isinstance(ev, TextDelta):
            text_count += 1
            print(ev.text, end="", flush=True)
        elif isinstance(ev, Usage):
            usage = ev
        elif isinstance(ev, Done):
            done_seen = True

    print(f"\n\n[summary] text_chunks={text_count} done={done_seen}")
    if usage is not None:
        print(
            f"[usage] input={usage.input_tokens} output={usage.output_tokens}"
        )

    assert text_count > 0
    assert done_seen


if __name__ == "__main__":
    asyncio.run(main())
