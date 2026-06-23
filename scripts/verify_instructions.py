"""项目指令文件加载端到端验证（spec AC15）。

不依赖真实 LLM，纯粹验证：
1. 三层 AGENTS.md 加载 + H3 标题拼接
2. build_system_prompt 注入 custom_instructions 段
3. reload 内容比对（hash）
"""

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 把项目根加入 sys.path，确保从临时 cwd 启动也能 import mewcode
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mewcode.instructions import InstructionsLoader  # noqa: E402
from mewcode.system_prompt import build_system_prompt  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("[1] 临时项目级 AGENTS.md 写入...")
        agents_path = tmp / "AGENTS.md"
        agents_path.write_text(
            "本项目用 Python 3.13 + asyncio。\n"
            "不要用 pydantic，所有数据类用 stdlib dataclass。\n",
            encoding="utf-8",
        )

        print("[2] InstructionsLoader.load_all...")
        loader = InstructionsLoader(tmp)
        text = loader.load_all()
        assert text is not None, "load_all 应返回非 None"
        assert "### 项目规则" in text, "应含 H3 标题"
        assert "Python 3.13" in text, "应含原文内容"
        assert "应当严格遵守" in text, "应含 framing"
        print(f"    text 长度: {len(text)} 字符")
        print(f"    含 H3 标题: ✓")

        print("[3] build_system_prompt 注入...")
        sys_prompt = build_system_prompt(
            cwd=tmp,
            tools=["read", "glob", "search", "write", "edit", "run"],
            custom_instructions=text,
        )
        assert "## 自定义指令" in sys_prompt, "system prompt 应含 ## 自定义指令"
        assert "Python 3.13" in sys_prompt, "system prompt 应含原文"
        assert "### 项目规则" in sys_prompt, "system prompt 应含 H3 标题"
        # 顺序验证：## 当前环境 之后才出现 ## 自定义指令
        pos_env = sys_prompt.index("## 当前环境")
        pos_custom = sys_prompt.index("## 自定义指令")
        assert pos_env < pos_custom, "## 自定义指令 应在 ## 当前环境 之后"
        print(f"    sys_prompt 长度: {len(sys_prompt)} 字符")
        print("    含 ## 自定义指令: ✓")

        print("[4] reload_and_check（内容未变）...")
        changed, _ = loader.reload_and_check()
        assert changed is False, "未改文件 → changed 应为 False"
        print("    changed=False ✓")

        print("[5] 改文件后 reload_and_check...")
        agents_path.write_text(
            "本项目用 Python 3.14 + trio。\n",
            encoding="utf-8",
        )
        changed, new_text = loader.reload_and_check()
        assert changed is True, "改文件 → changed 应为 True"
        assert new_text is not None and "trio" in new_text
        print("    changed=True ✓")
        print("    新内容含 trio ✓")

        print("[6] 多层拼接（用户级 + 项目级）...")
        # 模拟用户级目录
        home_mock = tmp / "fake_home"
        (home_mock / ".mewcode").mkdir(parents=True)
        (home_mock / ".mewcode" / "AGENTS.md").write_text(
            "我偏好简洁回答\n", encoding="utf-8"
        )

        # 临时 monkeypatch Path.home
        import unittest.mock

        with unittest.mock.patch.object(
            Path, "home", classmethod(lambda cls: home_mock)
        ):
            loader2 = InstructionsLoader(tmp)
            text2 = loader2.load_all()
            assert text2 is not None
            assert "### 用户全局规则" in text2
            assert "### 项目规则" in text2
            # 第九阶段 F1：项目级排在用户级前面（高优先级在前）
            assert text2.index("项目规则") < text2.index("用户全局规则")
            print("    项目级 → 用户级 顺序 ✓")

    print("\n✓ 项目指令端到端通过")


if __name__ == "__main__":
    main()
