"""T18 验证：配置错误场景（AC2、AC3、AC4）。

直接调用 mewcode.main:main()，因为这两个错误场景在配置加载阶段就退出，
不会触及 prompt_toolkit，所以不需要真实 TTY。

REPL 启动场景（AC1）的验证留给 T20 在真实终端中跑。
"""

import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mewcode.main import main


def run_in(cwd: Path) -> int:
    """切到指定 cwd 跑 main，恢复后返回退出码。"""
    saved = os.getcwd()
    try:
        os.chdir(cwd)
        return main()
    finally:
        os.chdir(saved)


def test_ac2_缺失配置文件() -> None:
    """AC2: 不含 mewcode.yaml 的目录启动应红字报错并退出码 1。"""
    print("\n=== AC2 缺失配置文件 ===")
    with tempfile.TemporaryDirectory() as tmp:
        code = run_in(Path(tmp))
    print(f"[exit_code] {code}")
    assert code == 1, f"期望退出码 1，实际 {code}"
    print("✓ AC2 PASSED")


def test_ac3_非法配置() -> None:
    """AC3: default 指向不存在供应商应红字报错并退出码 1。"""
    print("\n=== AC3 非法配置（default 指向不存在的供应商）===")
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "mewcode.yaml"
        cfg_path.write_text(
            """
default: ghost
providers:
  alpha:
    protocol: anthropic
    model: m
    base_url: https://example.com
    api_key: sk-test
""",
            encoding="utf-8",
        )
        code = run_in(Path(tmp))
    print(f"[exit_code] {code}")
    assert code == 1, f"期望退出码 1，实际 {code}"
    print("✓ AC3 PASSED")


def test_ac4_apikey不回显() -> None:
    """AC4: 错误信息中不应回显 api_key 值。"""
    print("\n=== AC4 api_key 不回显 ===")
    secret = "sk-LEAK-TEST-VALUE-XYZ"
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "mewcode.yaml"
        # 写一份 protocol 非法的配置，触发字段校验失败
        cfg_path.write_text(
            f"""
default: alpha
providers:
  alpha:
    protocol: gemini
    model: m
    base_url: https://example.com
    api_key: {secret}
""",
            encoding="utf-8",
        )
        # 把 stderr/stdout 截到字符串，搜 secret
        from io import StringIO

        buf = StringIO()
        saved_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = run_in(Path(tmp))
        finally:
            sys.stdout = saved_stdout
    output = buf.getvalue()
    print(output, end="")
    print(f"[exit_code] {code}")
    assert code == 1
    assert secret not in output, f"！！api_key 出现在输出中：{secret}"
    print("✓ AC4 PASSED")


if __name__ == "__main__":
    test_ac2_缺失配置文件()
    test_ac3_非法配置()
    test_ac4_apikey不回显()
    print("\n所有配置错误场景通过 ✓")
