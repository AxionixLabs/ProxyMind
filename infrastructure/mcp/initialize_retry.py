# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ssl

import httpx

from infrastructure.mcp.errors import flatten_exceptions

HTTP_INITIALIZE_RETRY_DELAYS = (0.25, 1.0)
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def is_retryable_initialize_error(error: BaseException) -> bool:
    """只接受明确的临时握手故障；协议、认证、证书和混合异常组均不重试。"""
    errors = tuple(flatten_exceptions(error))
    return bool(errors) and all(_retryable_leaf(item) for item in errors)


def _retryable_leaf(error: BaseException) -> bool:
    """按异常类型和状态码分类，不解析可能带秘密的错误字符串。"""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in _RETRYABLE_STATUS_CODES
    if not isinstance(error, (httpx.NetworkError, httpx.TimeoutException)) or isinstance(error, httpx.CloseError):
        return False
    cause = error.__cause__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, ssl.SSLError):
            return False
        seen.add(id(cause))
        cause = cause.__cause__
    return True


if __name__ == '__main__':
    pass
