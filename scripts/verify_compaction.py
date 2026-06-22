"""上下文压缩端到端验证脚本。

验证：
1. 第一层：超大 ToolResultBlock 存盘 + 预览替换
2. 第二层：stub provider 生成摘要 + messages 替换为 boundary + recent
3. /compact 手动触发路径（Compactor.compact_now）
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mewcode.compaction import Compactor
from mewcode.compaction.compactor import AUTO_BUFFER
from mewcode.providers import (
    Done,
    Message,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)


class StubProvider:
    model = "deepseek-v4-pro"

    async def stream_chat(self, messages, thinking, **kwargs):
        assert kwargs.get("tools_format") is None, "摘要请求不应传工具"
        output = """<analysis>草稿：识别目标、决策、变更。</analysis>
<summary>
## 会话目标
验证上下文压缩功能。

## 关键决策
使用两层策略：工具结果存盘 + LLM 摘要。

## 代码变更
新增 compaction 模块与 /compact 命令。

## 未完成事项
需要全量回归。

## 当前状态
正在运行 verify_compaction.py。
</summary>"""
        for ch in output:
            yield TextDelta(text=ch)
        yield Done()


class StubSession:
    def __init__(self, cwd: Path) -> None:
        self.provider = StubProvider()
        self.session_id = "verify_session"
        self.last_usage_input_tokens = 0
        self.last_anchor_message_count = 0
        self.compaction_failures = 0
        self.compaction_disabled = False
        self.messages = []


def user(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def assistant(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


def assistant_tool(tid: str) -> Message:
    return Message(role="assistant", content=[ToolUseBlock(id=tid, name="read", input={})])


def tool_result(tid: str, content: str) -> Message:
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=tid, content=content, is_error=False)],
    )


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td)
        session = StubSession(cwd)
        compactor = Compactor(cwd)

        print("[1] 构造历史 + 超大工具结果...")
        # 早期大历史，保证可摘要
        session.messages.append(user("最初目标：实现上下文压缩"))
        for i in range(15):
            session.messages.append(assistant(f"早期回答 {i} " + "x" * 3000))
            session.messages.append(user(f"早期追问 {i} " + "y" * 3000))

        # 最新工具结果，触发第一层存盘
        session.messages.append(assistant_tool("big1"))
        huge = "\n".join(f"line {i}" for i in range(3000))
        session.messages.append(tool_result("big1", huge))

        print("[2] 第一层 + 第二层 before_request...")
        # 通过锚点把估算推过阈值，触发第二层
        session.last_usage_input_tokens = 128000 - AUTO_BUFFER + 100
        session.last_anchor_message_count = len(session.messages)

        stats = await compactor.before_request(session)
        assert stats.stash_events, "第一层应该存盘至少一个工具结果"
        assert stats.summary_triggered, "第二层应该触发"
        assert stats.summary_succeeded, f"第二层应成功：{stats.summary_error}"
        print(f"    存盘数量: {len(stats.stash_events)}")
        print(f"    压缩消息数: {stats.compacted_message_count}")

        print("[3] 验证 transcripts 文件存在...")
        for ev in stats.stash_events:
            assert ev.file_path.exists(), ev.file_path
            assert ev.file_path.read_text(encoding="utf-8") == huge
            print(f"    {ev.file_path}")

        print("[4] 验证 messages 被替换为 boundary + recent...")
        first = session.messages[0].content[0].text
        assert "<system-reminder>" in first
        assert "[Context Compacted]" in first
        assert "验证上下文压缩功能" in first
        print("    boundary message: ✓")

        print("[5] after_response 更新锚点...")
        from mewcode.providers import Usage
        compactor.after_response(session, Usage(input_tokens=12345, output_tokens=10))
        assert session.last_usage_input_tokens == 12345
        assert session.last_anchor_message_count == len(session.messages)
        print("    anchor updated: ✓")

    print("\n✓ 上下文压缩端到端通过")


if __name__ == "__main__":
    asyncio.run(main())
