# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import random
import typing
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from backend.utilities.storage.logs import ensure_log_path
from backend.utilities import const


class _HelixBaseError(BaseException):
    """_HelixBaseError class."""
    pass


class HelixError(_HelixBaseError):
    """HelixError class."""

    def __init__(self, msg: typing.Any):
        """记录运行时错误消息。"""
        self.msg = msg

    def __str__(self):
        """把内部错误消息格式化成统一错误文本。"""
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    console: Console = Console()

    class _RichSink(RichHandler):

        # 电青，霓虹绿，霓虹粉
        debug_color = [
            "#00E5FF", "#39FF14", "#FF2D95"
        ]
        # 电黄，亮绿，蓝灰
        info_color = [
            "#FFD300", "#7CFF6B", "#64748B"
        ]
        level_style = {
            "DEBUG"    : f"bold {random.choice(debug_color)}",
            "INFO"     : f"bold {random.choice(info_color)}",
            "WARNING"  : "bold #FFD700",
            "ERROR"    : "bold #FF4500",
            "CRITICAL" : "bold #FF1493"
        }

        def __init__(self, console: "Console"):
            """初始化 Rich 日志输出通道。"""
            super().__init__(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                markup=False
            )

        def emit(self, record: "LogRecord") -> None:
            """把日志记录渲染成带颜色的控制台输出。"""
            self.console.print(
                const.PRINT_HEAD, Text(self.format(record), style=self.level_style.get(
                    record.levelname, "bold #ADD8E6"
                ))
            )

    @staticmethod
    def active(log_level: str) -> None:
        """配置控制台与文件双通道日志输出。"""
        logger.remove()
        logger.add(
            Active._RichSink(Active.console), level=log_level, format=const.PRINT_FORMAT
        )
        logger.add(
            ensure_log_path(),
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
            encoding=const.CHARSET,
            rotation="10 MB",
            retention="14 days",
            compression="zip"
        )


if __name__ == '__main__':
    pass
