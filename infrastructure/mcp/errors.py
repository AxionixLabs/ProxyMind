# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from urllib.parse import (
    urlsplit,
    urlunsplit,
)

_EXTERNAL_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{24,}$")
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_CREDENTIAL_PATTERN = re.compile(
    r"\b(authorization|access[_-]?token|api[_-]?key|token|secret|password)"
    r"(\s*[:=]\s*)([^\s,;&]+)",
    re.IGNORECASE,
)


def flatten_exceptions(exc: BaseException) -> typing.Iterator[BaseException]:
    """展开异常组，便于按底层异常做判断。"""
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            yield from flatten_exceptions(item)
        return

    yield exc


def exception_type_name(exc: BaseException) -> str:
    """返回异常的模块限定名，用于识别可选依赖异常。"""
    return f"{type(exc).__module__}.{type(exc).__name__}"


def summarize_exception(exc: BaseException) -> str:
    """从异常组中挑一个经过脱敏的展示和日志摘要。"""
    for item in flatten_exceptions(exc):
        text = _redact_external_error_text(str(item).strip())
        if text:
            return f"{type(item).__name__}: {text}"

    return type(exc).__name__


def _redact_external_error_text(value: str) -> str:
    """隐藏外部传输异常中可能出现的 URL 和凭据值。"""
    text = _EXTERNAL_URL_PATTERN.sub(_redact_external_url_match, str(value))
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    return _CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )


def _redact_external_url_match(match: re.Match[str]) -> str:
    """把异常文本中的一个 HTTP URL 转换为可记录形式。"""
    raw = match.group(0)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<redacted-url>"

    path = "/".join(
        "<redacted>" if _SENSITIVE_PATH_COMPONENT.fullmatch(part) else part
        for part in parsed.path.split("/")
    )
    return urlunsplit((
        parsed.scheme,
        parsed.netloc.rsplit("@", 1)[-1],
        path,
        "<redacted>" if parsed.query else "",
        "<redacted>" if parsed.fragment else "",
    ))


def is_transport_close_exception(exc: BaseException) -> bool:
    """判断异常组是否只包含 MCP 关闭期可忽略的传输断开异常。"""
    transport_close_types = {
        "httpx.ReadError",
        "httpx.WriteError",
        "httpx.CloseError",
        "httpcore.ReadError",
        "httpcore.WriteError",
        "httpcore.CloseError",
        "anyio.EndOfStream",
        "anyio.BrokenResourceError",
        "anyio.ClosedResourceError"
    }
    items = list(flatten_exceptions(exc))
    if not items:
        return False

    return all(exception_type_name(item) in transport_close_types for item in items)


if __name__ == '__main__':
    pass
