"""内置斜杠命令实现 + 注册（第十阶段重构）。

本文件承载：
- STATEFUL 类 handler 的具体实现
- register_builtins() 把所有命令（含 LOCAL/PROMPT 类引自 views/review）
  写入全局 COMMANDS

LOCAL 类 handler（/help /status /session list/current /memory show/list）
位于 mewcode/commands/views.py。
PROMPT 类 handler（/review）位于 mewcode/commands/review.py。

register_builtins 自身幂等：
- 同对象重复注册被 registry.register 视为 noop。
- 测试场景下若需重新注册，先调用 unregister_all()。
"""

from __future__ import annotations

from pathlib import Path

from mewcode.commands.registry import (
    Command,
    CommandContext,
    CommandResult,
    CommandType,
    register,
)
from mewcode.commands.review import _handle_review
from mewcode.commands.views import (
    _handle_help,
    _handle_memory,
    _handle_session,
    _handle_status,
)
from mewcode.providers import build_provider


# ---------- STATEFUL handler：原有 12 条命令的实现 ----------


async def _handle_exit(ctx: CommandContext) -> CommandResult:
    """/exit 与 /quit：通知 REPL 主循环退出。"""
    return CommandResult(should_exit=True)


async def _handle_clear(ctx: CommandContext) -> CommandResult:
    """/clear：清空当前会话的消息历史。"""
    ctx.session.clear()
    ctx.renderer.print_info("会话历史已清空")
    return CommandResult()


async def _handle_think(ctx: CommandContext) -> CommandResult:
    """/think on|off：开关 extended thinking。"""
    if not ctx.args:
        ctx.renderer.print_info("用法: /think on|off")
        return CommandResult()

    arg = ctx.args[0].lower()
    if arg == "on":
        if ctx.session.provider.protocol != "anthropic":
            ctx.renderer.print_info(
                "当前协议（"
                f"{ctx.session.provider.protocol}"
                "）不支持 extended thinking；仅 anthropic 协议可用此功能。"
            )
            return CommandResult()
        ctx.session.thinking_enabled = True
        ctx.renderer.print_info("extended thinking 已开启")
    elif arg == "off":
        ctx.session.thinking_enabled = False
        ctx.renderer.print_info("extended thinking 已关闭")
    else:
        ctx.renderer.print_info("用法: /think on|off")

    return CommandResult()


async def _handle_providers(ctx: CommandContext) -> CommandResult:
    """/providers：列出已配置的供应商，标记当前生效项。"""
    ctx.renderer.print_provider_list(
        ctx.app_config.providers,
        current_name=ctx.session.current_provider_name,
    )
    return CommandResult()


async def _handle_provider(ctx: CommandContext) -> CommandResult:
    """/provider <name>：切换到指定供应商，清空历史。"""
    if not ctx.args:
        ctx.renderer.print_info("用法: /provider <name>")
        ctx.renderer.print_provider_list(
            ctx.app_config.providers,
            current_name=ctx.session.current_provider_name,
        )
        return CommandResult()

    target_name = ctx.args[0]
    target_cfg = ctx.app_config.providers.get(target_name)
    if target_cfg is None:
        ctx.renderer.print_info(f"供应商不存在: {target_name}")
        ctx.renderer.print_provider_list(
            ctx.app_config.providers,
            current_name=ctx.session.current_provider_name,
        )
        return CommandResult()

    new_provider = build_provider(target_cfg)
    ctx.session.switch_provider(new_provider, name=target_name)
    ctx.renderer.print_info(
        f"已切换到 {target_name}（协议: {target_cfg.protocol}, "
        f"模型: {target_cfg.model}）"
    )
    return CommandResult()


async def _handle_plan(ctx: CommandContext) -> CommandResult:
    """/plan：切换到 Plan Mode（只读工具）。"""
    ctx.session.mode = "plan"
    ctx.renderer.print_info("📋 Plan Mode：只读工具（read / glob / search）")
    return CommandResult()


async def _handle_do(ctx: CommandContext) -> CommandResult:
    """/do：切回执行模式（全部工具）。"""
    ctx.session.mode = "do"
    ctx.renderer.print_info("🔧 执行模式：全部工具")
    return CommandResult()


# ---------- /permission 子命令族（第五阶段 spec F9，第十阶段重命名） ----------


