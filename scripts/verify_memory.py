"""第九阶段端到端验证脚本：会话恢复 + 长期记忆。

运行：python scripts/verify_memory.py

覆盖目标：
1. 三层指令优先级 + @include 展开（F1 / F2）。
2. 会话 JSONL 追加 + 恢复 + 坏行跳过 + 截断（F4-F8）。
3. 自动笔记 create/update + index 重建（F13-F16）。
4. build_system_prompt 注入「长期记忆」段（F17）。

不需要任何外部 LLM，全部依赖本地实现。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Windows 终端默认 GBK，强制 UTF-8 避免 emoji / ✓ 触发 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 确保仓库根可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _section(title: str) -> None:
    print()
    print(f"==== {title} ====")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="mewcode-verify9-"))
    try:
        _run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


def _run(work: Path) -> None:
    home = work / "home"
    project = work / "project"
    home.mkdir()
    project.mkdir()

    # 临时把 ~/ 重定向，避免污染真实目录
    Path.home = classmethod(lambda cls: home)  # type: ignore[assignment]

    # ---- 1. 项目指令优先级 + include ----
    _section("1. 项目指令三层优先级 + @include")
    (home / ".mewcode").mkdir()
    (home / ".mewcode" / "AGENTS.md").write_text(
        "用户全局：偏好中文回答\n", encoding="utf-8"
    )
    (project / "AGENTS.md").write_text(
        "项目共享：跑 pytest tests/ -q\n@include docs/extra.md\n",
        encoding="utf-8",
    )
    (project / "docs").mkdir()
    (project / "docs" / "extra.md").write_text(
        "项目附加：MCP 验证脚本 verify_mcp.py\n", encoding="utf-8"
    )
    (project / ".mewcode").mkdir()
    (project / ".mewcode" / "AGENTS.local.md").write_text(
        "本地：禁止真实联网\n", encoding="utf-8"
    )

    from mewcode.instructions import InstructionsLoader

    loader = InstructionsLoader(project)
    text = loader.load_all() or ""
    assert "本地：禁止真实联网" in text, "本地级缺失"
    assert text.find("本地") < text.find("项目共享") < text.find("用户全局"), (
        "三层顺序错误"
    )
    assert "项目附加：MCP 验证脚本 verify_mcp.py" in text, "include 未展开"
    assert "<!-- begin include: docs/extra.md -->" in text
    print("✓ 三层优先级与 @include 工作正常")

    # ---- 2. 会话 JSONL 追加 + 坏行跳过 + 截断 ----
    _section("2. 会话存档与恢复")
    from mewcode.providers import (
        Message,
        TextBlock,
        ToolUseBlock,
    )
    from mewcode.sessions import SessionArchive
    from mewcode.sessions.codec import message_to_jsonl

    archive = SessionArchive(project)
    sid = archive.new_session_id()

    archive.append_message(sid, Message.text("user", "hi"))
    archive.append_message(
        sid,
        Message(
            role="assistant",
            content=[TextBlock(text="hello")],
        ),
    )

    # 写一行坏数据 + 一行孤儿 tool_use
    bad_line = "{not-json}\n"
    orphan = message_to_jsonl(
        Message(
            role="assistant",
            content=[ToolUseBlock(id="tu_x", name="read", input={})],
        )
    )
    with archive.session_path(sid).open("a", encoding="utf-8") as f:
        f.write(bad_line)
        f.write(orphan)

    result = archive.restore(sid)
    assert result.bad_lines >= 1, "坏行未计数"
    assert result.truncated, "孤儿 tool_use 未截断"
    assert all(
        not any(isinstance(b, ToolUseBlock) for b in m.content)
        for m in result.messages
    ), "截断后仍残留 tool_use"
    print(
        f"✓ 恢复成功，坏行 {result.bad_lines}，孤儿截断 {result.truncated}，"
        f"消息 {len(result.messages)} 条"
    )

    # ---- 3. 笔记 + index ----
    _section("3. 自动笔记与 index")
    from mewcode.memory import MemoryManager
    from mewcode.memory.notes import scope_root, list_notes
    from mewcode.memory.updater import MemoryOperation

    mgr = MemoryManager(project)

    class _S:
        session_id = sid
        provider = None
        system_prompt = ""

    sess = _S()

    # 模拟一次 LLM 输出 operation
    ops = [
        MemoryOperation(
            op="create", category="preference", body="使用中文回答"
        ),
        MemoryOperation(
            op="create",
            category="project_knowledge",
            body="测试命令：pytest tests/ -q",
            tags=["testing"],
        ),
    ]
    changed_scopes: set[str] = set()
    for op in ops:
        changes, ok = mgr._apply_operation(op, sess.session_id)
        assert ok, f"应用 {op.op} 失败"
        changed_scopes |= changes

    from mewcode.memory.index import rebuild_index

    for sc in changed_scopes:
        rebuild_index(scope_root(project, sc), sc)  # type: ignore[arg-type]

    user_notes = list_notes(scope_root(project, "user"))
    project_notes = list_notes(scope_root(project, "project"))
    assert len(user_notes) == 1 and "中文" in user_notes[0].body
    assert len(project_notes) == 1 and "pytest" in project_notes[0].body
    print(
        f"✓ 笔记已写入：用户级 {len(user_notes)} 条，项目级 {len(project_notes)} 条"
    )

    ctx = mgr.load_context()
    assert ctx.text and "## 长期记忆" not in ctx.text  # ctx.text 不含外层 H2
    assert "项目记忆" in ctx.text and "用户记忆" in ctx.text
    assert "中文" in ctx.text and "pytest" in ctx.text
    print("✓ 长期记忆段拼接正确")

    # ---- 4. build_system_prompt 注入 ----
    _section("4. system_prompt 注入")
    from mewcode.system_prompt import build_system_prompt

    sys_prompt = build_system_prompt(
        cwd=project,
        tools=["read", "write"],
        custom_instructions=loader.current_text(),
        memory=ctx.text,
    )
    assert "## 长期记忆" in sys_prompt, "system_prompt 未含长期记忆段"
    assert "项目记忆" in sys_prompt and "用户记忆" in sys_prompt
    assert "本地：禁止真实联网" in sys_prompt
    assert "项目附加：MCP 验证脚本 verify_mcp.py" in sys_prompt
    print("✓ build_system_prompt 同时注入指令与长期记忆")

    print()
    print("✓ 会话恢复与长期记忆端到端通过")


if __name__ == "__main__":
    sys.exit(main())
