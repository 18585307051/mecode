"""动态环境信息生成（spec F2）。

环境信息从第二阶段单文件 system_prompt.py 演化而来，本阶段拆为独立
模块。本阶段视为"相对稳定"——cwd 不变、工具列表启动后不变、平台/Python
版本是常量，所以仍随 system 字段进入 prompt cache（spec D10）。

后续章节如果支持运行时 chdir，再考虑把环境信息拆出去走消息通道。
"""

import platform
import sys
from pathlib import Path


def build_env_section(cwd: Path, tools: list[str]) -> str:
    """生成 ## 当前环境 段落。

    Args:
        cwd:   工作目录绝对路径。
        tools: 已注册工具名列表（按字母序传入即可）。

    Returns:
        多行字符串，以 `## 当前环境\\n` 开头。
    """
    plat = platform.system()
    py_ver = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )
    shell_hint = _detect_shell_hint()
    tools_str = " / ".join(tools)

    return (
        "## 当前环境\n"
        f"- 操作系统：{plat}（{platform.platform()}）\n"
        f"- Python 版本：{py_ver}\n"
        f"- 工作目录：{cwd}\n"
        f"- 已注册工具：{tools_str}\n"
        f"- {shell_hint}"
    )


def _detect_shell_hint() -> str:
    """根据当前平台返回 shell 名称与常用命令对照提示。

    继承自第二阶段 system_prompt.py 的实现。spec F5 双重强化中"命令配
    shell"原则在 modules.TOOL_USAGE 已经覆盖；此处提示更具体的命令对照。
    """
    if sys.platform == "win32":
        return (
            "默认 shell 是 Windows cmd.exe（不是 bash / PowerShell）。"
            "常用命令对照："
            "查看当前目录用 `cd` 或 `echo %cd%`；"
            "列出文件用 `dir`；"
            "查看文件内容用 `type`；"
            "环境变量语法是 `%VAR%`（不是 $VAR）。"
        )
    elif sys.platform == "darwin":
        return "默认 shell 是 macOS 上的 zsh / bash，使用标准 POSIX 命令。"
    else:
        return "默认 shell 是 Linux 上的 bash，使用标准 POSIX 命令。"
