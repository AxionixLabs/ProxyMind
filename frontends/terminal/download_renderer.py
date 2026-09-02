# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from rich.console import Console

from infrastructure.platform.animation import AsyncAnimManager
from .contracts import TerminalDesign
from .download import download_animation


class TerminalDownloadProgress(object):
    """把终端下载设计适配为升级进度端口。"""

    def __init__(self, anim_manager: AsyncAnimManager, design: TerminalDesign) -> None:
        """绑定动画管理器和终端下载设计。"""
        self.anim_manager = anim_manager
        self.design = design

    async def start(self, state: dict[str, typing.Any]) -> None:
        """启动终端下载进度。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.download_animation(state, stop_event)
        )

    async def stop(self) -> None:
        """停止终端下载进度。"""
        await self.anim_manager.stop()


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
