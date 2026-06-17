"""工作目录沙盒。

spec F10 / N10：所有路径相关工具（read / write / edit / glob / search）
都必须通过 Sandbox.resolve(raw_path) 做统一前置校验：
- 解析为绝对路径（兼容相对路径与绝对路径输入）
- 校验落在启动时的 CWD 子树内（含本身）
- 越界（含 `..` 上溯、绝对路径指向 CWD 外）→ 抛 PathOutOfSandboxError

run 工具不调用 resolve，但子进程的工作目录强制为 sandbox.cwd
（用户输入的命令字符串可能含 cd 切换目录，那是命令本身的事，
不影响子进程启动时的 cwd）。
"""

from dataclasses import dataclass
from pathlib import Path

from mewcode.tools.errors import PathOutOfSandboxError


@dataclass(frozen=True)
class Sandbox:
    """工作目录沙盒。

    Attributes:
        cwd: 启动 mewcode 时的工作目录（main.py 用 Path.cwd() 构造）。
            运行期间不会变化（mewcode 进程不主动 chdir）。
    """

    cwd: Path

    def resolve(self, raw_path: str) -> Path:
        """把用户/模型输入的路径解析为绝对路径并校验沙盒边界。

        Args:
            raw_path: 路径字符串。可以是：
                - 相对路径（相对 cwd）："a.py"、"src/main.py"
                - 绝对路径："e:/.../mecode/README.md"
                - 含 `..` 上溯："src/../README.md"（只要解析后仍在 cwd 内即可）

        Returns:
            解析为绝对路径并 resolve 后的 Path 对象。

        Raises:
            PathOutOfSandboxError: 解析后的路径不在 cwd 子树内，
                错误信息包含原始 raw_path 与 cwd。
        """
        # 1) 把 raw_path 拼到 cwd（绝对路径会覆盖 cwd，相对路径相对 cwd）
        candidate = (self.cwd / raw_path).resolve(strict=False)
        cwd_resolved = self.cwd.resolve(strict=False)

        # 2) 校验 candidate 在 cwd_resolved 子树内
        # 用 try relative_to 做兜底校验，兼容 Python 3.10～3.13 的行为差异
        try:
            candidate.relative_to(cwd_resolved)
        except ValueError as e:
            raise PathOutOfSandboxError(
                f"路径越界：{raw_path} 不在工作目录 {self.cwd} 内"
            ) from e

        return candidate
