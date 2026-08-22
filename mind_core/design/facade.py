# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from rich.console import Console
from .fx import download_animation as design_download_animation


class Design(object):
    """提供仍需 Rich 绘制的下载动画门面。"""

    def __init__(self, console: Console | None = None) -> None:
        """绑定下载动画使用的终端控制台。"""
        self.console = console or Console()

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event,
    ) -> None:
        """展示运行时资源下载进度。"""
        await design_download_animation(
            console=self.console,
            state=state,
            stop_event=stop_event,
        )


if __name__ == '__main__':
    pass
