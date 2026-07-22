# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from loguru import logger
from engine.errors import MindError
from mind_app.mcp.config import load_mcp_servers_file
from mind_app.mcp.group import (
    ExternalMcpGroup,
    open_optional_external_mcp_group,
)
from mind_app.mcp.status import ExternalMcpStatus

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class ExternalMcpRuntime(object):
    """管理 Mind 生命周期内的外部 MCP 连接和状态。"""

    def __init__(self, mind: "Mind") -> None:
        """绑定 Mind 实例，并初始化外部 MCP 运行时状态。"""
        self._mind = mind

        self._group: typing.Optional[ExternalMcpGroup] = None

        self._context: typing.Any = None
        self._started: bool       = False

        self._lifecycle_lock = asyncio.Lock()

    @property
    def group(self) -> typing.Optional[ExternalMcpGroup]:
        """返回已建立的外部 MCP group；不可用时返回 None。"""
        return self._group

    @property
    def started(self) -> bool:
        """返回外部 MCP 启动流程是否已执行过。"""
        return self._started

    async def start(self, *, include_disabled: bool = False) -> None:
        """读取外部 MCP 配置并启动一次生命周期级连接。"""
        async with self._lifecycle_lock:
            await self._start_unlocked(include_disabled=include_disabled)

    async def _start_unlocked(self, *, include_disabled: bool = False) -> None:
        """在生命周期锁内启动外部 MCP。"""
        if self._started:
            return None

        servers = load_mcp_servers_file(self._mind.src_opera_place)

        if include_disabled:
            servers = [
                {
                    **server,
                    "enabled": True
                }
                for server in servers
            ]
        if servers:
            logger.debug(f"[MCP] external configured count={len(servers)}")

        status = ExternalMcpStatus(servers)

        external_anim_started: bool = False
        self._started = True

        try:
            if status.visible:
                await self._mind.start_external_mcp_anim(status.snapshot)
                external_anim_started = True

            self._context = open_optional_external_mcp_group(servers, status=status)
            self._group = await self._context.__aenter__()
            if self._group is None:
                await self._stop_unlocked()
        except BaseException as exc:
            await self._stop_unlocked()
            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit, MindError),
            ):
                raise
            logger.debug(f"[MCP] external runtime skipped {type(exc).__name__}: {exc}")
            self._group = None
        finally:
            if external_anim_started:
                await self._mind.await_cleanup(self._mind.stop_anim("external_mcp"))

    async def stop(self) -> None:
        """关闭已建立的外部 MCP 连接，并清空运行时状态。"""
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        """在生命周期锁内关闭外部 MCP。"""
        context = self._context

        self._context = None
        self._group   = None
        self._started = False

        if context is not None:
            await context.__aexit__(None, None, None)

    async def restart(self, *, include_disabled: bool = False) -> None:
        """重新读取配置并刷新外部 MCP 连接。"""
        async with self._lifecycle_lock:
            await self._stop_unlocked()
            await self._start_unlocked(include_disabled=include_disabled)


if __name__ == '__main__':
    pass
