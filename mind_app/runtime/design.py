# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_nova.modes import RunMode


class TerminalDesign(typing.Protocol):
    """描述非 TUI 终端动画使用的设计能力。"""

    async def download_animation(
        self,
        state: dict[str, typing.Any],
        stop_event: asyncio.Event,
    ) -> None:
        """展示下载进度。"""
        ...

    async def stream_mode_live(
        self,
        stop_event: asyncio.Event,
        mode: RunMode,
    ) -> None:
        """展示模式等待状态。"""
        ...

    async def upload_progress_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """展示附件上传状态。"""
        ...

    async def inbuild_startup_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """展示内置运行时启动状态。"""
        ...

    async def external_mcp_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """展示外部 MCP 启动状态。"""
        ...

    async def agent_connect_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], typing.Any],
    ) -> None:
        """展示 Agent 建连状态。"""
        ...

    async def agent_wait_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], typing.Any],
    ) -> None:
        """展示 Agent 等待状态。"""
        ...


if __name__ == '__main__':
    pass
