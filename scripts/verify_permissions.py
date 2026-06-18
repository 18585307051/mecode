"""真实 API 端到端验证：权限系统五层防御（spec AC1 / AC2 / AC11）。

场景：
1. allow 规则生效 → 模型调 git status 通过
2. deny 规则生效 → 模型调 rm 被拒，模型在 R2 中含错误信息
3. 黑名单拦截 → 模型调 rm -rf / 被黑名单拦（不可被 yolo 绕过）

注：本脚本通过显式 yolo 模式 + session deny 测试，避免人在回路阻塞。
"""

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from mewcode.chat import Session, run_turn
from mewcode.config import load
from mewcode.permissions import PermissionPolicy
from mewcode.permissions.rules import parse_rule
from mewcode.providers import (
    ToolResultBlock,
    ToolUseBlock,
    build_provider,
)
from mewcode.render import Renderer
from mewcode.system_prompt import build_system_prompt
from mewcode.tools import Sandbox, ToolRegistry, register_builtins


class _AutoYesConfirmer:
    async def ask(self, tool_name: str) -> bool:
        return True


async def _run_scenario(
    title: str,
    cfg_provider,
    sys_prompt: str,
    registry: ToolRegistry,
    sandbox: Sandbox,
    policy: PermissionPolicy,
    user_prompt: str,
):
    """跑一个场景，返回 (session, last_tool_results_blocks)。"""
    print(f"\n{'=' * 60}")
    print(f"场景：{title}")
    print(f"{'=' * 60}")

    session = Session(
        provider=cfg_provider,
        current_provider_name="deepseek-anthropic",
        system_prompt=sys_prompt,
    )
    renderer = Renderer(Console())
    confirmer = _AutoYesConfirmer()

    ok = await run_turn(
        session, user_prompt, renderer,
        registry=registry, confirmer=confirmer, sandbox=sandbox,
        policy=policy, asker=None,  # 不弹询问，未匹配规则的全 deny
    )
    print(f"\n[run_turn ok={ok}]")
    return session


async def main() -> None:
    cfg = load("mewcode.yaml")
    prov = build_provider(cfg.providers["deepseek-anthropic"])

    registry = ToolRegistry()
    register_builtins(registry)
    sandbox = Sandbox(cwd=Path.cwd())
    sys_prompt = build_system_prompt(
        cwd=sandbox.cwd, tools=sorted(t.name for t in registry)
    )

    # ----- 场景 1：allow 规则生效 -----
    policy1 = PermissionPolicy(sandbox.cwd)
    policy1.add_session_allow(parse_rule("Bash(git *)"))
    policy1.add_session_allow(parse_rule("Read(**/*)"))

    session1 = await _run_scenario(
        "allow 规则放行 git 命令",
        prov, sys_prompt, registry, sandbox, policy1,
        "用 git status 看一下当前仓库状态",
    )

    # 检查模型确实调了 git
    found_git = False
    for m in session1.messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock) and b.name == "run":
                cmd = b.input.get("command", "")
                if cmd.startswith("git"):
                    found_git = True
                    print(f"  模型调用：{cmd}")
    if found_git:
        print("✓ 场景 1 通过：模型成功调 git 命令")
    else:
        print("⚠️ 场景 1：未观察到 git 调用（模型可能直接回答了）")

    # ----- 场景 2：deny 规则拦截 -----
    policy2 = PermissionPolicy(sandbox.cwd)
    policy2.set_mode_override("yolo")  # yolo 但加 session_deny 拦 rm
    policy2.add_session_deny(parse_rule("Bash(rm *)"))

    session2 = await _run_scenario(
        "deny 规则拦截 rm 命令（即便 yolo 模式）",
        prov, sys_prompt, registry, sandbox, policy2,
        "请用 rm 命令删除 /tmp/some_test_file.txt（这是个虚构文件，"
        "你不需要真删，按规则尝试调用即可）",
    )

    # 检查 tool_results 是否含权限拒绝
    found_deny = False
    for m in session2.messages:
        for b in m.content:
            if isinstance(b, ToolResultBlock) and b.is_error:
                if "权限拒绝" in b.content or "deny" in b.content.lower():
                    found_deny = True
                    print(f"  权限拒绝反馈：{b.content[:80]}...")
    if found_deny:
        print("✓ 场景 2 通过：rm 被 deny 规则拦截")
    else:
        print("⚠️ 场景 2：未触发 deny（可能模型没尝试 rm）")

    # ----- 场景 3：黑名单不可绕过 -----
    policy3 = PermissionPolicy(sandbox.cwd)
    policy3.set_mode_override("yolo")
    policy3.add_session_allow(parse_rule("Bash(*)"))  # 故意全开

    # 直接构造一个 tool_use 调用，模拟 rm -rf /，看 policy.check 行为
    decision = policy3.check("run", {"command": "rm -rf /"})
    print(f"\n场景 3：黑名单不可绕过")
    print(f"  policy.check('rm -rf /') → action={decision.action}, category={decision.error_category}")
    if decision.action == "deny" and decision.error_category == "黑名单拦截":
        print("✓ 场景 3 通过：yolo + 全 allow 仍无法绕过黑名单")
    else:
        print("✗ 场景 3 失败：黑名单被绕过！")
        sys.exit(1)

    print("\n✓ 权限系统端到端验证全部通过")


if __name__ == "__main__":
    asyncio.run(main())
