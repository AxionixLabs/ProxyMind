# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import json
import os
import shutil
import sys
import typing
import webbrowser

from infrastructure.platform.terminal import Terminal
from metadata import const


class FileAssist(object):
    """提供本地文件和网页的系统级打开能力。"""

    CHROME_CANDIDATES_WIN = (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    )

    @staticmethod
    async def open(file: str) -> str | None:
        """使用当前平台的文本编辑器打开文件。"""
        if sys.platform == "win32":
            command = ["notepad++"] if shutil.which("notepad++") else ["Notepad"]
        else:
            command = ["open", "-W", "-a", "TextEdit"]
        return await Terminal.cmd_line(command + [file])

    @staticmethod
    async def open_url(url: str) -> None:
        """优先使用 Chrome 打开网页，并回退到系统浏览器。"""
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
            for command in (
                    "google-chrome",
                    "google-chrome-stable",
                    "chrome",
                    "chromium",
                    "chromium-browser",
            ):
                if shutil.which(command):
                    await Terminal.cmd_link([command, url])
                    return None
        await asyncio.to_thread(webbrowser.open, url)

    @staticmethod
    def _find_windows_chrome() -> str | None:
        """返回 Windows 上可用的 Chrome 可执行文件。"""
        chrome_exec = shutil.which("chrome") or shutil.which("chrome.exe")
        if chrome_exec:
            return chrome_exec
        for candidate in FileAssist.CHROME_CANDIDATES_WIN:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def read_json(file: str) -> dict[str, typing.Any]:
        """读取 UTF-8 JSON 对象。"""
        with open(file, "r", encoding=const.CHARSET) as stream:
            return json.loads(stream.read())

    @staticmethod
    def dump_json(src: str, dst: dict[str, typing.Any]) -> None:
        """把对象写入 UTF-8 JSON 文件。"""
        with open(src, "w", encoding=const.CHARSET) as stream:
            stream.write(json.dumps(
                dst,
                indent=4,
                separators=(",", ":"),
                ensure_ascii=False,
            ))


if __name__ == '__main__':
    pass
