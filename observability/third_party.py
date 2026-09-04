# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import logging
import typing

from . import observe

__all__ = [
    "route_session_termination_warnings",
    "route_stdio_client_logs",
]

_STDIO_LOGGER_NAME = "mcp.client.stdio"
_STREAMABLE_HTTP_LOGGER_NAME = "mcp.client.streamable_http"
_SESSION_TERMINATION_WARNING = "Session termination failed:"


class _SessionTerminationLogFilter(logging.Filter):
    """把 SDK 已处理的会话关闭失败转入诊断日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤关闭期 warning，并保留其它 SDK 日志。"""
        message = record.getMessage()
        if not message.startswith(_SESSION_TERMINATION_WARNING):
            return True
        observe(
            "external_mcp.cleanup.warning",
            level="WARNING",
            detail=message,
        )
        return False


class _StdioClientLogFilter(logging.Filter):
    """阻止 MCP stdio SDK 日志绕过交互前端写入终端。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """把 SDK 日志转换为不携带外部正文的结构化诊断事件。"""
        observe(
            "external_mcp.stdio.sdk_log",
            level="WARNING",
            sdk_level=record.levelname,
        )
        return False


@contextlib.contextmanager
def route_stdio_client_logs() -> typing.Iterator[None]:
    """在 stdio 会话期间接管 SDK 日志，避免破坏终端渲染。"""
    sdk_logger = logging.getLogger(_STDIO_LOGGER_NAME)
    log_filter = _StdioClientLogFilter()
    sdk_logger.addFilter(log_filter)
    try:
        yield
    finally:
        sdk_logger.removeFilter(log_filter)


@contextlib.contextmanager
def route_session_termination_warnings() -> typing.Iterator[None]:
    """在外接会话关闭期间临时接管 SDK 关闭 warning。"""
    sdk_logger = logging.getLogger(_STREAMABLE_HTTP_LOGGER_NAME)
    log_filter = _SessionTerminationLogFilter()
    sdk_logger.addFilter(log_filter)
    try:
        yield
    finally:
        sdk_logger.removeFilter(log_filter)


if __name__ == '__main__':
    pass
