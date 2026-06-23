"""斜杠命令 Tab 补全（spec 第十阶段 F7）。

仅在以下条件下产生候选：
- 当前行以 `/` 开头
- 光标位于第一个空格之前（即正在写命令名）
- 至少有 1 个字符的命令名 prefix（仅 `/` 不触发，避免全表噪音）

候选集 = 所有 hidden=False 的命令 name；别名不参与补全。

prompt_toolkit Completer 接口语义：
- 单匹配 + 按 Tab → 直接补全到完整命令名
- 多匹配 + 按 Tab → 弹下拉菜单（默认 UI）
- 空 prefix（输入仅 `/`）→ 不返回候选（用户想看全表请用 /help）
"""

from prompt_toolkit.completion import Completer, Completion

from mewcode.commands.registry import visible_command_names


class SlashCommandCompleter(Completer):
    """与 prompt_toolkit 集成的斜杠命令补全器。"""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        # 已进入参数区（例如 `/session resume xxx`）→ 不补全命令名
        if " " in text:
            return

        prefix = text[1:].lower()  # 去掉斜杠并归一化小写
        # spec F7：空 prefix 不返回候选，避免按 Tab 弹全表噪音
        if not prefix:
            return

        for name in visible_command_names():
            if name.lower().startswith(prefix):
                yield Completion(
                    name,
                    start_position=-len(prefix),
                    display=f"/{name}",
                )
