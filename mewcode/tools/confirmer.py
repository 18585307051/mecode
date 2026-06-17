"""用户确认对话器。

spec F13 / N11：write / edit / run 三类 DANGEROUS 工具执行前必须经过
用户确认。Confirmer 负责打印 "执行 <tool>？[y/N] " 并等待用户输入：
- 输入 y / yes（不区分大小写）→ 返回 True，执行
- 其他（含空回车 / n / 任何字符）→ 返回 False，拒绝
- Ctrl+D / EOFError → 返回 False（拒绝执行，但不取消整个 turn）
- Ctrl+C → 抛 ConfirmCancelled（区别于普通拒绝，chat 层据此回滚 R1
  assistant 入历史并跳过剩余 tool_use，整个 turn 视作"被用户取消"）

确认对话走 prompt_toolkit 的 PromptSession（与主输入框统一，避免 stdin
冲突），不在 Renderer 里——render 只负责"输出"，输入由 Confirmer 处理
（D9 / D10 决策）。
"""

from prompt_toolkit import PromptSession


class ConfirmCancelled(Exception):
    """用户在确认提示中按 Ctrl+C，表示要取消整个 turn。

    chat 层捕获此异常后：
    - 不执行当前 tool_use
    - 跳过剩余未执行的 tool_use
    - 不进入 Round 2
    - 回滚 R1 assistant 消息（避免协议层"孤儿 tool_use"）
    - 返回 False 给 REPL，回到主输入提示符
    """


class Confirmer:
    """用户 y/N 确认对话器。

    单次 mewcode 进程内复用同一个 PromptSession 实例，与主 REPL 的
    PromptSession 是不同实例但共享 stdin/stdout。
    """

    def __init__(self) -> None:
        # 懒构造：第一次调用 ask 时再创建 PromptSession，避免在
        # 单测/无终端环境下 import 即失败
        self._pt_session: PromptSession | None = None

    def _get_session(self) -> PromptSession:
        if self._pt_session is None:
            self._pt_session = PromptSession()
        return self._pt_session

    async def ask(self, tool_name: str) -> bool:
        """打印 "执行 <tool_name>？[y/N] " 并等待用户输入。

        Returns:
            True  —— 用户输入 y / yes（不区分大小写）
            False —— 其他输入（含空回车 / Ctrl+D）

        Raises:
            ConfirmCancelled —— 用户按 Ctrl+C
        """
        prompt_text = f"执行 {tool_name}？[y/N] "
        try:
            answer = await self._get_session().prompt_async(prompt_text)
        except KeyboardInterrupt as e:
            raise ConfirmCancelled() from e
        except EOFError:
            # Ctrl+D：拒绝但不取消整个 turn
            return False

        return answer.strip().lower() in ("y", "yes")
