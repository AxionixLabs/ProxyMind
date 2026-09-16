# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import shutil
import sys
from urllib.parse import urlsplit


async def open_browser_url(url: str) -> bool:
    """交给系统浏览器打开 HTTP 地址，启动失败返回 False，由调用方保留手动路径。"""
    if urlsplit(url).scheme not in ("http", "https"):
        return False
    if sys.platform == "win32":
        try:
            os.startfile(url)
            return True
        except OSError:
            return False
    executable = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
    if executable is None:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            executable, url, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        async with asyncio.timeout(5):
            return await process.wait() == 0
    except TimeoutError:
        return False
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


if __name__ == '__main__':
    pass
