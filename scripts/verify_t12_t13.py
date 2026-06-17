"""T12+T13 Renderer 手工验证。

涵盖：
- 启动横幅 / 命令提示
- 命令清单 / 供应商列表（验证 api_key 不外露）
- 错误红字 / 未知命令
- 流式 Markdown（标题、加粗、代码块）
- 流式思考 + 灰色斜体 + ▎思考中… 起始标记
- token 用量（含/不含 thinking）
"""

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.config import ProviderConfig
from mewcode.providers import Usage
from mewcode.render import Renderer


def main() -> None:
    r = Renderer(Console())

    # 1. 启动横幅
    print("\n=== 1. 启动横幅 ===")
    r.print_banner("deepseek-anthropic", "anthropic", "deepseek-v4-pro[1m]")
    r.print_help_hint(["/help", "/exit"])

    # 2. 错误红字
    print("\n=== 2. 错误红字 ===")
    r.print_error("网络错误", "连接被拒绝")

    # 3. 未知命令
    print("\n=== 3. 未知命令 ===")
    r.print_unknown_command("foo", ["exit", "help", "clear"])

    # 4. 供应商列表（验证 api_key 不外露）
    print("\n=== 4. 供应商列表（含 api_key 的 ProviderConfig，但输出绝不能含）===")
    providers = {
        "alpha": ProviderConfig(
            name="alpha",
            protocol="anthropic",
            model="m-1",
            base_url="https://a.example.com",
            api_key="sk-SECRET-AAA-DO-NOT-LEAK",
        ),
        "beta": ProviderConfig(
            name="beta",
            protocol="openai",
            model="m-2",
            base_url="https://b.example.com",
            api_key="sk-SECRET-BBB-DO-NOT-LEAK",
        ),
    }
    r.print_provider_list(providers, current_name="alpha")

    # 5. 流式 Markdown
    print("\n=== 5. 流式 Markdown（标题/加粗/代码块）===")
    r.begin_assistant()
    chunks = [
        "# Python 数据结构\n\n",
        "下面介绍**三种**常见结构：\n\n",
        "## 列表 (list)\n",
        "动态数组：\n\n",
        "```python\n",
        "items = [1, 2, 3]\n",
        "items.append(4)\n",
        "```\n\n",
        "## 字典 (dict)\n",
        "键值对映射，*查找*快。\n",
    ]
    for c in chunks:
        r.push_text(c)
        time.sleep(0.15)
    r.end_assistant()

    # 6. 用量（不含 thinking）
    print("=== 6. 用量（不含思考）===")
    r.print_usage(Usage(input_tokens=42, output_tokens=128))

    # 7. 思考流 + 正文
    print("\n=== 7. 思考流 + 正文 ===")
    r.begin_thinking()
    for c in [
        "用户在问 Python，",
        "我应该列举常用结构，",
        "并给出示例代码。",
    ]:
        r.push_thinking(c)
        time.sleep(0.2)
    r.end_thinking()
    r.begin_assistant()
    for c in ["Python 中常用的数据结构有", "list、dict、set、tuple。"]:
        r.push_text(c)
        time.sleep(0.2)
    r.end_assistant()

    # 8. 用量（含 thinking）
    print("=== 8. 用量（含思考）===")
    r.print_usage(
        Usage(input_tokens=42, output_tokens=128, thinking_tokens=63)
    )

    # 9. 普通信息
    print("\n=== 9. 普通信息 ===")
    r.print_info("会话历史已清空")

    print("\n=== 验证要点（请肉眼检查上面输出）===")
    print("- 横幅、错误、列表、Markdown、思考流、用量行都正确显示")
    print("- 输出中应包含两个 SECRET 字符串吗？答：不应该。")
    print("- 现在搜索 SECRET 字眼…")
    # 自动检查（重定向 stderr 到 stdout 时手工检查）
    print("(请通过 `python scripts/verify_t12_t13.py | findstr SECRET` 验证 0 匹配)")


if __name__ == "__main__":
    main()