async def _handle_permission(ctx: CommandContext) -> CommandResult:
    """/permission [show|allow|deny|mode|reload|init] ...

    第十阶段把 /permissions 重命名为 /permission（单数），旧名作为别名保留。
    """
    policy = ctx.policy
    if policy is None:
        ctx.renderer.print_info(
            "权限系统未启用（启动时未注入 PermissionPolicy）。"
        )
        return CommandResult()

    if not ctx.args:
        return await _permissions_show(ctx)

    sub = ctx.args[0]
    rest = ctx.args[1:]

    if sub == "show":
        return await _permissions_show(ctx)
    if sub == "allow":
        return await _permissions_allow(ctx, rest)
    if sub == "deny":
        return await _permissions_deny(ctx, rest)
    if sub == "mode":
        return await _permissions_mode(ctx, rest)
    if sub == "reload":
        return await _permissions_reload(ctx)
    if sub == "init":
        return await _permissions_init(ctx)

    ctx.renderer.print_info(f"未知子命令：{sub}")
    ctx.renderer.print_info(
        "用法：/permission [show|allow|deny|mode|reload|init] ..."
    )
    return CommandResult()


async def _permissions_show(ctx: CommandContext) -> CommandResult:
    """打印当前生效的权限模式与规则列表。"""
    policy = ctx.policy
    ctx.renderer.print_info(f"权限模式：{policy.mode}")

    session_allow = policy.session_allow
    session_deny = policy.session_deny
    if session_allow or session_deny:
        ctx.renderer.print_info("\n会话级规则（重启后失效）：")
        for r in session_allow:
            ctx.renderer.print_info(f"  allow: {r.raw}")
        for r in session_deny:
            ctx.renderer.print_info(f"  deny:  {r.raw}")

    file_allow = policy.all_allow[len(session_allow):]
    file_deny = policy.all_deny[len(session_deny):]
    if file_allow or file_deny:
        ctx.renderer.print_info("\n文件级规则（来自 .mewcode/permissions*.yaml）：")
        for r in file_allow:
            ctx.renderer.print_info(f"  allow: {r.raw}")
        for r in file_deny:
            ctx.renderer.print_info(f"  deny:  {r.raw}")
    elif not (session_allow or session_deny):
        ctx.renderer.print_info(
            "（无任何规则。运行 /permission init 创建模板，或 "
            "/permission allow \"...\" 添加临时规则。）"
        )
    return CommandResult()


async def _permissions_allow(
    ctx: CommandContext, args: list[str]
) -> CommandResult:
    from mewcode.permissions.rules import parse_rule

    if not args:
        ctx.renderer.print_info('用法：/permission allow "Bash(git *)"')
        return CommandResult()
    raw = " ".join(args).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1]
    rule = parse_rule(raw)
    if rule is None:
        ctx.renderer.print_info(
            f"非法规则格式：{raw!r}（应为 'ToolName(pattern)' "
            "如 Bash(git *) / Read(**/*.py)）"
        )
        return CommandResult()
    ctx.policy.add_session_allow(rule)
    ctx.renderer.print_info(f"已添加会话级 allow 规则：{rule.raw}")
    return CommandResult()


async def _permissions_deny(
    ctx: CommandContext, args: list[str]
) -> CommandResult:
    from mewcode.permissions.rules import parse_rule

    if not args:
        ctx.renderer.print_info('用法：/permission deny "Bash(rm *)"')
        return CommandResult()
    raw = " ".join(args).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1]
    rule = parse_rule(raw)
    if rule is None:
        ctx.renderer.print_info(f"非法规则格式：{raw!r}")
        return CommandResult()
    ctx.policy.add_session_deny(rule)
    ctx.renderer.print_info(f"已添加会话级 deny 规则：{rule.raw}")
    return CommandResult()


async def _permissions_mode(
    ctx: CommandContext, args: list[str]
) -> CommandResult:
    if not args:
        ctx.renderer.print_info(
            f"当前模式：{ctx.policy.mode}\n"
            "用法：/permission mode [strict|default|yolo]"
        )
        return CommandResult()
    mode = args[0].lower()
    try:
        ctx.policy.set_mode_override(mode)
    except ValueError as e:
        ctx.renderer.print_info(f"切换失败：{e}")
        return CommandResult()
    if mode == "yolo":
        ctx.renderer.print_info(
            "⚠️ 已切换到 YOLO 模式：除致命黑名单外全部放行"
        )
    else:
        ctx.renderer.print_info(f"已切换到 {mode} 模式")
    return CommandResult()


