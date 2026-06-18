"""七个内置斜杠命令的实现。

调用 register_builtins() 把所有内置命令写入全局 COMMANDS 表。
此函数幂等：重复调用只会用相同内容覆盖。
"""

from mewcode.commands.registry import (
    COMMANDS,
    Command,
    CommandContext,
    CommandResult,
    register,
)
from mewcode.providers import build_provider


# ---------- handler 实现 ----------


async def _handle_exit(ctx: CommandContext) -> CommandResult:
    """/exit 与 /quit：通知 REPL 主循环退出。"""
    return CommandResult(should_exit=True)


async def _handle_help(ctx: CommandContext) -> CommandResult:
    """/help：列出所有命令及简短说明。"""
    # 去重：同一个 Command 对象在 COMMANDS 中可能因别名出现多次
    seen: set[int] = set()
    unique: list[Command] = []
    for cmd in COMMANDS.values():
        if id(cmd) in seen:
            continue
        seen.add(id(cmd))
        unique.append(cmd)
    # 按 name 排序，让输出稳定
    unique.sort(key=lambda c: c.name)
    ctx.renderer.print_command_list(unique)
    return CommandResult()


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


# ---------- /permissions 子命令族（第五阶段 spec F9） ----------


async def _handle_permissions(ctx: CommandContext) -> CommandResult:
    """/permissions [show|allow|deny|mode|reload|init] ...

    管理权限规则与模式。详见各子命令的 docstring。
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
        "用法：/permissions [show|allow|deny|mode|reload|init] ..."
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
            "（无任何规则。运行 /permissions init 创建模板，或 "
            "/permissions allow \"...\" 添加临时规则。）"
        )
    return CommandResult()


async def _permissions_allow(
    ctx: CommandContext, args: list[str]
) -> CommandResult:
    """添加一条会话级 allow 规则。

    用法：/permissions allow "Bash(git *)"
    """
    from mewcode.permissions.rules import parse_rule

    if not args:
        ctx.renderer.print_info('用法：/permissions allow "Bash(git *)"')
        return CommandResult()
    raw = " ".join(args).strip()
    # 去掉首尾的引号（用户可能从 shell 习惯加引号）
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
    """添加一条会话级 deny 规则。"""
    from mewcode.permissions.rules import parse_rule

    if not args:
        ctx.renderer.print_info('用法：/permissions deny "Bash(rm *)"')
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
    """临时切换权限模式（覆盖文件级配置）。"""
    if not args:
        ctx.renderer.print_info(
            f"当前模式：{ctx.policy.mode}\n"
            "用法：/permissions mode [strict|default|yolo]"
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
    """重新加载三层 YAML，并清空所有会话级状态。"""
    ctx.policy.reload()
    ctx.renderer.print_info(
        f"已重新加载权限规则。当前模式：{ctx.policy.mode}"
    )
    return CommandResult()


async def _permissions_init(ctx: CommandContext) -> CommandResult:
    """生成默认 permissions.yaml 模板，并把 local 文件加入 .gitignore。"""
    from pathlib import Path

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

    # 同时把 local 文件加入 .gitignore（spec N11）
    gitignore = cwd / ".gitignore"
    line = ".mewcode/permissions.local.yaml"
    try:
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            # 检查是否已存在该行（忽略首尾空格）
            existing_lines = {ln.strip() for ln in content.splitlines()}
            if line not in existing_lines:
                # 追加（保持文件原有换行结尾）
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


# ---------- 一次性注册 ----------


def register_builtins() -> None:
    """把所有内置命令写入全局 COMMANDS 表。幂等。"""
    register(
        Command(
            name="exit",
            aliases=("quit",),
            description="退出 MewCode",
            handler=_handle_exit,
        )
    )
    register(
        Command(
            name="help",
            aliases=(),
            description="列出所有可用命令",
            handler=_handle_help,
        )
    )
    register(
        Command(
            name="clear",
            aliases=(),
            description="清空当前会话历史，开始新对话",
            handler=_handle_clear,
        )
    )
    register(
        Command(
            name="think",
            aliases=(),
            description="/think on|off — 开关 extended thinking（仅 anthropic）",
            handler=_handle_think,
        )
    )
    register(
        Command(
            name="providers",
            aliases=(),
            description="列出所有已配置的供应商",
            handler=_handle_providers,
        )
    )
    register(
        Command(
            name="provider",
            aliases=(),
            description="/provider <name> — 切换供应商（清空历史）",
            handler=_handle_provider,
        )
    )
    register(
        Command(
            name="plan",
            aliases=(),
            description="切换到 Plan Mode（只读工具）",
            handler=_handle_plan,
        )
    )
    register(
        Command(
            name="do",
            aliases=(),
            description="切回执行模式（全部工具）",
            handler=_handle_do,
        )
    )
    register(
        Command(
            name="permissions",
            aliases=(),
            description="管理权限规则与模式（show/allow/deny/mode/reload/init）",
            handler=_handle_permissions,
        )
    )
