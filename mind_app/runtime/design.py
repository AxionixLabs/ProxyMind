# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio


class TerminalDesign(typing.Protocol):
    """描述非 TUI 下载动画使用的设计能力。"""

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event,
    ) -> None:
        """展示下载进度。"""
        ...

if __name__ == '__main__':
    pass
