"""权限策略：综合判定五层防御链（spec F1 / F12）。

五层优先级（从高到低）：
1. 黑名单（不可绕过；仅 run 工具）
2. 沙箱（路径越界；本类不直接处理，由调用方先调 sandbox）
3. 会话级 deny → 4. 会话级 allow
5. 本地级 deny → 6. 本地级 allow （三层 YAML 已在 loader 拼接为 config.deny/allow）
7-10. 项目级 / 用户级 deny / allow（同上）
11. 权限模式（yolo / default / strict）
12. ask 用户

返回 Decision 给调用方（chat.engine），由调用方决定如何处理 ask。
"""

from dataclasses import dataclass
from pathlib import Path

from mewcode.permissions.blocklist import match_blocklist
from mewcode.permissions.loader import PermissionConfig, load_all
from mewcode.permissions.rules import (
    Rule,
    extract_match_target,
    format_rule_for_display,
    parse_rule,
)


@dataclass(frozen=True)
class Decision:
    """权限决策结果。

    Attributes:
        action:         "allow" / "deny" / "ask"
        reason:         中文说明，用于 ToolResult.text 或 UI 显示
        error_category: 拒绝时的错误类别（"黑名单拦截" / "权限拒绝"）；
                        allow / ask 时为 None
    """

    action: str
    reason: str
    error_category: str | None = None


class PermissionPolicy:
    """权限策略综合判定。

    线程模型：单线程异步使用，与 chat.engine 同生命周期。
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._config: PermissionConfig = load_all(cwd)
        # 会话级（运行时 /permissions allow|deny 与 ask 的 s 选项添加）
        self._session_allow: list[Rule] = []
        self._session_deny: list[Rule] = []
        # /permissions mode 临时覆盖
        self._mode_override: str | None = None

    # ---------- 状态访问 ----------

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def mode(self) -> str:
        """当前生效的权限模式。"""
        return self._mode_override or self._config.mode

    @property
    def config_mode(self) -> str:
        """三层 YAML 合并后的 mode（不含 override）。"""
        return self._config.mode

    @property
    def all_allow(self) -> list[Rule]:
        """完整 allow 规则列表（会话级 + 文件级，会话在前）。"""
        return self._session_allow + self._config.allow

    @property
    def all_deny(self) -> list[Rule]:
        """完整 deny 规则列表（会话级 + 文件级，会话在前）。"""
        return self._session_deny + self._config.deny

    @property
    def session_allow(self) -> list[Rule]:
        return self._session_allow

    @property
    def session_deny(self) -> list[Rule]:
        return self._session_deny

    # ---------- 修改状态 ----------

    def reload(self) -> None:
        """重新加载三层 YAML，并清空所有会话级状态（spec F9 reload 子命令）。"""
        self._config = load_all(self._cwd)
        self._session_allow.clear()
        self._session_deny.clear()
        self._mode_override = None

    def add_session_allow(self, rule: Rule) -> None:
        """添加一条会话级 allow 规则（spec F9）。"""
        self._session_allow.append(rule)

    def add_session_deny(self, rule: Rule) -> None:
        """添加一条会话级 deny 规则（spec F9）。"""
        self._session_deny.append(rule)

    def add_session_allow_for(self, tool_name: str, target: str) -> None:
        """便捷方法：根据 tool+target 构造 allow 规则并添加。

        spec F7：人在回路 s 选项后调用此方法。
        """
        raw = format_rule_for_display(tool_name, target)
        rule = parse_rule(raw)
        if rule is not None:
            self._session_allow.append(rule)

    def set_mode_override(self, mode: str) -> None:
        """临时覆盖 mode（spec F9 mode 子命令）。"""
        if mode not in ("strict", "default", "yolo"):
            raise ValueError(f"未知模式：{mode!r}（应为 strict/default/yolo）")
        self._mode_override = mode

    def clear_mode_override(self) -> None:
        self._mode_override = None

    # ---------- 主入口 ----------

    def check(self, tool_name: str, params: dict) -> Decision:
        """五层防御主入口（spec F1）。

        Args:
            tool_name: 内部小写工具名（run/read/write/edit/glob/search）。
            params:    工具的 input 字典。

        Returns:
            Decision——调用方按 action 处理：
            - allow → 直接执行
            - deny  → 返回 ToolResult(success=False, ...)
            - ask   → 调 PermissionAsker 询问用户
        """
        # Layer 1: 黑名单（仅 run 工具，不可绕过）
        if tool_name == "run":
            command = params.get("command", "")
            hit = match_blocklist(command)
            if hit:
                return Decision(
                    action="deny",
                    reason=(
                        f"黑名单拦截：命令匹配高危模式（{hit!r}）。"
                        "此层不可通过权限规则或 yolo 模式放行——"
                        "请使用更精确的命令路径，避免影响系统关键文件。"
                    ),
                    error_category="黑名单拦截",
                )

        # Layer 2: 沙箱由调用方处理（chat.engine 在调 policy 之前
        # 不主动预检，让工具自己用 sandbox.resolve 拦截路径越界）

        # Layer 3-10: 规则匹配
        target = extract_match_target(tool_name, params)
        if target is not None:
            # 会话级 deny > 会话级 allow > 文件级 deny > 文件级 allow
            for rule in self._session_deny:
                if rule.matches(tool_name, target):
                    return self._build_deny(tool_name, target, "会话级 deny 命中")
            for rule in self._session_allow:
                if rule.matches(tool_name, target):
                    return Decision("allow", reason=f"会话级 allow 命中 ({rule.raw})")

            for rule in self._config.deny:
                if rule.matches(tool_name, target):
                    return self._build_deny(tool_name, target, f"deny 规则命中 ({rule.raw})")
            for rule in self._config.allow:
                if rule.matches(tool_name, target):
                    return Decision("allow", reason=f"allow 规则命中 ({rule.raw})")

        # Layer 11: 权限模式
        mode = self.mode
        if mode == "yolo":
            return Decision("allow", reason="yolo 模式放行")

        # Layer 12: ask 用户（default 与 strict 都进入 ask；UI 区别由
        # 调用方按 mode 处理——本阶段 default 与 strict 行为相同，简化
        # 实现）
        return Decision("ask", reason="未匹配规则")

    def _build_deny(
        self, tool_name: str, target: str, reason_prefix: str
    ) -> Decision:
        """构造 deny Decision，含给模型的引导文字。"""
        rule_str = format_rule_for_display(tool_name, target)
        return Decision(
            action="deny",
            reason=(
                f"权限拒绝：{rule_str} {reason_prefix}。"
                f"如需允许此操作，请告诉用户运行 "
                f"`/permissions allow \"{rule_str}\"` 添加规则，"
                f"或临时切换到 yolo 模式（不推荐用于生产环境）。"
            ),
            error_category="权限拒绝",
        )