async def _permissions_reload(ctx: CommandContext) -> CommandResult:
    ctx.policy.reload()
    ctx.renderer.print_info(
        f"已重新加载权限规则。当前模式：{ctx.policy.mode}"
    )
    return CommandResult()


async def _permissions_init(ctx: CommandContext) -> CommandResult:
    cwd = ctx.policy.cwd if ctx.policy else Path.cwd()
    project_path = cwd / ".mewcode" / "permissions.yaml"

    if project_path.exists():
        ctx.renderer.print_info(
            f"文件已存在：{project_path}（未覆盖；如需重置请手动删除后再运行）"
        )
    else:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(_PERMISSIONS_TEMPLATE, encoding="utf-8")
        ctx.renderer.print_info(f"已生成 {project_path}")

    gitignore = cwd / ".gitignore"
    line = ".mewcode/permissions.local.yaml"
    try:
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            existing_lines = {ln.strip() for ln in content.splitlines()}
            if line not in existing_lines:
                if not content.endswith("\n"):
                    content += "\n"
                content += f"{line}\n"
                gitignore.write_text(content, encoding="utf-8")
                ctx.renderer.print_info(f"已把 {line} 加入 .gitignore")
        else:
            gitignore.write_text(f"{line}\n", encoding="utf-8")
            ctx.renderer.print_info(f"已创建 .gitignore 并加入 {line}")
    except OSError as e:
        ctx.renderer.print_info(f"⚠️ 修改 .gitignore 失败：{e}")

    return CommandResult()


_PERMISSIONS_TEMPLATE = """# MewCode 权限规则
# 详见 docs/06/spec.md 第五阶段权限系统

# 权限模式：strict / default / yolo
mode: default

# allow 列表：明确允许的工具调用（先匹配先生效）
allow:
  - "Bash(git *)"
  - "Bash(npm install*)"
  - "Bash(npm test*)"
  - "Bash(pytest*)"
  - "Bash(python -m*)"
  - "Read(**/*)"
  - "Glob(**/*)"
  - "Search(**/*)"

# deny 列表：明确拒绝的工具调用（优先级高于 allow）
deny:
  - "Edit(mewcode.yaml)"
  - "Edit(.git/**)"
  - "Edit(.env*)"
  - "Bash(rm -rf*)"
"""


# ---------- /instructions 命令族（第七阶段 spec F8/F9/F10） ----------


async def _handle_instructions(ctx: CommandContext) -> CommandResult:
    loader = ctx.instructions
    if loader is None:
        ctx.renderer.print_info(
            "项目指令系统未启用（启动时未注入 InstructionsLoader）。"
        )
        return CommandResult()

    sub = ctx.args[0] if ctx.args else "show"

    if sub == "show":
        return await _instructions_show(ctx)
    if sub == "reload":
        return await _instructions_reload(ctx)

    ctx.renderer.print_info(f"未知子命令：{sub}")
    ctx.renderer.print_info("用法：/instructions [show|reload]")
    return CommandResult()


async def _instructions_show(ctx: CommandContext) -> CommandResult:
    text = ctx.instructions.current_text()
    if not text:
        ctx.renderer.print_info(
            "当前未加载任何项目指令。"
            "建议在项目根创建 AGENTS.md 写明工作规则。"
        )
        return CommandResult()
    ctx.renderer.print_info(text)
    return CommandResult()


async def _instructions_reload(ctx: CommandContext) -> CommandResult:
    changed, new_text = ctx.instructions.reload_and_check()

    if not changed:
        ctx.renderer.print_info(
            "指令未变化，未重新构造 system prompt（cache 仍生效）。"
        )
        return CommandResult()

    rebuild = ctx.rebuild_system_prompt
    if callable(rebuild):
        rebuild(new_text)

    if new_text:
        size_kb = len(new_text.encode("utf-8")) / 1024
        ctx.renderer.print_info(
            f"已重新加载（{size_kb:.1f}KB）。"
            f"下次请求会重新建立 prompt cache。"
        )
    else:
        ctx.renderer.print_info(
            "已重新加载（三层均无内容，自定义指令段已清空）。"
        )
    return CommandResult()


# ---------- /compact 命令（第八阶段 spec F16） ----------


