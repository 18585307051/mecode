"""tools 层异常体系。

所有 ToolError 子类都被工具自身在 execute() 内捕获并转换为
`ToolResult(success=False, error_category=<类别>, text=<描述>)`，
不会向上传播到 chat 层导致整个 turn 失败。

异常的 `category` 类属性用于 ToolResult.error_category 字段，再被
Renderer 在简略反馈中显示给用户（spec F19）。
"""


class ToolError(Exception):
    """工具层错误的基类。"""

    category: str = "工具错误"


class PathOutOfSandboxError(ToolError):
    """路径越界：解析后的绝对路径不在 Sandbox.cwd 子树内。

    触发场景：
    - 显式 `..` 上溯到父目录
    - 绝对路径指向 CWD 外的位置
    - glob 模式以 `/` 开头或含 `..`

    spec F10：所有路径相关工具必经 Sandbox.resolve 校验。
    """

    category = "路径越界"


class FileTooLargeError(ToolError):
    """文件过大：read 工具的 256KB 上限触发（spec F4 截断后通常不抛
    此异常，但保留作为某些极端场景的兜底）。"""

    category = "文件过大"


class FileDecodeError(ToolError):
    """文件解码失败：read 工具尝试 utf-8 读取二进制文件等。

    spec 不做文件 watcher 与多模态读取，read 仅支持 utf-8 文本文件。
    """

    category = "解码失败"


class EditNotFoundError(ToolError):
    """edit 工具的原文片段在文件中未找到（count == 0）。

    spec F6：返回结构化错误让模型据此重试。
    """

    category = "未找到匹配"


class EditAmbiguousError(ToolError):
    """edit 工具的原文片段在文件中匹配多次（count > 1）。

    spec F6 要求"匹配不到或匹配多次都给清楚的报错让模型重试"。
    """

    category = "匹配多次需更多上下文"


class CommandTimeoutError(ToolError):
    """run 工具子进程超出 60 秒未结束被强制 kill。"""

    category = "超时"


class ToolInterruptedError(ToolError):
    """用户中断：工具执行期间收到 Ctrl+C / CancelledError。

    chat 层捕获 KeyboardInterrupt 后会回滚 R1 assistant 入历史并跳过
    剩余 tool_use；本异常仅供工具内部表达"被取消"语义。
    """

    category = "用户中断"
