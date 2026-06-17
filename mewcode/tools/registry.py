"""工具注册中心。

spec F2 / F8：集中登记所有可用工具，提供：
- 按名查找（get / __getitem__）
- 列出全部（all / __iter__）
- 按目标协议格式批量序列化（to_anthropic_format / to_openai_format）

新增工具的所需改动（spec N7 / AC4）：
1. 在 mewcode/tools/ 下新建工具文件，实现 Tool 子类
2. 在本文件 register_builtins() 中追加一行 registry.register(NewTool())

REPL、chat、Provider、render、commands 模块的代码均无需修改。
"""

from collections.abc import Iterator

from mewcode.tools.base import Tool


class ToolRegistry:
    """按 name 索引的 Tool 集合。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ---------- 注册与查找 ----------

    def register(self, tool: Tool) -> None:
        """注册一个 Tool 实例。同名覆盖（最后注册的生效）。"""
        if not tool.name:
            raise ValueError(
                f"Tool 实例 {type(tool).__name__} 的 name 为空，无法注册"
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名查找；不存在返回 None（区别于 __getitem__ 抛 KeyError）。"""
        return self._tools.get(name)

    def __getitem__(self, name: str) -> Tool:
        """按名查找；不存在抛 KeyError。"""
        return self._tools[name]

    def __iter__(self) -> Iterator[Tool]:
        """按注册顺序迭代所有 Tool 实例。"""
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[Tool]:
        """快照：当前已注册的全部 Tool（按注册顺序）。"""
        return list(self._tools.values())

    # ---------- 协议格式序列化（spec F11 / AC3） ----------

    def to_anthropic_format(self) -> list[dict]:
        """把所有工具序列化为 Anthropic /v1/messages 的 tools 字段格式。

        每项：{"name": str, "description": str, "input_schema": dict}
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters_schema,
            }
            for t in self._tools.values()
        ]

    def to_anthropic_format_with_cache(self) -> list[dict]:
        """与 to_anthropic_format 行为相同，但最后一项含 cache_control。

        spec F4 / D2：Anthropic 协议的 tools 数组也支持 cache_control。
        把"breakpoint"加在最后一项上，标记到此位置以前的所有工具描述都
        进入 prompt cache。

        Anthropic 的 cache_control 限制最多 4 个 breakpoint；本方法只占
        用 1 个（tools 末尾），与 system 字段的 1 个共 2 个，仍有余量。

        Returns:
            带 cache_control 的工具列表；空列表时直接返回（不需要标记）。
        """
        items = self.to_anthropic_format()
        if items:
            items[-1] = {
                **items[-1],
                "cache_control": {"type": "ephemeral"},
            }
        return items

    def to_openai_format(self) -> list[dict]:
        """把所有工具序列化为 OpenAI /v1/chat/completions 的 tools 字段格式。

        每项：{"type": "function", "function": {"name", "description", "parameters"}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in self._tools.values()
        ]


def register_builtins(registry: ToolRegistry) -> None:
    """一次性注册全部 6 个内置工具。

    spec F8 扩展点：新增工具时只需在此函数内追加一行 registry.register(...)。
    import 在函数内部是为了避免 registry.py 顶部循环依赖具体工具实现
    （T16 任务的占位实现，T16 会启用真实工具类）。
    """
    # T10-T15 完成后启用：
    from mewcode.tools.read import ReadTool
    from mewcode.tools.write import WriteTool
    from mewcode.tools.edit import EditTool
    from mewcode.tools.run import RunTool
    from mewcode.tools.glob import GlobTool
    from mewcode.tools.search import SearchTool

    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    registry.register(RunTool())
    registry.register(GlobTool())
    registry.register(SearchTool())
