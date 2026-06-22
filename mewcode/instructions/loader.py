"""项目指令文件加载（spec F1-F7, F10, F11）。

三层文件按 用户→项目→本地 顺序加载，每层独立查找候选名（AGENTS.md →
CLAUDE.md → .mewcoderc），找到第一个就停。

错误容错：所有错误都不阻塞启动，只 warning 并跳过该层。

InstructionsLoader 既负责加载也持有"当前生效的指令文本"状态——
/instructions reload 时通过 hash 比对决定是否需要重建 system prompt。
"""

import hashlib
from pathlib import Path
from typing import NamedTuple

# spec F5：单文件 8KB 上限
_FILE_LIMIT_BYTES = 8 * 1024

# 候选文件名（按优先级查找，找到第一个就停）—— spec F1 / Q1 / Q11
_USER_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_PROJECT_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_LOCAL_CANDIDATES = ["AGENTS.local.md", "CLAUDE.local.md"]


class LayerInfo(NamedTuple):
    """一层加载的元信息。

    Attributes:
        name:         "用户级" / "项目级" / "本地级"
        path:         实际加载到的文件绝对路径
        display_path: 标题显示用的相对/友好路径（如 ./AGENTS.md）
        text:         文件内容（已 normalize：UTF-8 解码 + 截断 +
                      尾部加 "\\n"）
        bytes_len:    字节数（截断后），供横幅显示
    """
    name: str
    path: Path
    display_path: str
    text: str
    bytes_len: int


def _read_layer(
    dir_path: Path,
    candidates: list[str],
    layer_name: str,
    display_prefix: str,
) -> LayerInfo | None:
    """在某一层目录下查找候选文件并加载。

    Args:
        dir_path: 目录绝对路径
        candidates: 候选文件名（按优先级）
        layer_name: 中文层名（"用户级"/"项目级"/"本地级"）
        display_prefix: 标题显示路径前缀（如 "./" / "~/.mewcode/"）

    Returns:
        LayerInfo 或 None（该层无内容/出错跳过）
    """
    for name in candidates:
        path = dir_path / name
        if not path.exists():
            continue
        if not path.is_file():
            continue
        try:
            raw_bytes = path.read_bytes()
        except (PermissionError, OSError) as e:
            print(f"⚠️ 项目指令文件 {path} 读不了（已跳过）：{e}")
            return None

        truncated = False
        if len(raw_bytes) > _FILE_LIMIT_BYTES:
            raw_bytes = raw_bytes[:_FILE_LIMIT_BYTES]
            truncated = True
            print(f"⚠️ 项目指令文件 {path} 超过 8KB，已截断")

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            print(f"⚠️ 项目指令文件 {path} 非 UTF-8 编码（已跳过）")
            return None

        if truncated:
            text += "\n\n[... 内容已截断（超过 8KB 上限）...]\n"

        # 文本规范化：去掉尾部多余空白、保证结尾恰好一个换行
        normalized = text.rstrip() + "\n"
        return LayerInfo(
            name=layer_name,
            path=path,
            display_path=display_prefix + name,
            text=normalized,
            bytes_len=len(raw_bytes),
        )
    return None


# 拼接时的层标题映射（spec F3 / Q12）
_TITLE_MAP = {
    "用户级": "用户全局规则",
    "项目级": "项目规则",
    "本地级": "本地规则",
}

# 顶部 framing（spec D3）
_FRAMING_PREFIX = "以下是用户在项目中明确写出的工作规则，应当严格遵守：\n"


class InstructionsLoader:
    """项目指令加载器。

    生命周期：
    1. main.py 启动时构造一个实例
    2. 调 load_all() 拿到拼接结果，传给 build_system_prompt
    3. 把实例放进 CommandContext，供 /instructions show / reload 用
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._home = Path.home()
        self._last_text: str | None = None
        self._last_hash: str = ""
        self._last_layers: list[LayerInfo] = []

    def load_all(self) -> str | None:
        """加载三层文件，返回拼接后的 custom_instructions 字符串。

        spec F2 / F3：用户 → 项目 → 本地 顺序拼接，H3 标题包装来源。
        三层全空 → 返回 None。
        """
        layers: list[LayerInfo] = []
        plan = [
            ("用户级", self._home / ".mewcode", _USER_CANDIDATES, "~/.mewcode/"),
            ("项目级", self._cwd, _PROJECT_CANDIDATES, "./"),
            ("本地级", self._cwd / ".mewcode", _LOCAL_CANDIDATES, "./.mewcode/"),
        ]
        for layer_name, dir_path, candidates, prefix in plan:
            info = _read_layer(dir_path, candidates, layer_name, prefix)
            if info is not None:
                layers.append(info)

        self._last_layers = layers

        if not layers:
            self._last_text = None
            self._last_hash = ""
            return None

        # 拼接（spec F3）
        parts = [_FRAMING_PREFIX]
        for layer in layers:
            title = _TITLE_MAP[layer.name]
            parts.append(
                f"### {title}（来自 {layer.display_path}）\n{layer.text}"
            )
        text = "\n".join(parts)

        self._last_text = text
        self._last_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return text

    def current_text(self) -> str | None:
        """返回最近一次 load_all 的结果（不重新加载文件）。"""
        return self._last_text

    def current_hash(self) -> str:
        """返回最近一次内容的 SHA-256 hash。"""
        return self._last_hash

    def loaded_layers(self) -> list[LayerInfo]:
        """返回最近一次 load_all 加载到内容的层（供横幅 / show 用）。"""
        return list(self._last_layers)

    def reload_and_check(self) -> tuple[bool, str | None]:
        """重新加载文件，返回 (内容是否变化, 新文本)。

        spec F10 / D5：hash 比对决定是否需要重建 system_prompt。
        """
        old_hash = self._last_hash
        new_text = self.load_all()
        new_hash = self._last_hash
        return (old_hash != new_hash), new_text
