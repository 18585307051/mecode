"""LOCAL 类命令实现：只读视图聚合（spec 第十阶段 F6 / F8 / F9 / F10）。

包含：
- _handle_help：按 type 三段分组渲染
- _handle_status：六节仪表盘
- _handle_session：list / current（STATEFUL 子命令转 builtin）
- _handle_memory：show / list（STATEFUL 子命令转 builtin）

避免循环 import：与 builtin 互引用的 STATEFUL handler 通过函数体内
延迟 import 解耦。
"""

from __future__ import annotations

from mewcode.commands.registry import (
    CommandContext,
    CommandResult,
    commands_by_type,
)


# ---------- /help ----------


async def _handle_help(ctx: CommandContext) -> CommandResult:
    """按 type 分三段输出。隐藏命令不出现。"""
    grouped = commands_by_type()
    ctx.renderer.print_command_groups(grouped)
    return CommandResult()


# ---------- /status ----------


def _collect_status(ctx: CommandContext) -> dict[str, list[str]]:
    """收集 6 节仪表盘数据。

    某子系统未启用时该节仅一行 "（未启用）"，确保 6 节齐整。
    """
    snapshot: dict[str, list[str]] = {}

    # 1. 供应商
    session = ctx.session
    if session is not None and getattr(session, "provider", None) is not None:
        provider = session.provider
        snapshot["供应商"] = [
            f"{getattr(session, 'current_provider_name', '') or '(未命名)'} / "
            f"{getattr(provider, 'protocol', '?')} / "
            f"{getattr(provider, 'model', '?')}"
        ]
    else:
        snapshot["供应商"] = ["（未启用）"]

    # 2. 模式
    if session is not None:
        mode = getattr(session, "mode", "do")
        thinking = getattr(session, "thinking_enabled", False)
        snapshot["模式"] = [
            "[PLAN] 计划模式（只读工具）" if mode == "plan" else "[DEFAULT] 执行模式",
            f"thinking: {'on' if thinking else 'off'}",
        ]
    else:
        snapshot["模式"] = ["（未启用）"]

    # 3. 会话
    sess_lines: list[str] = []
    if session is not None:
        sid = getattr(session, "session_id", "") or "(未分配)"
        msg_count = len(getattr(session, "messages", []) or [])
        sess_lines.append(f"id: {sid}")
        sess_lines.append(f"消息数: {msg_count}")
        compactor = ctx.compactor
        if compactor is not None and msg_count > 0:
            try:
                from mewcode.compaction.tokens import estimate_tokens
                est = estimate_tokens(session.messages, 0, 0)
                window = compactor.get_window(getattr(session.provider, "model", ""))
                # 与 compactor.AUTO_BUFFER 同步
                from mewcode.compaction.compactor import AUTO_BUFFER
                threshold = window - AUTO_BUFFER
                remaining = threshold - est
                sess_lines.append(
                    f"估算 tokens: {est} / 自动压缩阈值 {threshold}（余 {remaining}）"
                )
            except Exception:
                pass
    if not sess_lines:
        sess_lines = ["（未启用）"]
    snapshot["会话"] = sess_lines

    # 4. 权限
    policy = ctx.policy
    if policy is None:
        snapshot["权限"] = ["（未启用）"]
    else:
        try:
            mode = getattr(policy, "mode", "?")
            allow_n = len(getattr(policy, "all_allow", []))
            deny_n = len(getattr(policy, "all_deny", []))
            snapshot["权限"] = [
                f"mode: {mode}",
                f"规则: allow {allow_n} / deny {deny_n}",
            ]
        except Exception:
            snapshot["权限"] = ["（读取失败）"]

    # 5. 长期记忆
    mm = ctx.memory_manager
    if mm is None:
        snapshot["长期记忆"] = ["（未启用）"]
    else:
        try:
            user_notes = mm.list_notes("user")
            project_notes = mm.list_notes("project")
            text = mm.get_combined_index_text()
            byte_size = len(text.encode("utf-8"))
            line_count = len(text.splitlines()) if text else 0
            snapshot["长期记忆"] = [
                f"笔记数: user={len(user_notes)}  project={len(project_notes)}",
                f"index: {byte_size} 字节 / {line_count} 行",
            ]
        except Exception as e:
            snapshot["长期记忆"] = [f"（读取失败：{e}）"]

    # 6. 项目指令
    loader = ctx.instructions
    if loader is None:
        snapshot["项目指令"] = ["（未启用）"]
    else:
        try:
            layers = loader.loaded_layers() or []
            total_bytes = sum(getattr(layer, "bytes_len", 0) for layer in layers)
            if not layers:
                snapshot["项目指令"] = ["（无 AGENTS.md / CLAUDE.md / .mewcoderc）"]
            else:
                names = " + ".join(getattr(la, "name", "?") for la in layers)
                snapshot["项目指令"] = [
                    f"已加载 {len(layers)} 层: {names}",
                    f"合计 {total_bytes} 字节",
                ]
        except Exception:
            snapshot["项目指令"] = ["（读取失败）"]

    return snapshot


