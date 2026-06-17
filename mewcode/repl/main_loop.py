"""REPL 主循环。

职责：
- 用 prompt_toolkit 的 PromptSession 读取用户输入（自带方向键历史）。
- 区分命令行（以 / 开头）与对话行：命令交给 commands.dispatch，
  对话交给 chat.run_turn。
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

PROMPT = "> "


async def run_repl(
    session: Session,
    app_config: AppConfig,
    renderer: Renderer,
) -> int:
    """REPL 主循环。

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

        # 5. 对话分支：run_turn 内部已处理 KeyboardInterrupt 与 ProviderError
        try:
            await run_turn(session, line, renderer)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # 极端兜底：run_turn 应当自吞，万一漏出来也不让它冒到 main
            continue

