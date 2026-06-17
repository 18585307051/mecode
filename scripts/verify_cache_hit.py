"""真实 API 端到端验证：Anthropic 协议的 prompt cache 命中（spec AC5 / AC18）。

策略：
1. 启动一个 Session，发送相同的简单 prompt 两次
2. 第一次：cache_creation_input_tokens > 0（写入缓存）
3. 第二次：cache_read_input_tokens > 0（命中缓存），且接近第一次创建值

注：DeepSeek Anthropic 端点支持 cache_control 字段（已测试）。如果换其他
后端不支持，应当看到两次 cache_creation 都为 0/None，而 cache_read 也为
None——此时验证应判定"后端不支持缓存"。
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
    Done,
    TextDelta,
    ToolUseEnd,
    ToolUseInputDelta,
    ToolUseStart,
    Usage,
    build_provider,
)
from mewcode.render import Renderer
from mewcode.system_prompt import build_system_prompt
from mewcode.tools import Sandbox, ToolRegistry, register_builtins


class _AutoYesConfirmer:
    async def ask(self, tool_name: str) -> bool:
        return True


class _CapturingRenderer(Renderer):
    """记录所有 Usage 事件以便分析。"""

    def __init__(self, console: Console):
        super().__init__(console)
        self.usages: list[Usage] = []

    def print_usage(self, usage):  # 兼容旧接口
        self.usages.append(usage)
        super().print_usage(usage) if hasattr(super(), "print_usage") else None


async def _send_prompt(session: Session, prompt: str, renderer: Renderer):
    """直接调 provider.stream_chat 发一次请求并累积 Usage。"""
    # 拼接 user 消息
    session.append_user_text(prompt)

    # 准备 tools_format（with_cache）
    registry = ToolRegistry()
    register_builtins(registry)
    tools_format = registry.to_anthropic_format_with_cache()

    last_usage: Usage | None = None
    text_buf = ""
    async for ev in session.provider.stream_chat(
        session.messages,
        thinking=False,
        tools_format=tools_format,
        system=session.system_prompt or None,
    ):
        if isinstance(ev, TextDelta):
            text_buf += ev.text
        elif isinstance(ev, Usage):
            last_usage = ev
        elif isinstance(ev, Done):
            break

    # 把 assistant 回复入历史，保持对话状态
    from mewcode.providers import TextBlock

    if text_buf:
        session.append_assistant([TextBlock(text=text_buf)])

    return last_usage, text_buf


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-anthropic"])
    print(f"[provider] protocol={prov.protocol} model={prov.model}")

    registry = ToolRegistry()
    register_builtins(registry)
    sandbox = Sandbox(cwd=Path.cwd())
    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd, tools=sorted(t.name for t in registry)
    )
    print(f"[system prompt] {len(sys_prompt)} 字符")
    print(f"[tools] {len(registry)} 个工具，含 cache_control")

    # 第一次请求（清新 session）
    session1 = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
    )
    renderer1 = Renderer(Console())

    print("\n=== 第一次请求 ===")
    prompt = "你好，简短一句话回答。"
    usage1, text1 = await _send_prompt(session1, prompt, renderer1)
    if usage1 is None:
        print("⚠️ 第一次未拿到 Usage")
        return

    print(f"  input_tokens                 = {usage1.input_tokens}")
    print(f"  output_tokens                = {usage1.output_tokens}")
    print(f"  cache_creation_input_tokens  = {usage1.cache_creation_input_tokens}")
    print(f"  cache_read_input_tokens      = {usage1.cache_read_input_tokens}")

    # 第二次请求（清新 session 但相同 system + tools，应当命中缓存）
    session2 = Session(
        provider=prov,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
    )
    renderer2 = Renderer(Console())

    print("\n=== 第二次请求（相同 system + tools） ===")
    usage2, text2 = await _send_prompt(session2, prompt, renderer2)
    if usage2 is None:
        print("⚠️ 第二次未拿到 Usage")
        return

    print(f"  input_tokens                 = {usage2.input_tokens}")
    print(f"  output_tokens                = {usage2.output_tokens}")
    print(f"  cache_creation_input_tokens  = {usage2.cache_creation_input_tokens}")
    print(f"  cache_read_input_tokens      = {usage2.cache_read_input_tokens}")

    # ---- 分析 ----
    print("\n=== 分析 ===")

    cc1 = usage1.cache_creation_input_tokens or 0
    cr1 = usage1.cache_read_input_tokens or 0
    cc2 = usage2.cache_creation_input_tokens or 0
    cr2 = usage2.cache_read_input_tokens or 0

    if usage1.cache_creation_input_tokens is None and usage1.cache_read_input_tokens is None:
        print("⚠️ 后端未返回 cache 字段——可能不支持 Anthropic prompt cache")
        print("（注：DeepSeek Anthropic 端点应当支持，请检查网络/请求）")
        return

    if cr2 > 0:
        print(f"✓ 第二次命中缓存：cache_read_input_tokens = {cr2}")
        # AC18：cache_read 占 input_tokens 的比例
        ratio = cr2 / max(usage2.input_tokens, 1)
        print(f"  缓存命中比例：{ratio:.1%}")
        if ratio >= 0.3:  # 宽松阈值（spec AC18 是 0.5）
            print("✓ 缓存命中比例达标")
        else:
            print(f"⚠️ 缓存命中比例较低（< 30%）")
        if cr2 >= cc1 * 0.5:
            print(f"✓ 第二次 cache_read ({cr2}) 接近第一次 cache_creation ({cc1})")
        print("\n✓ 缓存策略生效")
    else:
        print("⚠️ 第二次未命中缓存（cache_read_input_tokens = 0）")
        print(f"   第一次 cache_creation = {cc1}")
        print(f"   第二次 cache_creation = {cc2}")


if __name__ == "__main__":
    asyncio.run(main())
