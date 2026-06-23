"""项目指令文件加载（spec F1-F7, F10, F11；第九阶段 F1/F2 扩展）。

三层文件按 本地 → 项目 → 用户 顺序加载（高优先级在前），每层独立查找
候选名（AGENTS.md → CLAUDE.md → .mewcoderc），找到第一个就停。

第九阶段新增：
- 优先级反转：本地级（项目内私有） > 项目共享级 > 用户全局级；
  靠前内容更易被模型重视，三层冲突时遵守靠前规则。
- 单文件支持 `@include <path>`：相对当前文件所在目录解析，受 8KB 限制，
  限制嵌套深度（≤3）、用 visited 集合防环路、拦截跳出 allowed_root 的
  路径（项目层只能引用项目内文件，用户层只能引用 ~/.mewcode 内文件）。

错误容错：所有错误都不阻塞启动，只 warning 并跳过。
"""

import hashlib
import re
from pathlib import Path
from typing import NamedTuple

# spec F5：单文件 8KB 上限（include 文件同样适用）
_FILE_LIMIT_BYTES = 8 * 1024

# 候选文件名（按优先级查找，找到第一个就停）—— spec F1 / Q1 / Q11
_USER_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_PROJECT_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".mewcoderc"]
_LOCAL_CANDIDATES = ["AGENTS.local.md", "CLAUDE.local.md"]

# 第九阶段 F2：@include 行级语法 + 嵌套深度限制
_INCLUDE_RE = re.compile(r"^@include\s+(.+?)\s*$")
_MAX_INCLUDE_DEPTH = 3


class LayerInfo(NamedTuple):
    """一层加载的元信息。

    Attributes:
        name:         "本地级" / "项目级" / "用户级"
        path:         实际加载到的文件绝对路径
        display_path: 标题显示用的相对/友好路径（如 ./AGENTS.md）
        text:         文件内容（已 normalize：UTF-8 解码 + 截断 + include
                      展开 + 尾部加 "\\n"）
        bytes_len:    主文件字节数（截断后），供横幅显示
    """
    name: str
    path: Path
    display_path: str
    text: str
    bytes_len: int


def _read_text_with_limit(path: Path) -> str | None:
    """读取单文件，UTF-8 解码 + 8KB 截断；错误 warning 后返回 None。

    用于主指令文件与 @include 引用文件，统一的读取入口。
    """
    if not path.is_file():
        return None
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

    return text


def _resolve_include_path(
    base_file: Path,
    raw: str,
    allowed_root: Path,
) -> Path | None:
    """解析 @include 路径（相对当前文件目录），并校验是否在 allowed_root 内。

    Args:
        base_file:    包含 @include 的当前文件绝对路径。
        raw:          @include 后的原始路径串（可能含相对路径）。
        allowed_root: 允许引用的根目录（项目层 = cwd；用户层 = ~/.mewcode）。

    Returns:
        合法时返回 resolve 后的绝对路径；越界 / 异常时返回 None。
    """
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        candidate = (base_file.parent / raw).resolve(strict=False)
        allowed = allowed_root.resolve(strict=False)
    except (OSError, ValueError) as e:
        print(f"⚠️ @include {raw} 路径解析失败（已跳过）：{e}")
        return None

    try:
        candidate.relative_to(allowed)
    except ValueError:
        print(
            f"⚠️ @include {raw} 越出允许目录 {allowed}（已跳过）"
        )
        return None

    return candidate


