"""黑名单：硬拦截高危命令（spec F2 / D1）。

不可被配置或 yolo 模式绕过。仅作用于 run 工具的 command 参数。

DANGEROUS_PATTERNS 列表覆盖：
- rm -rf 致命路径（/, ~, $HOME, /*）
- 文件系统破坏（mkfs, dd of=/dev/sd*, > /dev/sd*）
- fork 炸弹
- 网络下载直接执行（curl/wget | sh/bash）
- Windows 等价破坏命令（format X:, rmdir /s /q X:）

新增模式时直接在列表内追加；测试通过 match_blocklist 验证。
"""

import re

DANGEROUS_PATTERNS: list[re.Pattern] = [
    # rm -rf 致命路径：可能的标志组合 + 危险路径结尾
    # 形式 A：rm -rf /  或  rm -rf ~
    re.compile(
        r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*r[a-zA-Z]*|-[rR]\s+-[fF]|-[fF]\s+-[rR])\s+(?:/|~|\$HOME|/\*)\s*$",
        re.IGNORECASE,
    ),
    # 形式 B：rm --recursive --force /
    re.compile(
        r"\brm\s+(?:--recursive\s+--force|--force\s+--recursive)\s+(?:/|~|\$HOME)\s*$",
        re.IGNORECASE,
    ),
    # 文件系统格式化
    re.compile(r"\bmkfs(?:\.[a-zA-Z0-9]+)?\s+", re.IGNORECASE),
    # dd 写入磁盘设备
    re.compile(r"\bdd\s+.*\bof=/dev/(?:sd|nvme|hd|xvd|disk)", re.IGNORECASE),
    # 重定向到磁盘设备
    re.compile(r">\s*/dev/(?:sd|nvme|hd|xvd|disk)", re.IGNORECASE),
    # fork 炸弹（经典写法）
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    # 网络下载直接执行：curl url | sh / wget url | bash
    re.compile(
        r"\b(?:curl|wget)\b[^|]*\|\s*(?:sh|bash|zsh|fish|dash)\b",
        re.IGNORECASE,
    ),
    # Windows: format X:
    re.compile(r"\bformat\s+[A-Za-z]:", re.IGNORECASE),
    # Windows: rmdir /s /q X:
    re.compile(
        r"\brmdir\s+(?:/[sS]\s+/[qQ]|/[qQ]\s+/[sS])\s+[A-Za-z]:",
        re.IGNORECASE,
    ),
    # Windows: del /s /q /f C:\* 类
    re.compile(
        r"\bdel\s+(?:/[sSqQfF]\s+){2,}\s*[A-Za-z]:[\\/]?\*?",
        re.IGNORECASE,
    ),
]


def match_blocklist(command: str) -> str | None:
    """检查命令是否触发黑名单。

    Args:
        command: run 工具的 command 参数原文。

    Returns:
        命中返回匹配的危险模式描述（pattern 字符串）；
        未命中返回 None。
    """
    if not command:
        return None
    for pat in DANGEROUS_PATTERNS:
        if pat.search(command):
            return pat.pattern
    return None
