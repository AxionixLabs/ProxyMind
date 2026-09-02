# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing


class TerminalDesign(typing.Protocol):
    """定义入口资源升级所需的终端下载渲染端口。"""

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event,
    ) -> None:
        """展示下载进度并在停止事件触发后收敛。"""
        ...


if __name__ == "__main__":
    pass
