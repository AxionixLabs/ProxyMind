# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from rich.console import Console
from .download import download_animation


class TerminalDownloadRenderer(object):
    """实现终端资源下载进度渲染端口。"""

    def __init__(self, console: Console | None = None) -> None:
        """绑定下载动画使用的终端控制台。"""
        self.console = console or Console()

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event,
    ) -> None:
        """展示运行时资源下载进度。"""
        await download_animation(
            console=self.console,
            state=state,
            stop_event=stop_event,
        )


if __name__ == '__main__':
    pass
