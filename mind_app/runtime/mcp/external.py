# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger
from engine.tinker import MindError
from mind_app.mcp import (
    ExternalMcpGroup,
    ExternalMcpStatus,
    load_mcp_servers_file,
    open_optional_external_mcp_group
)

if typing.TYPE_CHECKING:
    from mind_app.mind_core import Mind


class ExternalMcpRuntime(object):
    """管理 Mind 生命周期内的外部 MCP 连接和状态。"""

    def __init__(self, mind: "Mind") -> None:
        """绑定 Mind 实例，并初始化外部 MCP 运行时状态。"""
        self._mind = mind
        self._context: typing.Any = None
        self._group: typing.Optional[ExternalMcpGroup] = None
        self._started = False

    @property
    def group(self) -> typing.Optional[ExternalMcpGroup]:
        """返回已建立的外部 MCP group；不可用时返回 None。"""
        return self._group

    @property
    def started(self) -> bool:
        """返回外部 MCP 启动流程是否已执行过。"""
        return self._started

    async def start(self) -> None:
        """读取外部 MCP 配置并启动一次生命周期级连接。"""
        if self._started:
            return None

        self._started = True
        servers = load_mcp_servers_file(self._mind.src_opera_place)
        if servers:
            logger.debug(f"[MCP] external configured count={len(servers)}")

        status = ExternalMcpStatus(servers)

        external_anim_started = False
        if status.visible:
            await self._mind.start_external_mcp_anim(status.snapshot)
            external_anim_started = True

        try:
            self._context = open_optional_external_mcp_group(servers, status=status)
            self._group = await self._context.__aenter__()
        except BaseException as exc:
            await self.stop()
            if isinstance(exc, (KeyboardInterrupt, SystemExit, MindError)):
                raise
            logger.debug(f"[MCP] external runtime skipped {type(exc).__name__}: {exc}")
            self._group = None
        finally:
            if external_anim_started:
                await self._mind.await_cleanup(self._mind.stop_anim())

    async def stop(self) -> None:
        """关闭已建立的外部 MCP 连接，并清空运行时状态。"""
        context = self._context

        self._context = None
        self._group   = None

        if context is not None:
            await context.__aexit__(None, None, None)

    async def restart(self) -> None:
        """重新读取配置并刷新外部 MCP 连接。"""
        await self.stop()
        self._started = False
        await self.start()


if __name__ == '__main__':
    pass
