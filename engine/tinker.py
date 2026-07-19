# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import json
import random
import shutil
import typing
import asyncio
import webbrowser
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from engine.terminal import Terminal
from mind_nova import const


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
            "#00E5FF",  # 电青
            "#39FF14",  # 霓虹绿
            "#FF2D95",  # 霓虹粉
        ]
        info_color = [
            "#FFD300",  # 电黄
            "#7CFF6B",  # 亮绿
            "#64748B",  # 蓝灰
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
    def active(
        log_level: str,
        *,
        console: Console | None = None,
        stderr: bool = False
    ) -> None:
        """使用指定控制台激活应用日志输出。"""
        logger.remove()
        active_console = Console(stderr=True) if stderr else (console or Console())
        logger.add(
            Active._RichSink(active_console),
            level=log_level,
            format=const.PRINT_FORMAT,
        )


class FileAssist(object):
    """FileAssist class."""

    CHROME_CANDIDATES_WIN = (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    )

    @staticmethod
    async def open(file: str) -> typing.Optional[str]:
        if sys.platform == "win32":
            cmd = ["notepad++"] if shutil.which("notepad++") else ["Notepad"]
        else:
            cmd = ["open", "-W", "-a", "TextEdit"]
        return await Terminal.cmd_line(cmd + [file])

    @staticmethod
    async def open_url(url: str) -> None:
        if sys.platform == "darwin":
            if os.path.exists("/Applications/Google Chrome.app"):
                await Terminal.cmd_link(["open", "-a", "Google Chrome", url])
                return None

        elif sys.platform == "win32":
            chrome = FileAssist._find_windows_chrome()
            if chrome:
                await Terminal.cmd_link([chrome, url])
                return None

        else:
            for chrome_cmd in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"):
                if shutil.which(chrome_cmd):
                    await Terminal.cmd_link([chrome_cmd, url])
                    return None

        await asyncio.to_thread(webbrowser.open, url)

    @staticmethod
    def _find_windows_chrome() -> typing.Optional[str]:
        chrome_exec = shutil.which("chrome") or shutil.which("chrome.exe")
        if chrome_exec:
            return chrome_exec

        for candidate in FileAssist.CHROME_CANDIDATES_WIN:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def read_json(file: str) -> dict:
        with open(file, "r", encoding=const.CHARSET) as f:
            return json.loads(f.read())

    @staticmethod
    def dump_json(src: str, dst: dict) -> None:
        with open(src, "w", encoding=const.CHARSET) as f:
            f.write(json.dumps(dst, indent=4, separators=(",", ":"), ensure_ascii=False))


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
                keys = list(raw_value.keys())
                head = ", ".join(map(str, keys[:3]))
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
