"""配置层异常体系。

启动阶段的配置错误是致命的：由入口层捕获后红字打印并以非 0 退出码结束
进程，不进入 REPL。错误类按"配置错误"的不同细分场景分类，便于呈现
精确的错误类别给用户。
"""


class ConfigError(Exception):
    """配置错误的基类。

    所有配置加载/校验过程中产生的错误都应继承此类。入口层通过
    `except ConfigError` 一并捕获并以退出码 1 结束。
    """

    category: str = "配置错误"


class ConfigFileNotFound(ConfigError):
    """指定的配置文件路径不存在。"""

    category = "配置文件不存在"


class ConfigFormatError(ConfigError):
    """YAML 解析失败，或顶层结构不是 dict。"""

    category = "配置格式错误"


class ConfigFieldError(ConfigError):
    """字段缺失、字段值非法、default 指向不存在的供应商等。

    错误信息中应明确指出问题字段名，便于用户直接修改配置。
    """

    category = "配置字段错误"
