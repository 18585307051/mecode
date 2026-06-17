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
