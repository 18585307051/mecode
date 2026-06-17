"""噪声目录常量与判定函数。

spec F8 / F9：glob 与 search 工具递归遍历 cwd 时自动排除常见的"无意义
噪声目录"——它们里面是构建产物或缓存，模型几乎不会真想搜进去，但
会带来巨大的性能开销与 token 浪费。

噪声目录列表写死在常量中（spec 的"不做的事"明确：不支持自定义忽略
规则）。新增条目时直接在 NOISE_DIRS 加一行；带通配的（如 *.egg-info）
在 has_noise_part 中特判。
"""

from pathlib import Path

# 路径段精确匹配的噪声目录名集合（区分大小写）
NOISE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".ruff_cache",
        ".idea",
        ".vscode",
    }
)


def has_noise_part(p: Path, base: Path) -> bool:
    """判断 p 相对 base 的任一路径段是否是噪声目录。

    Args:
        p:    被检查的路径（通常是 base.rglob() 产出的某个文件）。
        base: 参考基准（通常是 sandbox.cwd）。

    Returns:
        True：p 的任一段在 NOISE_DIRS 中，或匹配 *.egg-info 模式；
        False：所有段都不属于噪声目录。

    边界处理：
        - 若 p 不是 base 的子路径，relative_to 抛 ValueError，本函数
          返回 True（视为越界即噪声，调用方应在此前用 sandbox 校验）。
    """
    try:
        rel = p.relative_to(base)
    except ValueError:
        return True

    for part in rel.parts:
        if part in NOISE_DIRS:
            return True
        # *.egg-info 之类带通配的目录
        if part.endswith(".egg-info"):
            return True
    return False
