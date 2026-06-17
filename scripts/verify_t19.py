"""T19 真实端到端验证：OpenAIProvider 解析工具调用 SSE 事件。

切换到 deepseek-openai 供应商，发起需要工具的 prompt，观察事件流是否
含 ToolUseStart / ToolUseInputDelta / ToolUseEnd。
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
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
    build_provider,
)
from mewcode.tools import ToolRegistry, register_builtins


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-openai"])
    print(f"[provider] protocol={prov.protocol} model={prov.model}")

    registry = ToolRegistry()
    register_builtins(registry)
    tools_format = registry.to_openai_format()
    print(f"[tools] {len(tools_format)} 个工具已注册")

    prompt = (
        "请使用 read 工具读取当前工作目录下的 README.md 文件，告诉我项目的"
        "第一行标题是什么。"
    )

    text_chunks = 0
    tool_starts: list[tuple[str, str]] = []
    tool_input_deltas = 0
    tool_ends: list[tuple[str, str, dict]] = []
    usage_seen = False
    done_seen = False

    async for ev in prov.stream_chat(
        [Message.text("user", prompt)],
        thinking=False,
        tools_format=tools_format,
    ):
        if isinstance(ev, TextDelta):
            text_chunks += 1
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolUseStart):
            print(f"\n[ToolUseStart] id={ev.id} name={ev.name}", flush=True)
            tool_starts.append((ev.id, ev.name))
        elif isinstance(ev, ToolUseInputDelta):
            tool_input_deltas += 1
        elif isinstance(ev, ToolUseEnd):
            print(
                f"\n[ToolUseEnd]   id={ev.id} name={ev.name} input={ev.input}",
                flush=True,
            )
            tool_ends.append((ev.id, ev.name, ev.input))
        elif isinstance(ev, Usage):
            usage_seen = True
            print(
                f"\n[usage] input={ev.input_tokens} output={ev.output_tokens}"
            )
        elif isinstance(ev, Done):
            done_seen = True

    print(
        f"\n\n[summary] text_chunks={text_chunks} tool_starts={len(tool_starts)} "
        f"tool_input_deltas={tool_input_deltas} tool_ends={len(tool_ends)} "
        f"usage={usage_seen} done={done_seen}"
    )

    assert len(tool_starts) >= 1, "期望模型至少调用一次工具"
    assert len(tool_starts) == len(tool_ends), "Start/End 数量应匹配"
    names = [name for _, name in tool_starts]
    assert "read" in names, f"期望调用 read 工具，实际：{names}"
    for _, _, inp in tool_ends:
        assert isinstance(inp, dict), f"input 不是 dict：{inp!r}"
    assert done_seen, "应收到 Done"

    print("\n✓ T19 验证通过")


if __name__ == "__main__":
    asyncio.run(main())
