"""MewCode 入口。

负责：
1. 在 Windows 修复控制台 ANSI 支持。
2. 静默 asyncio 清理路径上的良性异常（防止 stderr 渗漏堆栈）。
3. 创建 Console 与 Renderer。
4. 从当前工作目录加载 mewcode.yaml。
5. 根据 default 供应商构造 Provider 与 Session。
6. 启动 asyncio 事件循环跑 REPL。
7. 把异常转换为退出码：
       0 - 正常退出
       1 - 配置错误（ConfigError）
       2 - 未预期异常

设计约束：除 Console 实例化外，main.py 不直接调用 rich API，所有终端
输出经 Renderer（spec N6 模块边界）。
"""

import asyncio
import sys
from pathlib import Path

from rich.console import Console

from mewcode.chat import Session
from mewcode.config import ConfigError, load
from mewcode.providers import build_provider
from mewcode.render import Renderer
from mewcode.repl import run_repl
from mewcode.tools import (
    Confirmer,
    Sandbox,
    ToolRegistry,
    register_builtins,
)

CONFIG_FILENAME = "mewcode.yaml"


def _fix_windows_console() -> None:
    """修复 Windows 控制台对 ANSI 转义序列的支持。

    问题背景：
        老版 Windows PowerShell 5.x、cmd.exe（早期版本）默认不解析 ANSI
        转义序列，会把 rich 发出的颜色码当成字面字符串显示。

    修复策略（双保险）：
        1) colorama.just_fix_windows_console()——优先尝试 SetConsoleMode
           启用 VT 模式（Win10 1607+ 快路径）；失败时 wrap stdout 把
           ANSI 翻译为 Win32 Console API 调用。
        2) 直接调 SetConsoleMode 兜底。

    在非 Windows 平台静默跳过。
    """
    if sys.platform != "win32":
        return

    # 把 sys.stdout / sys.stderr 编码强制改为 utf-8 + errors='replace'。
    # Windows cmd 默认 GBK，遇到 ▸ ↑ ↓ · ▎ 等 emoji 时会抛 UnicodeEncodeError。
    # reconfigure 是 Python 3.7+ TextIOWrapper 的能力。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    try:
        import colorama

        colorama.just_fix_windows_console()
    except Exception:
        pass

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        for std_handle in (STD_OUTPUT_HANDLE, STD_ERROR_HANDLE):
            handle = kernel32.GetStdHandle(std_handle)
            if handle in (0, -1):
                continue
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
    except Exception:
        pass


def _silence_async_cleanup_noise() -> None:
    """静默 asyncio 清理路径上的良性异常。

    问题：
        Provider 流被 Ctrl+C 中断或被 stream.aclose() 关闭时，httpx 在
        其 async 生成器的清理路径中会抛 ReadError、CancelledError、
        GeneratorExit 等异常。这些异常**不影响功能**，但 asyncio 默认
        通过 sys.excepthook / loop.default_exception_handler 把它们打到
        stderr，污染终端显示。

    修复：
        1) 装静默的事件循环 exception_handler。
        2) 装 unraisablehook 静默 async generator cleanup 告警。
        3) 装 sys.excepthook 静默 KeyboardInterrupt（用户主动退出时
           asyncio.Runner 可能让 KeyboardInterrupt 冒到 Python 顶层）。

    业务路径上的真异常仍会被 chat.run_turn 的 try/except 捕获并红字
    打印；本函数只屏蔽清理路径的 noise。
    """

    def _silent_handler(loop, context):
        return

    class _SilentEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            loop.set_exception_handler(_silent_handler)
            return loop

    try:
        asyncio.set_event_loop_policy(_SilentEventLoopPolicy())
    except Exception:
        pass

    def _silent_unraisable(unraisable) -> None:
        return

    try:
        sys.unraisablehook = _silent_unraisable
    except Exception:
        pass

    # KeyboardInterrupt / GeneratorExit 等"用户取消"类异常静默；其他真
    # 异常仍走默认 excepthook（不过 main 的 catch-all 应当已经拦住）。
    _orig_excepthook = sys.excepthook

    def _filtered_excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, (KeyboardInterrupt, GeneratorExit)):
            return
        _orig_excepthook(exc_type, exc, tb)

    try:
        sys.excepthook = _filtered_excepthook
    except Exception:
        pass


def main() -> int:
    """主入口。返回值即进程退出码。"""
    _fix_windows_console()
    _silence_async_cleanup_noise()

    console = Console()
    renderer = Renderer(console)

    # 阶段 1：加载配置
    try:
        app_config = load(Path.cwd() / CONFIG_FILENAME)
    except ConfigError as e:
        renderer.print_error(e.category, str(e))
        return 1

    # 阶段 2：装配对象图
    try:
        default_cfg = app_config.providers[app_config.default]
        provider = build_provider(default_cfg)
        # 第二阶段：构造工具系统三件套
        registry = ToolRegistry()
        register_builtins(registry)
        sandbox = Sandbox(cwd=Path.cwd())
        confirmer = Confirmer()
        # 构造环境感知的 system prompt（让模型知道 Win/Linux、shell 等）
        from mewcode.system_prompt import build_system_prompt

        sys_prompt = build_system_prompt(
            cwd=sandbox.cwd,
            tools=sorted(t.name for t in registry),
        )
        session = Session(
            provider=provider,
            current_provider_name=app_config.default,
            system_prompt=sys_prompt,
        )
    except Exception:
        renderer.print_exception()
        return 2

    # 阶段 3：启动 REPL
    # 关键：业务正常路径不会写 stderr，所有用户可见输出都经 Renderer 走
    # stdout（rich 的 Console 默认走 stdout）。但 httpx / asyncio 在
    # cleanup 路径上会偶发漏 traceback 到 stderr（spec N4 控制字符泄漏）。
    # REPL 启动后把 stderr 重定向到 devnull——彻底封锁 cleanup noise
    # 进入终端的最后路径。
    import os

    devnull_fd: int | None = None
    saved_stderr_fd: int | None = None
    try:
        try:
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            saved_stderr_fd = os.dup(2)
            os.dup2(devnull_fd, 2)
        except OSError:
            saved_stderr_fd = None

        try:
            return asyncio.run(
                run_repl(
                    session,
                    app_config,
                    renderer,
                    registry,
                    sandbox,
                    confirmer,
                )
            )
        except KeyboardInterrupt:
            # asyncio.Runner 在 SIGINT 时可能把 KeyboardInterrupt 一路抛
            # 到这里——这是用户主动退出意图，干净返回 0，不打堆栈。
            return 0
        except Exception:
            # 真异常：先恢复 stderr 再打印堆栈
            if saved_stderr_fd is not None:
                try:
                    os.dup2(saved_stderr_fd, 2)
                except OSError:
                    pass
            renderer.print_exception()
            return 2
    finally:
        # 关闭 devnull / 恢复 stderr（防御性，正常退出时进程将销毁）
        try:
            if saved_stderr_fd is not None:
                os.dup2(saved_stderr_fd, 2)
                os.close(saved_stderr_fd)
        except OSError:
            pass
        try:
            if devnull_fd is not None:
                os.close(devnull_fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
