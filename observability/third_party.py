# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import logging
import contextlib
from . import observe

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


__all__ = ["route_session_termination_warnings"]
