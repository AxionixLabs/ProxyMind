#  _____ _       _
# |_   _(_)_ __ | | _____ _ __
#   | | | | '_ \| |/ / _ \ '__|
#   | | | | | | |   <  __/ |
#   |_| |_|_| |_|_|\_\___|_|
#

import sys
import json
import random
import shutil
import typing
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from engine.terminal import Terminal
from mindcore.design import Design
from mindnova import const


class _MindBaseError(BaseException):
    """_MindBaseError class."""
    pass


class MindError(_MindBaseError):
    """MindError class."""

    def __init__(self, msg: typing.Any):
        self.msg = msg

    def __str__(self):
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    class _RichSink(RichHandler):
        debug_color = [
            "#00CED1",  # 深青色 - 冷静理性
            "#98FB98",  # 浅绿色 - 绿色无压调试层
            "#B0C4DE",  # 灰蓝色 - 安静辅助信息
        ]
        info_color = [
            "#D8BFD8",  # 藕荷紫 - 精致低饱和
            "#EEE8AA",  # 浅卡其 - 稳妥类日志色
            "#F0FFF0",  # 蜜瓜白 - 极淡提示背景色
        ]
        level_style = {
            "DEBUG"    : f"bold {random.choice(debug_color)}",
            "INFO"     : f"bold {random.choice(info_color)}",
            "WARNING"  : "bold #FFD700",
            "ERROR"    : "bold #FF4500",
            "CRITICAL" : "bold #FF1493",
        }

        def __init__(self, console: "Console"):
            super().__init__(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                markup=False
            )

        def emit(self, record: "LogRecord") -> None:
            self.console.print(
                const.PRINT_HEAD, Text(self.format(record), style=self.level_style.get(
                    record.levelname, "bold #ADD8E6"
                ))
            )

    @staticmethod
    def active(log_level: str) -> None:
        logger.remove()
        logger.add(
            Active._RichSink(Design.console), level=log_level, format=const.PRINT_FORMAT
        )


class FileAssist(object):
    """FileAssist class."""

    @staticmethod
    async def open(file: str) -> typing.Optional[str]:
        if sys.platform == "win32":
            cmd = ["notepad++"] if shutil.which("notepad++") else ["Notepad"]
        else:
            cmd = ["open", "-W", "-a", "TextEdit"]
        return await Terminal.cmd_line(cmd + [file])

    @staticmethod
    def read_json(file: str) -> dict:
        with open(file, "r", encoding=const.CHARSET) as f:
            return json.loads(f.read())

    @staticmethod
    def dump_json(src: str, dst: dict) -> None:
        with open(src, "w", encoding=const.CHARSET) as f:
            f.write(json.dumps(dst, indent=4, separators=(",", ":"), ensure_ascii=False))


if __name__ == '__main__':
    pass