async def _handle_status(ctx: CommandContext) -> CommandResult:
    """LOCAL：仪表盘汇总六节状态。"""
    snapshot = _collect_status(ctx)
    ctx.renderer.print_status(snapshot)
    return CommandResult()


# ---------- /session ----------


async def _handle_session(ctx: CommandContext) -> CommandResult:
    """/session [list|current|new|resume <id>]

    list/current 在本文件实现；new/resume 转 builtin（STATEFUL）。
    """
    sub = ctx.args[0].lower() if ctx.args else "list"

    if sub == "list":
        return await _session_list(ctx)
    if sub == "current":
        return await _session_current(ctx)
    if sub == "new":
        # STATEFUL：lazy import 避免循环
        from mewcode.commands.builtin import _handle_session_new
        return await _handle_session_new(ctx)
    if sub == "resume":
        from mewcode.commands.builtin import _handle_session_resume
        return await _handle_session_resume(ctx)

    ctx.renderer.print_info(f"未知子命令：{sub}")
    ctx.renderer.print_info("用法：/session [list|current|new|resume <id>]")
    return CommandResult()


async def _session_list(ctx: CommandContext) -> CommandResult:
    archive = ctx.archive
    if archive is None:
        ctx.renderer.print_info("会话存档未启用。")
        return CommandResult()
    try:
        summaries = archive.scan_summaries() or []
    except Exception as e:
        ctx.renderer.print_info(f"读取会话目录失败：{e}")
        return CommandResult()

    rows = []
    current_id = getattr(ctx.session, "session_id", "")
    for s in summaries[:10]:
        rows.append({
            "session_id": s.session_id,
            "message_count": s.message_count,
            "updated_at": s.updated_at.strftime("%Y-%m-%d %H:%M"),
            "title": s.title,
        })
    ctx.renderer.print_session_list(rows, current_id=current_id)
    return CommandResult()


async def _session_current(ctx: CommandContext) -> CommandResult:
    sess = ctx.session
    if sess is None:
        ctx.renderer.print_info("无当前会话。")
        return CommandResult()
    info: dict[str, object] = {
        "id": getattr(sess, "session_id", "") or "(未分配)",
        "消息数": len(getattr(sess, "messages", []) or []),
        "模式": getattr(sess, "mode", "do"),
    }
    archive = ctx.archive
    if archive is not None:
        try:
            sid = getattr(sess, "session_id", "")
            if sid:
                path = archive.session_path(sid)
                info["JSONL"] = str(path)
        except Exception:
            pass
    ctx.renderer.print_session_current(info)
    return CommandResult()


# ---------- /memory ----------


async def _handle_memory(ctx: CommandContext) -> CommandResult:
    """/memory [show|list [user|project]|refresh]"""
    sub = ctx.args[0].lower() if ctx.args else "show"

    if sub == "show":
        return await _memory_show(ctx)
    if sub == "list":
        return await _memory_list(ctx)
    if sub == "refresh":
        from mewcode.commands.builtin import _handle_memory_refresh
        return await _handle_memory_refresh(ctx)

    ctx.renderer.print_info(f"未知子命令：{sub}")
    ctx.renderer.print_info("用法：/memory [show|list [user|project]|refresh]")
    return CommandResult()


async def _memory_show(ctx: CommandContext) -> CommandResult:
    mm = ctx.memory_manager
    if mm is None:
        ctx.renderer.print_info("长期记忆未启用。")
        return CommandResult()
    try:
        text = mm.get_combined_index_text()
    except Exception as e:
        ctx.renderer.print_info(f"读取记忆失败：{e}")
        return CommandResult()
    ctx.renderer.print_memory_index(text or "")
    return CommandResult()


async def _memory_list(ctx: CommandContext) -> CommandResult:
    mm = ctx.memory_manager
    if mm is None:
        ctx.renderer.print_info("长期记忆未启用。")
        return CommandResult()

    scope: str | None = None
    if len(ctx.args) >= 2:
        arg = ctx.args[1].lower()
        if arg in ("user", "project"):
            scope = arg
        else:
            ctx.renderer.print_info(f"非法 scope：{arg}（应为 user 或 project）")
            return CommandResult()

    try:
        rows = mm.list_notes(scope)
    except Exception as e:
        ctx.renderer.print_info(f"列出笔记失败：{e}")
        return CommandResult()
    ctx.renderer.print_note_list(rows)
    return CommandResult()
