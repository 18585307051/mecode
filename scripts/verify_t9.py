"""T9 真实端到端验证脚本：AnthropicProvider 不带 thinking。

期望：流式输出若干 TextDelta，最后 Usage + Done，无异常。
"""

import asyncio
import sys

# Windows cmd 默认 GBK，强制 stdout 用 UTF-8 输出，否则 emoji/中文标点会崩。
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
    usage_seen = False
    done_seen = False

    async for ev in prov.stream_chat(
        [Message.text("user", "你好，请用一句话自我介绍")],
        thinking=False,
    ):
        if isinstance(ev, TextDelta):
            text_count += 1
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ThinkingDelta):
            thinking_count += 1
            print(f"[!! thinking 不应出现] {ev.text}", flush=True)
        elif isinstance(ev, Usage):
            usage_seen = True
            print(
                f"\n[usage] input={ev.input_tokens} output={ev.output_tokens} "
                f"thinking={ev.thinking_tokens}"
            )
        elif isinstance(ev, Done):
            done_seen = True
            print("[done]")

    print(
        f"\n[summary] text_chunks={text_count} thinking_chunks={thinking_count} "
        f"usage={usage_seen} done={done_seen}"
    )
    assert text_count > 0, "应该收到至少 1 个 TextDelta"
    assert thinking_count == 0, "thinking=False 时不应有 ThinkingDelta"
    assert done_seen, "应该收到 Done 事件"


if __name__ == "__main__":
    asyncio.run(main())
