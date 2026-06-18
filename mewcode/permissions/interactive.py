"""人在回路 UI（spec F7 / D5 / D13）。

当 PermissionPolicy.check 返回 Decision(action="ask") 时，由 chat.engine
调 PermissionAsker.ask 询问用户：

    ● Bash rm -rf node_modules
    未匹配规则，是否允许？
      y - 仅本次
      s - 本会话
      a - 永久（写入 permissions.local.yaml）
      n - 拒绝
    请选择 [y/s/a/N]:

输入处理：
- y / yes        → "once"   仅本次允许
- s              → "session" 本会话允许（chat.engine 调 policy.add_session_allow）
- a              → "forever" 永久（写入 .mewcode/permissions.local.yaml）
- n / 回车 / EOF  → "deny"   拒绝
- Ctrl+C         → 抛 ConfirmCancelled（继承第二阶段语义，整个 turn 取消）

注：本类用 print 直接输出（不依赖 Renderer），符合 spec D13——
permissions/ 子模块不应 import mewcode.render。
"""

import sys
from pathlib import Path

import yaml
from prompt_toolkit import PromptSession

from mewcode.permissions.rules import format_rule_for_display
from mewcode.tools.confirmer import ConfirmCancelled


class PermissionAsker:
    """人在回路询问器。

    懒构造 PromptSession——避免在单测/无终端环境下 import 即失败。
    """

    def __init__(self) -> None:
        self._pt: PromptSession | None = None

    def _session(self) -> PromptSession:
        if self._pt is None:
            self._pt = PromptSession()
        return self._pt

    async def ask(self, tool_name: str, target: str, cwd: Path) -> str:
        """问 y / s / a / N 四选。

        Args:
            tool_name: 内部小写工具名。
            target:    要批准的目标字符串（命令或路径）。
            cwd:       项目根目录（写 local YAML 用）。

        Returns:
            "once" / "session" / "forever" / "deny"

        Raises:
            ConfirmCancelled: 用户按 Ctrl+C
        """
        # 显示工具调用与询问
        verb_line = self._format_call(tool_name, target)
        sys.stdout.write(f"\n● {verb_line}\n")
        sys.stdout.write("未匹配规则，是否允许？\n")
        sys.stdout.write("  y - 仅本次\n")
        sys.stdout.write("  s - 本会话\n")
        sys.stdout.write("  a - 永久（写入 permissions.local.yaml）\n")
        sys.stdout.write("  n - 拒绝\n")
        sys.stdout.flush()

        try:
            answer = await self._session().prompt_async("请选择 [y/s/a/N]: ")
        except KeyboardInterrupt as e:
            raise ConfirmCancelled() from e
        except EOFError:
            return "deny"

        ans = answer.strip().lower()
        if ans in ("y", "yes"):
            return "once"
        if ans == "s":
            return "session"
        if ans == "a":
            try:
                self._write_to_local_yaml(tool_name, target, cwd)
            except OSError as e:
                sys.stdout.write(f"⚠️ 写入本地规则文件失败：{e}\n")
                sys.stdout.flush()
                # 写盘失败时降级为 session 级，至少本会话有效
                return "session"
            return "forever"
        # n / 回车 / 任何其他输入 → 拒绝
        return "deny"

    def _format_call(self, tool_name: str, target: str) -> str:
        """构造动词式描述行，与 Renderer.on_agent_event 风格一致。"""
        verb_map = {
            "run": f"Bash {target}",
            "read": f"Read {target}",
            "write": f"Wrote {target}",
            "edit": f"Edit {target}",
            "glob": f"Glob {target}",
            "search": f"Search {target}",
        }
        return verb_map.get(tool_name, f"{tool_name} {target}")

    def _write_to_local_yaml(
        self, tool_name: str, target: str, cwd: Path
    ) -> None:
        """把规则追加到 .mewcode/permissions.local.yaml 的 allow 列表。

        - 文件不存在 → 创建（含父目录）
        - YAML 解析失败 → 视为空 dict，覆盖写入
        - 已存在同名规则 → 不重复添加
        """
        path = cwd / ".mewcode" / "permissions.local.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        # 加载现有内容
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    data = {}
            except (yaml.YAMLError, OSError):
                data = {}
        else:
            data = {}

        # 确保 allow 是列表
        if not isinstance(data.get("allow"), list):
            data["allow"] = []

        rule_str = format_rule_for_display(tool_name, target)
        if rule_str not in data["allow"]:
            data["allow"].append(rule_str)

        # 写回（保持 utf-8 中文友好 + 不排序键，保留写入顺序）
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