async def _handle_compact(ctx: CommandContext) -> CommandResult:
    compactor = getattr(ctx, "compactor", None)
    if compactor is None:
        ctx.renderer.print_info("压缩系统未启用（启动时未注入 Compactor）。")
        return CommandResult()

    instruction = " ".join(ctx.args).strip()

    try:
        stats = await compactor.compact_now(ctx.session, instruction)
    except Exception as e:
        ctx.renderer.print_info(f"⚠️ 压缩异常：{type(e).__name__}: {e}")
        return CommandResult()

    if stats.stash_events:
        ctx.renderer.print_info(
            f"📦 第一层：存盘 {len(stats.stash_events)} 个工具结果"
        )

    if stats.summary_succeeded:
        before = stats.estimated_tokens_before
        after = stats.estimated_tokens_after
        if before > after:
            ctx.renderer.print_info(
                f"🧠 已压缩 {stats.compacted_message_count} 条消息。"
                f"同口径估算 {before} → {after} tokens"
                f"（节省约 {before - after}）"
            )
        else:
            ctx.renderer.print_info(
                f"🧠 已压缩 {stats.compacted_message_count} 条消息。"
                f"当前历史估算约 {after} tokens"
            )
    elif stats.summary_triggered:
        ctx.renderer.print_info(
            f"⚠️ 压缩失败：{stats.summary_error or '未知错误'}"
        )
    elif not stats.stash_events:
        ctx.renderer.print_info(
            f"当前对话无需压缩（估算 {stats.estimated_tokens_before} tokens）。"
        )

    return CommandResult()


# ---------- /session new / /session resume（第十阶段 STATEFUL 子命令） ----------


async def _handle_session_new(ctx: CommandContext) -> CommandResult:
    """/session new：换发新 session_id，清空 messages。"""
    archive = ctx.archive
    if archive is None:
        ctx.renderer.print_info("会话存档未启用。")
        return CommandResult()
    try:
        # session.clear() 已经会调 _rotate_session_id；这里直接复用
        ctx.session.clear()
    except Exception as e:
        ctx.renderer.print_info(f"⚠️ 切换新会话失败：{e}")
        return CommandResult()
    ctx.renderer.print_info(
        f"已切换到新会话: {getattr(ctx.session, 'session_id', '(未知)')}"
    )
    return CommandResult()


async def _handle_session_resume(ctx: CommandContext) -> CommandResult:
    """/session resume <id>：加载指定会话并重建消息历史。"""
    archive = ctx.archive
    if archive is None:
        ctx.renderer.print_info("会话存档未启用。")
        return CommandResult()

    # ctx.args 形如 ["resume", "<id>"] 或 ["resume"]
    if len(ctx.args) < 2:
        ctx.renderer.print_info("用法：/session resume <id>")
        return CommandResult()

    target = ctx.args[1]

    # 先尝试精确命中
    result = archive.load_by_id(target)
    if not result.restored:
        # 前缀模糊匹配
        try:
            matches = archive.find_by_prefix(target)
        except Exception:
            matches = []
        if not matches:
            ctx.renderer.print_info(f"找不到会话：{target}")
            return CommandResult()
        if len(matches) > 1:
            ctx.renderer.print_info(
                f"会话 id 模糊匹配多于 1 条：{matches}\n请提供更具体的 id。"
            )
            return CommandResult()
        result = archive.load_by_id(matches[0])
        if not result.restored:
            ctx.renderer.print_info(f"无法恢复会话：{matches[0]}")
            return CommandResult()

    # 替换 session 状态
    archive.attach(ctx.session, result)
    ctx.renderer.print_info(
        f"💾 已恢复会话: {result.session_id}（{len(result.messages)} 条消息）"
    )
    if result.bad_lines:
        ctx.renderer.print_info(
            f"⚠️ 会话恢复跳过 {result.bad_lines} 行损坏记录"
        )
    if result.truncated:
        ctx.renderer.print_info(
            "⚠️ 会话恢复检测到未配对工具调用，已截断到上一条完整消息"
        )

    # 触发记忆段刷新（如有）
    rebuild = ctx.rebuild_system_prompt
    if ctx.memory_manager is not None and callable(rebuild):
        try:
            ctx.memory_manager.refresh_system_prompt_if_changed(rebuild)
        except Exception:
            pass

    return CommandResult()


# ---------- /memory refresh（第十阶段 STATEFUL 子命令） ----------


