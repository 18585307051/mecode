"""系统提示构造。

在第二阶段开启工具系统后，模型需要知道当前运行环境的基本信息——
特别是平台和 shell 类型——才能正确使用 run 工具（例如 Windows cmd
不识别 pwd / ls，必须用 cd / dir）。

第一阶段 spec 规定"不设 system prompt"；本阶段在引入工具后必须修订
这一规则，否则模型会跨平台地默认使用 Linux 命令。

策略：
- 启动时一次性构造，运行期间不变（spec 不做配置热加载）
- 内容简短：平台、shell、Python 版本、工作目录、可用工具列表
- 明确告诉模型：用 run 工具时要根据 shell 选合适的命令
"""

import platform
import sys
from pathlib import Path


def _detect_shell_hint() -> str:
    """根据平台返回 shell 名称与常用命令对照提示。"""
    if sys.platform == "win32":
        return (
            "默认 shell 是 Windows cmd.exe（不是 bash/PowerShell）。"
            "常用命令对照："
            "查看当前目录用 `cd` 或 `echo %cd%`（不是 pwd）；"
            "列出文件用 `dir`（不是 ls）；"
            "查看文件内容用 `type`（不是 cat）；"
            "环境变量语法是 `%VAR%`（不是 $VAR）；"
            "管道 / 重定向语法基本与 bash 一致。"
        )
    elif sys.platform == "darwin":
        return "默认 shell 是 macOS 上的 zsh / bash，使用标准 POSIX 命令。"
    else:
        return "默认 shell 是 Linux 上的 bash，使用标准 POSIX 命令。"


def build_system_prompt(cwd: Path, tools: list[str]) -> str:
    """构造环境感知的 system prompt。

    Args:
        cwd:   工作目录的绝对路径。
        tools: 已注册的工具名列表（通常 6 个）。

    Returns:
        多行字符串，作为请求体中的 system 字段发给模型。
    """
    plat = platform.system()  # 'Windows' / 'Linux' / 'Darwin'
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    shell_hint = _detect_shell_hint()
    tools_str = " / ".join(tools)

    prompt = (
        "你是 MewCode 内置的 AI 编程助手，运行在用户终端中。\n"
        "\n"
        "## 当前环境\n"
        f"- 操作系统：{plat}（{platform.platform()}）\n"
        f"- Python 版本：{py_ver}\n"
        f"- 工作目录：{cwd}\n"
        f"- {shell_hint}\n"
        "\n"
        "## 可用工具\n"
        f"{tools_str}\n"
        "\n"
        "## 工具使用守则\n"
        "- 路径相关参数限定在工作目录内（绝对路径或相对路径都接受），"
        "越界会被沙盒拒绝。\n"
        "- 使用 run 工具时，命令字符串必须匹配当前 shell——不要在"
        "Windows 上用 pwd / ls / cat，不要在 Linux 上用 dir / type。\n"
        "- 优先用 read / glob / search 等专用工具读取信息，而不是 "
        "`run cat xxx` 或 `run dir`。\n"
        "- 对中文输出友好——用户使用中文，你也用中文回答。\n"
    )
    return prompt
