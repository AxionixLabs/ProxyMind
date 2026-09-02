# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections import defaultdict

from agent.ports import (
    McpRuntimeContext,
    McpToolGroupSnapshot,
)
from infrastructure.errors import AppError
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.external_status import (
    ExternalMcpStatus,
    external_status_detail_from_exception,
)
from infrastructure.mcp.settings import normalize_mcp_servers
from observability import (
    observe,
    observe_exception
)


class ExternalMcpRuntime(object):
    """管理应用生命周期内的外部 MCP 连接和状态。"""

    def __init__(self, context: McpRuntimeContext) -> None:
        """绑定冻结的配置与生命周期回调。"""
        self._context = context
        self._group: typing.Optional[ExternalMcpGroup] = None
        self._started: bool = False
        self._last_start_snapshot: dict[str, typing.Any] = {}
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    @property
    def group(self) -> typing.Optional[ExternalMcpGroup]:
        """返回已建立的外部 MCP group；不可用时返回 None。"""
        return self._group

    @property
    def started(self) -> bool:
        """返回外部 MCP 是否已经建立可用连接。"""
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

    @property
    def tool_groups(self) -> tuple[McpToolGroupSnapshot, ...]:
        """把 SDK 工具和连接统计投影为稳定的前端状态。"""
        group = self._group
        if group is None:
            return ()

        tools_by_group: dict[tuple[str, str], list[str]] = defaultdict(list)
        auth_by_group: dict[tuple[str, str], str] = {}
        for name, tool in group.tools.items():
            meta = dict(tool.meta or {})
            server = str(meta.get("server") or "external").strip() or "external"
            transport = (
                str(meta.get("transport") or "external").strip()
                or "external"
            )
            auth = str(meta.get("auth") or "Unknown").strip() or "Unknown"
            key = (server, transport)
            tools_by_group[key].append(str(name))
            auth_by_group.setdefault(key, auth)

        snapshots: list[McpToolGroupSnapshot] = []
        for stats in group.server_stats.values():
            server = str(stats.get("server") or "external")
            transport = str(stats.get("transport") or "external")
            key = (server, transport)
            names = tuple(sorted(tools_by_group.pop(key, ())))
            exposed = len(names)
            discovered = max(exposed, int(stats.get("discovered") or 0))
            snapshots.append(McpToolGroupSnapshot(
                server=server,
                transport=transport,
                auth=auth_by_group.pop(key, "Unknown"),
                tools=names,
                discovered=discovered,
                exposed=exposed,
                filtered=max(0, discovered - exposed),
            ))

        for (server, transport), raw_names in tools_by_group.items():
            names = tuple(sorted(raw_names))
            snapshots.append(McpToolGroupSnapshot(
                server=server,
                transport=transport,
                auth=auth_by_group.get((server, transport), "Unknown"),
                tools=names,
                discovered=len(names),
                exposed=len(names),
                filtered=0,
            ))

        return tuple(sorted(
            snapshots,
            key=lambda item: (item.server, item.transport),
        ))

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

        config = self._context.config.load()
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

        status: ExternalMcpStatus = ExternalMcpStatus(servers)
        external_anim_started: bool = False
        group: ExternalMcpGroup | None = None

        try:
            if status.visible:
                await self._context.start_activity(status.snapshot)
                external_anim_started = True

            group = ExternalMcpGroup()

            connected_servers = await group.start(servers, status=status)
            if connected_servers > 0:
                self._group = group
                self._started = True
            else:
                await self._context.await_cleanup(group.close())

        except BaseException as exc:
            status.finish_unresolved(external_status_detail_from_exception(exc))

            if group is not None:
                await self._context.await_cleanup(group.close())

            self._group = None
            self._started = False

            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit, AppError),
            ):
                observe_exception("external_mcp.start.failed", exc)
                raise
            observe_exception("external_mcp.start.failed", exc, level="WARNING")
        finally:
            if external_anim_started and not defer_activity_stop:
                await self._context.await_cleanup(self._context.stop_activity(
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
        group = self._group
        was_started = self._started
        self._group = None

        try:
            if group is not None:
                await self._context.await_cleanup(group.close())
        finally:
            self._started = False

        if was_started or group is not None:
            observe("external_mcp.stopped")

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False
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
