# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from loguru import logger
from mind_nova import const


class Active(object):
    """Active class."""

    @staticmethod
    def silent() -> None:
        """移除当前进程已注册的日志输出接收器。"""
        logger.remove()

    @staticmethod
    def active(
        log_level: str,
        *,
        console: typing.Any = None,
        stderr: bool = False
    ) -> None:
        """使用指定控制台激活应用日志输出。"""
        Active.silent()

        if console is None and not stderr:
            return None

        from rich.console import Console
        from rich.logging import (
            LogRecord,
            RichHandler
        )
        from rich.text import Text

        class RichSink(RichHandler):
            debug_color = [
                "#00E5FF",
                "#39FF14",
                "#FF2D95",
            ]
            info_color = [
                "#FFD300",
                "#7CFF6B",
                "#64748B",
            ]
            level_style = {
                "DEBUG"    : f"bold {random.choice(debug_color)}",
                "INFO"     : f"bold {random.choice(info_color)}",
                "WARNING"  : "bold #FFD700",
                "ERROR"    : "bold #FF4500",
                "CRITICAL" : "bold #FF1493",
            }

            def __init__(self, sink_console: Console) -> None:
                super().__init__(
                    console=sink_console,
                    rich_tracebacks=True,
                    show_path=False,
                    show_time=False,
                    markup=False
                )

            def emit(self, record: LogRecord) -> None:
                self.console.print(
                    const.PRINT_HEAD,
                    Text(
                        self.format(record),
                        style=self.level_style.get(
                            record.levelname,
                            "bold #ADD8E6",
                        ),
                    ),
                )

        active_console = Console(stderr=True) if stderr else (console or Console())
        logger.add(
            RichSink(active_console),
            level=log_level,
            format=const.PRINT_FORMAT,
        )


class Tooling(object):

    @staticmethod
    def summarize_tool_arguments(tool_name: str, tool_args: typing.Any) -> str:

        def short_text(raw_value: typing.Any, limit: int = 48) -> str:
            text = str(raw_value).replace("\n", " ").strip()
            return text if len(text) <= limit else f"{text[:limit - 3]}..."

        def short_value(raw_value: typing.Any) -> str:
            if isinstance(raw_value, str):
                return short_text(raw_value)

            if isinstance(raw_value, bool):
                return "true" if raw_value else "false"

            if raw_value is None:
                return "null"

            if isinstance(raw_value, (int, float)):
                return str(raw_value)

            if isinstance(raw_value, list):
                return f"[{len(raw_value)} items]"

            if isinstance(raw_value, dict):
                keys   = list(raw_value.keys())
                head   = ", ".join(map(str, keys[:3]))
                suffix = "" if len(keys) <= 3 else f", +{len(keys) - 3}"

                return f"{{{head}{suffix}}}"

            return short_text(raw_value)

        if not isinstance(tool_args, dict):
            summary = short_text(tool_args, 120)
            return f"{tool_name}: {summary}" if tool_name else summary

        parts: list[str] = []
        for key, value in list(tool_args.items())[:4]:
            parts.append(f"{key}={short_value(value)}")

        if len(tool_args) > 4:
            parts.append(f"+{len(tool_args) - 4} fields")

        summary = ", ".join(parts)
        return f"{tool_name}: {summary}" if tool_name else summary


if __name__ == '__main__':
    pass
