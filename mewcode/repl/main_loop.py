"""REPL 主循环。

职责：
- 用 prompt_toolkit 的 PromptSession 读取用户输入（自带方向键历史）。
- 区分命令行（以 / 开头）与对话行：命令交给 commands.dispatch，
  对话交给 chat.run_turn。
- 透传 ToolRegistry / Sandbox / Confirmer 给 chat.run_turn（第二阶段）。
- 处理 prompt 阶段的 Ctrl+C：第一次提示，第二次退出（spec AC22）。
- 处理 EOF（Ctrl+D / Ctrl+Z）：等同 /exit。

关键约定：
- 流式阶段的 Ctrl+C 由 chat.run_turn 自捕获处理；不会冒泡到本循环。
- ProviderError 也由 run_turn 处理；本循环不感知。
"""

import asyncio

from prompt_toolkit import PromptSession

from mewcode.chat import Session, run_turn
from mewcode.commands import (
    CommandContext,
    dispatch,
    register_builtins,
)
from mewcode.config import AppConfig
from mewcode.render import Renderer
from mewcode.tools import Confirmer, Sandbox, ToolRegistry

PROMPT = "> "


async def run_repl(
    session: Session,
    app_config: AppConfig,
    renderer: Renderer,
    registry: ToolRegistry,
    sandbox: Sandbox,
    confirmer: Confirmer,
    *,
    policy=None,
    asker=None,
    instructions=None,
    rebuild_system_prompt=None,
    compactor=None,
    archive=None,
    memory_manager=None,
) -> int:
    """REPL 主循环。

    Args:
        session:    会话状态容器。
        app_config: 已加载的 mewcode.yaml。
        renderer:   终端渲染器。
        registry:   工具注册中心（spec F2 / F21：始终启用，不可为空）。
        sandbox:    工作目录沙盒（spec F10）。
        confirmer:  用户 y/N 确认器（DANGEROUS 工具执行前调用）。
        policy:     第五阶段权限策略（可选）。None 时不做权限检查。
        asker:      第五阶段人在回路询问器（可选）。
        instructions:  第七阶段项目指令加载器（可选）。供 /instructions 命令使用。
        rebuild_system_prompt: 第七阶段：reload 时重建 system prompt 的 callable。
        compactor:  第八阶段上下文压缩器（可选）。供 /compact 与 run_turn 使用。
        archive:    第九阶段 SessionArchive，目前主要供 Session 自动写盘用，
            REPL 这一层透传以便后续命令引用。
        memory_manager: 第九阶段 MemoryManager，run_turn 在 natural stop 后用它
            调度后台记忆更新。

    Returns:
        进程退出码：0 = 正常退出。
    """
    # 注册内置命令（幂等）
    register_builtins()

    # 启动横幅与提示
    renderer.print_banner(
        provider_name=session.current_provider_name,
        protocol=session.provider.protocol,
        model=session.provider.model,
    )
    renderer.print_help_hint(["/help", "/exit"])

    pt_session: PromptSession = PromptSession()
    ctrl_c_pending = False

    while True:
        # 1. 读取一行输入
        try:
            line = await pt_session.prompt_async(PROMPT)
        except EOFError:
            # Ctrl+D / Ctrl+Z → 当 /exit
            return 0
        except KeyboardInterrupt:
            if ctrl_c_pending:
                return 0
            ctrl_c_pending = True
            renderer.print_info(
                "再按一次 Ctrl+C 或输入 /exit 退出"
            )
            continue
        except (asyncio.CancelledError, BaseException) as e:
            # 兜底：任何意外的取消/中断（如 SIGINT 在窗口期触发）当作
            # 用户取消处理；只对 KeyboardInterrupt / CancelledError 有效，
            # 其他真异常向上抛由 main 兜底打堆栈。
            if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                if ctrl_c_pending:
                    return 0
                ctrl_c_pending = True
                renderer.print_info(
                    "再按一次 Ctrl+C 或输入 /exit 退出"
                )
                continue
            raise

        # 2. 任何成功输入都重置双击退出状态
        ctrl_c_pending = False

        # 3. 空白行直接跳过
        if not line.strip():
            continue

        # 4. 命令分发
        ctx = CommandContext(
            session=session,
            app_config=app_config,
            args=[],
            renderer=renderer,
            policy=policy,
            instructions=instructions,
            rebuild_system_prompt=rebuild_system_prompt,
            compactor=compactor,
        )
        try:
            result = await dispatch(line, ctx)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # 命令执行期间被中断：回到 prompt
            continue
        if result is not None:
            if result.should_exit:
                return 0
            continue

        # 5. 对话分支：透传 registry/sandbox/confirmer + 第五阶段 policy/asker
        try:
            await run_turn(
                session, line, renderer,
                registry=registry,
                confirmer=confirmer,
                sandbox=sandbox,
                policy=policy,
                asker=asker,
                compactor=compactor,
                memory_manager=memory_manager,
                rebuild_system_prompt=rebuild_system_prompt,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            # 极端兜底：run_turn 应当自吞，万一漏出来也不让它冒到 main
            continue


