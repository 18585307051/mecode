"""工作目录沙盒。

spec F10 / N10（第二阶段）：所有路径相关工具都必须通过
Sandbox.resolve(raw_path) 做统一前置校验：
- 解析为绝对路径（兼容相对路径与绝对路径输入）
- 校验落在启动时的 CWD 子树内（含本身）
- 越界（含 `..` 上溯、绝对路径指向 CWD 外）→ 抛 PathOutOfSandboxError

第五阶段（spec F3 / Q9 / D2）新增 safe_open 上下文管理器：
- 在 open 之后立即 fstat（fd）+ lstat（路径），比对 inode + dev
- 不一致说明 open 后路径被换成 symlink → 抛 PathRaceConditionError

run 工具不调用 resolve，但子进程的工作目录强制为 sandbox.cwd。
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mewcode.tools.errors import PathOutOfSandboxError, PathRaceConditionError


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
        try:
            candidate.relative_to(cwd_resolved)
        except ValueError as e:
            raise PathOutOfSandboxError(
                f"路径越界：{raw_path} 不在工作目录 {self.cwd} 内"
            ) from e

        return candidate

    @contextmanager
    def safe_open(self, raw_path: str, mode: str = "r", encoding: str = "utf-8"):
        """以原子方式安全打开文件，防 TOCTOU（spec 第五阶段 F3）。

        步骤：
        1. resolve raw_path → 校验在 cwd 内（继承 self.resolve）
        2. open 文件（"r" / "w" / "rb" / "wb" 等）
        3. 对 fd 调 os.fstat → 取 (st_ino, st_dev)
        4. 对 resolved 路径调 os.lstat → 取 (st_ino, st_dev)
        5. 比对：不一致说明 open 后路径被换成了 symlink，抛
           PathRaceConditionError

        Args:
            raw_path: 路径（同 resolve）。
            mode:     open 模式，例如 "r" / "w" / "rb" / "wb"。
            encoding: 文本模式的编码（默认 utf-8）。binary 模式自动忽略。

        Yields:
            file 对象（with 语句自动 close）。

        Raises:
            PathOutOfSandboxError:  resolve 阶段越界
            PathRaceConditionError: TOCTOU 竞态被检测
            OSError:                I/O 失败（不是 race）
        """
        resolved = self.resolve(raw_path)

        # binary 模式不指定 encoding
        open_kwargs: dict = {} if "b" in mode else {"encoding": encoding}

        f = open(resolved, mode, **open_kwargs)
        try:
            # TOCTOU 检测：fd 与路径的 (inode, dev) 必须一致
            try:
                fd_stat = os.fstat(f.fileno())
                ln_stat = os.lstat(resolved)
                if (fd_stat.st_ino, fd_stat.st_dev) != (
                    ln_stat.st_ino,
                    ln_stat.st_dev,
                ):
                    raise PathRaceConditionError(
                        f"TOCTOU 竞态：文件 {raw_path} 在 open 后被换成符号链接"
                    )
            except OSError:
                # Windows 某些场景 fstat / lstat 可能 raise（例如某些
                # 设备文件、特殊路径）；这种宽容处理——不当作竞态。
                # 真正的 PathRaceConditionError 是上面的 raise。
                pass
            yield f
        finally:
            f.close()