def _expand_includes(
    text: str,
    current_file: Path,
    allowed_root: Path,
    depth: int,
    visited: set[Path],
) -> str:
    """逐行展开 @include 指令。

    - 只识别独占一行的 `@include <path>`。
    - 深度超过 _MAX_INCLUDE_DEPTH 时跳过更深层 include 并 warning。
    - visited 用 resolve 后的绝对路径作 key，防止 A→B→A 这类环路。
    - 越界 / 文件错误 / 编码错误均 warning 后跳过，不阻塞主流程。
    - 展开内容用 begin/end include 注释包裹，便于在 system prompt 中
      区分原文与引用内容。
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue

        raw = m.group(1)

        if depth >= _MAX_INCLUDE_DEPTH:
            print(
                f"⚠️ @include {raw} 嵌套深度超过 {_MAX_INCLUDE_DEPTH}（已跳过）"
            )
            continue

        target = _resolve_include_path(current_file, raw, allowed_root)
        if target is None:
            continue

        if target in visited:
            print(f"⚠️ @include {raw} 形成环路（已跳过）")
            continue
        if not target.exists():
            print(f"⚠️ @include {raw} 指向的文件不存在（已跳过）")
            continue
        if not target.is_file():
            print(f"⚠️ @include {raw} 指向的不是文件（已跳过）")
            continue

        included_text = _read_text_with_limit(target)
        if included_text is None:
            continue

        # 标记已访问，递归展开嵌套 include
        new_visited = visited | {target}
        expanded = _expand_includes(
            included_text,
            current_file=target,
            allowed_root=allowed_root,
            depth=depth + 1,
            visited=new_visited,
        )

        try:
            display = target.relative_to(allowed_root).as_posix()
        except ValueError:
            display = target.name

        out_lines.append(f"<!-- begin include: {display} -->")
        out_lines.append(expanded.rstrip("\n"))
        out_lines.append(f"<!-- end include: {display} -->")

    return "\n".join(out_lines)


def _read_layer(
    dir_path: Path,
    candidates: list[str],
    layer_name: str,
    display_prefix: str,
    allowed_root: Path,
) -> LayerInfo | None:
    """在某一层目录下查找候选文件并加载（含 @include 展开）。

    Args:
        dir_path:       目录绝对路径
        candidates:     候选文件名（按优先级）
        layer_name:     中文层名（"用户级"/"项目级"/"本地级"）
        display_prefix: 标题显示路径前缀（如 "./" / "~/.mewcode/"）
        allowed_root:   该层允许 @include 引用的根目录

    Returns:
        LayerInfo 或 None（该层无内容/出错跳过）
    """
    for name in candidates:
        path = dir_path / name
        if not path.exists():
            continue
        if not path.is_file():
            continue
        text = _read_text_with_limit(path)
        if text is None:
            return None

        # 第九阶段 F2：展开 @include
        try:
            resolved = path.resolve(strict=False)
            visited = {resolved}
            expanded = _expand_includes(
                text,
                current_file=path,
                allowed_root=allowed_root,
                depth=0,
                visited=visited,
            )
        except Exception as e:
            print(f"⚠️ @include 展开失败 {path}（已使用原文）：{e}")
            expanded = text

        # 文本规范化：去掉尾部多余空白、保证结尾恰好一个换行
        normalized = expanded.rstrip() + "\n"

        # bytes_len 仍记录主文件原始字节数（不含 include 展开），
        # 横幅展示与第七阶段一致。
        try:
            bytes_len = len(path.read_bytes())
            if bytes_len > _FILE_LIMIT_BYTES:
                bytes_len = _FILE_LIMIT_BYTES
        except OSError:
            bytes_len = len(text.encode("utf-8"))

        return LayerInfo(
            name=layer_name,
            path=path,
            display_path=display_prefix + name,
            text=normalized,
            bytes_len=bytes_len,
        )
    return None


# 拼接时的层标题映射（spec F3 / Q12）
_TITLE_MAP = {
    "用户级": "用户全局规则",
    "项目级": "项目规则",
    "本地级": "本地规则",
}

# 顶部 framing：第九阶段 F1 强调高优先级在前
_FRAMING_PREFIX = (
    "以下是用户在项目中明确写出的工作规则，应当严格遵守。"
    "若多层规则冲突，请优先遵循靠前列出的规则：\n"
)


class InstructionsLoader:
    """项目指令加载器。

    生命周期：
    1. main.py 启动时构造一个实例
    2. 调 load_all() 拿到拼接结果，传给 build_system_prompt
    3. 把实例放进 CommandContext，供 /instructions show / reload 用

    第九阶段 F1：拼接顺序改为 本地 → 项目 → 用户（高优先级在前）。
    """

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._home = Path.home()
        self._user_root = self._home / ".mewcode"
        self._last_text: str | None = None
        self._last_hash: str = ""
        self._last_layers: list[LayerInfo] = []

    def load_all(self) -> str | None:
        """加载三层文件，返回拼接后的 custom_instructions 字符串。

        spec F2 / F3 + 第九阶段 F1：本地 → 项目 → 用户 顺序拼接，
        H3 标题包装来源。三层全空 → 返回 None。
        """
        layers: list[LayerInfo] = []

        # 第九阶段 F1：高优先级排在前面
        # allowed_root：项目级与本地级用 cwd；用户级用 ~/.mewcode
        plan = [
            (
                "本地级",
                self._cwd / ".mewcode",
                _LOCAL_CANDIDATES,
                "./.mewcode/",
                self._cwd,
            ),
            (
                "项目级",
                self._cwd,
                _PROJECT_CANDIDATES,
                "./",
                self._cwd,
            ),
            (
                "用户级",
                self._user_root,
                _USER_CANDIDATES,
                "~/.mewcode/",
                self._user_root,
            ),
        ]
        for layer_name, dir_path, candidates, prefix, allowed_root in plan:
            info = _read_layer(
                dir_path, candidates, layer_name, prefix, allowed_root
            )
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
