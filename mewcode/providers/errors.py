"""Provider 层异常体系。

所有从 Provider 抛出的、属于"预期内可发生"的错误都继承自 ProviderError。
chat.run_turn 通过 `except ProviderError` 一并捕获，由 Renderer 红字
打印类别和原因，然后回到 REPL 输入提示符；本阶段不做任何自动重试。
"""


class ProviderError(Exception):
    """Provider 层错误的基类。"""

    category: str = "Provider 错误"


class NetworkError(ProviderError):
    """网络层错误：连接被拒绝、超时、DNS 解析失败等。"""

    category = "网络错误"


class HTTPStatusError(ProviderError):
    """HTTP 响应状态码非 2xx。

    Attributes:
        status_code:  HTTP 状态码。
        body_snippet: 响应体片段（已截断到合理长度，且经过 api_key 脱敏处理）。
    """

    category = "HTTP 错误"

    def __init__(self, status_code: int, body_snippet: str) -> None:
        super().__init__(f"HTTP {status_code}: {body_snippet}")
        self.status_code = status_code
        self.body_snippet = body_snippet


class AuthError(HTTPStatusError):
    """鉴权失败（HTTP 401 / 403）。

    与 HTTPStatusError 区分开来，便于在终端展示更友好的"鉴权失败"分类。
    """

    category = "鉴权失败"


class StreamParseError(ProviderError):
    """SSE 帧或事件结构非预期：JSON 解析失败、字段类型不符等。"""

    category = "流解析错误"