async def _handle_memory_refresh(ctx: CommandContext) -> CommandResult:
    """/memory refresh：强制重读 notes 重建 index，按 hash 决定是否重建 system_prompt。"""
    mm = ctx.memory_manager
    if mm is None:
        ctx.renderer.print_info("长期记忆未启用。")
        return CommandResult()
    rebuild = ctx.rebuild_system_prompt
    try:
        changed = await mm.refresh(rebuild_system_prompt=rebuild if callable(rebuild) else None)
    except Exception as e:
        ctx.renderer.print_info(f"⚠️ 刷新记忆失败：{e}")
        return CommandResult()
    if changed:
        ctx.renderer.print_info("🧠 已刷新长期记忆索引（system prompt 已重建）。")
    else:
        ctx.renderer.print_info("🧠 已刷新长期记忆索引（hash 未变，未重建 system prompt）。")
    return CommandResult()


# ---------- 一次性注册 ----------


def register_builtins() -> None:
    """把所有内置命令写入全局 COMMANDS。

    注册顺序无语义意义，按"用户认知频率"排，方便未来排查。
    register() 内部对"同对象"幂等，所以本函数可被多次调用而不抛错。
    """

    # ---- LOCAL ----
    register(Command(
        name="help",
        aliases=(),
        description="列出所有可用命令",
        handler=_handle_help,
        type=CommandType.LOCAL,
        usage="/help",
    ))
    register(Command(
        name="status",
        aliases=(),
        description="查看当前会话与各子系统状态仪表盘",
        handler=_handle_status,
        type=CommandType.LOCAL,
        usage="/status",
    ))
    register(Command(
        name="providers",
        aliases=(),
        description="列出所有已配置的供应商",
        handler=_handle_providers,
        type=CommandType.LOCAL,
        usage="/providers",
        hidden=True,
    ))

    # ---- STATEFUL ----
    register(Command(
        name="exit",
        aliases=("quit",),
        description="退出 MewCode",
        handler=_handle_exit,
        type=CommandType.STATEFUL,
        usage="/exit",
    ))
    register(Command(
        name="clear",
        aliases=(),
        description="清空当前会话历史，开始新对话",
        handler=_handle_clear,
        type=CommandType.STATEFUL,
        usage="/clear",
    ))
    register(Command(
        name="plan",
        aliases=(),
        description="切换到 Plan Mode（只读工具）",
        handler=_handle_plan,
        type=CommandType.STATEFUL,
        usage="/plan",
    ))
    register(Command(
        name="do",
        aliases=(),
        description="切回执行模式（全部工具）",
        handler=_handle_do,
        type=CommandType.STATEFUL,
        usage="/do",
    ))
    register(Command(
        name="compact",
        aliases=(),
        description="手动触发上下文压缩（可附自定义指示）",
        handler=_handle_compact,
        type=CommandType.STATEFUL,
        usage="/compact [自定义指示]",
    ))
    register(Command(
        name="session",
        aliases=(),
        description="管理会话存档（list/current/new/resume）",
        handler=_handle_session,
        type=CommandType.STATEFUL,
        usage="/session [list|current|new|resume <id>]",
    ))
    register(Command(
        name="memory",
        aliases=(),
        description="管理长期记忆索引（show/list/refresh）",
        handler=_handle_memory,
        type=CommandType.STATEFUL,
        usage="/memory [show|list [user|project]|refresh]",
    ))
    register(Command(
        name="permission",
        aliases=("permissions",),
        description="管理权限规则与模式（show/allow/deny/mode/reload/init）",
        handler=_handle_permission,
        type=CommandType.STATEFUL,
        usage="/permission [show|allow|deny|mode|reload|init]",
    ))
    register(Command(
        name="think",
        aliases=(),
        description="/think on|off — 开关 extended thinking（仅 anthropic）",
        handler=_handle_think,
        type=CommandType.STATEFUL,
        usage="/think on|off",
        hidden=True,
    ))
    register(Command(
        name="provider",
        aliases=(),
        description="/provider <name> — 切换供应商（清空历史）",
        handler=_handle_provider,
        type=CommandType.STATEFUL,
        usage="/provider <name>",
        hidden=True,
    ))
    register(Command(
        name="instructions",
        aliases=(),
        description="管理项目指令文件 AGENTS.md/CLAUDE.md（show/reload）",
        handler=_handle_instructions,
        type=CommandType.STATEFUL,
        usage="/instructions [show|reload]",
        hidden=True,
    ))

    # ---- PROMPT ----
    register(Command(
        name="review",
        aliases=(),
        description="让 AI 对本轮对话的所有改动做一次自检（可附侧重点）",
        handler=_handle_review,
        type=CommandType.PROMPT,
        usage="/review [侧重点]",
    ))
