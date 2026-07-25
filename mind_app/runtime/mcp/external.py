# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from engine.errors import ApplicationError
from mind_app.mcp.config import normalize_mcp_servers
from mind_app.mcp.group import (
    ExternalMcpGroup,
    open_optional_external_mcp_group
)
from mind_app.mcp.status import (
    ExternalMcpStatus,
    external_status_detail_from_exception
)
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class ExternalMcpRuntime(object):
    """管理应用生命周期内的外部 MCP 连接和状态。"""

    def __init__(self, mind: "Mind") -> None:
        """绑定主控制器，并初始化外部 MCP 运行时状态。"""
        self._mind = mind

        self._group: typing.Optional[ExternalMcpGroup] = None

        self._context: typing.Any = None
        self._started: bool       = False
        self._last_start_snapshot: dict[str, typing.Any] = {}

        self._lifecycle_lock = asyncio.Lock()

    @property
    def group(self) -> typing.Optional[ExternalMcpGroup]:
        """返回已建立的外部 MCP group；不可用时返回 None。"""
        return self._group

    @property
    def started(self) -> bool:
        """返回外部 MCP 启动流程是否已执行过。"""
        return self._started

    @property
    def last_start_snapshot(self) -> dict[str, typing.Any]:
        """返回最近一次外部 MCP 启动的最终状态。"""
        snapshot = self._last_start_snapshot
        if not snapshot:
            return {}
        return {
            **snapshot,
            "items": [
                dict(item)
                for item in list(snapshot.get("items") or [])
                if isinstance(item, dict)
            ],
        }

    async def start(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """读取外部 MCP 配置并启动一次生命周期级连接。"""
        async with self._lifecycle_lock:
            await self._start_unlocked(
                include_disabled=include_disabled,
                defer_activity_stop=defer_activity_stop,
            )

    async def _start_unlocked(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """在生命周期锁内启动外部 MCP。"""
        if self._started:
            observe("external_mcp.start.skipped", reason="already_started")
            return None

        self._last_start_snapshot = {}

        config  = self._mind.config_session.load()
        servers = normalize_mcp_servers(config.get("mcp_servers"))

        if include_disabled:
            servers = [
                {
                    **server,
                    "enabled": True
                }
                for server in servers
            ]
        observe(
            "external_mcp.start",
            configured=len(servers),
            include_disabled=include_disabled,
        )

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
            status.finish_unresolved(external_status_detail_from_exception(exc))
            await self._stop_unlocked()
            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit, ApplicationError),
            ):
                observe_exception("external_mcp.start.failed", exc)
                raise
            observe_exception("external_mcp.start.failed", exc, level="WARNING")
            self._group = None
        finally:
            if external_anim_started and not defer_activity_stop:
                await self._mind.await_cleanup(self._mind.stop_anim(
                    "external_mcp",
                    settle=False,
                ))
            self._last_start_snapshot = status.snapshot()
            items = list(self._last_start_snapshot.get("items") or [])
            states: dict[str, int] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                state = str(item.get("state") or "unknown")
                states[state] = states.get(state, 0) + 1
            observe(
                "external_mcp.start.complete",
                configured=len(servers),
                connected=self._group is not None,
                states=states,
            )

    async def stop(self) -> None:
        """关闭已建立的外部 MCP 连接，并清空运行时状态。"""
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        """在生命周期锁内关闭外部 MCP。"""
        context = self._context
        was_started = self._started

        self._context = None
        self._group   = None
        self._started = False

        if context is not None:
            await self._mind.await_cleanup(
                context.__aexit__(None, None, None)
            )
        if was_started or context is not None:
            observe("external_mcp.stopped")

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """重新读取配置并刷新外部 MCP 连接。"""
        observe("external_mcp.restart")
        async with self._lifecycle_lock:
            await self._stop_unlocked()
            await self._start_unlocked(
                include_disabled=include_disabled,
                defer_activity_stop=defer_activity_stop,
            )


if __name__ == '__main__':
    pass
