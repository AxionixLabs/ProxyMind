# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from agent.ports import (
    McpRuntimeContext,
    McpToolGroupSnapshot,
)
from agent.ports.mcp_runtime import (
    McpServiceControlRequest,
    McpServiceOutcome,
    McpServiceSnapshot,
)
from infrastructure.config.schema import validate_config
from infrastructure.errors import AppError
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.external_status import (
    ExternalMcpStatus,
    external_status_detail_from_exception,
)
from infrastructure.mcp.settings import (
    NormalizedMcpServer,
    normalize_mcp_servers,
)
from observability import observe


class ExternalMcpRuntime:
    """管理应用生命周期内的外部 MCP 连接和状态。"""

    def __init__(self, context: McpRuntimeContext) -> None:
        """绑定冻结的配置与生命周期回调。"""
        self._context = context
        self._group: typing.Optional[ExternalMcpGroup] = None
        self._runtime_id = str(uuid4())
        self._workspace = context.config.workspace.resolve()
        self._configured: dict[str, NormalizedMcpServer] = {}
        self._last_start_snapshot: dict[str, typing.Any] = {}
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

    @property
    def group(self) -> typing.Optional[ExternalMcpGroup]:
        """返回本实例持有的外部 MCP group，保留无可用连接时的失败事实。"""
        return self._group

    @property
    def started(self) -> bool:
        """返回外部 MCP 是否已经建立可用连接。"""
        return self._group is not None and self._group.started

    @property
    def runtime_id(self) -> str:
        """返回本实例不可复用的控制身份。"""
        return self._runtime_id

    @property
    def workspace(self) -> Path:
        """返回实例创建时冻结的工作区。"""
        return self._workspace

    def _load_servers(self) -> list[NormalizedMcpServer]:
        """校验完整服务配置并解析本工作区路径，失败不改变已持有连接。"""
        if self._context.config.workspace.resolve() != self._workspace:
            raise ValueError("MCP workspace is no longer active")
        config = self._context.config.load()
        raw = config.get("mcp_servers", {})
        validate_config({"mcp_servers": raw})
        servers = normalize_mcp_servers(raw)
        for server in servers:
            if server.get("transport") != "stdio":
                continue
            directory = Path(server.get("cwd") or ".").expanduser()
            cwd = (self._workspace / directory).resolve()
            server["cwd"] = str(cwd)
            command_path = Path(server.get("command", "")).expanduser()
            if command_path.parent != Path(".") or server.get("command", "").startswith(("./", ".\\")):
                server["command"] = str((cwd / command_path).resolve())
        self._configured = {server["config_key"]: server for server in servers}
        return servers

    def _snapshots(self) -> tuple[McpServiceSnapshot, ...]:
        """合并配置事实和连接事实，不由工具数量推断连接是否存在。"""
        group = self._group
        states = {item.config_key: item for item in group.service_snapshots} if group else {}
        for key, server in self._configured.items():
            current = states.get(key)
            states[key] = (
                replace(current, config_enabled=server["enabled"])
                if current is not None
                else McpServiceSnapshot(
                    key, f"mcp__{server['name']}__", server["enabled"], "stopped", server["transport"],
                )
            )
        for key in states.keys() - self._configured.keys():
            states[key] = replace(states[key], config_enabled=None)
        return tuple(states.values())

    @property
    def service_snapshots(self) -> tuple[McpServiceSnapshot, ...]:
        """刷新本地配置投影，不进行初始化或网络探测。"""
        self._load_servers()
        owned = self._group.owned_keys if self._group is not None else frozenset()
        return tuple(item for item in self._snapshots() if item.config_enabled is not None or item.config_key in owned)

    async def control_service(self, request: McpServiceControlRequest) -> McpServiceOutcome:
        """串行执行单服务生命周期动作，保留其他连接及原有启动活动展示。"""
        async with self._lifecycle_lock:
            key = request.target.config_key
            if (
                request.runtime_id != self._runtime_id
                or request.workspace != str(self._workspace)
                or self._context.config.workspace.resolve() != self._workspace
            ):
                return McpServiceOutcome(key, "failed", None, "MCP runtime or workspace is no longer active")
            try:
                self._load_servers()
            except (ValueError, OSError, AppError) as error:
                snapshot = next((item for item in self._snapshots() if item.config_key == key), None)
                if request.action != "stop" or snapshot is None:
                    return McpServiceOutcome(key, "failed", snapshot, external_status_detail_from_exception(error))
            snapshot = next((item for item in self._snapshots() if item.config_key == key), None)
            server = self._configured.get(key)
            if snapshot is None:
                return McpServiceOutcome(key, "failed", None, "MCP service target no longer exists")
            if request.action == "status":
                return McpServiceOutcome(key, "unchanged", snapshot)
            if request.action in ("start", "force", "restart") and server is None:
                return McpServiceOutcome(key, "failed", snapshot, "MCP service is missing from configuration")
            if request.action in ("start", "force") and snapshot.state == "ready":
                return McpServiceOutcome(key, "unchanged", snapshot)
            if request.action == "start" and snapshot.config_enabled is False:
                return McpServiceOutcome(key, "disabled", snapshot)
            if request.action == "stop" and snapshot.state == "stopped":
                return McpServiceOutcome(key, "unchanged", snapshot)
            group = self._group
            if group is None:
                group = ExternalMcpGroup()
                self._group = group
            try:
                if server is not None and request.action in ("start", "force", "restart"):
                    prefix = f"mcp__{server['name']}__"
                    if any(item.config_key != key and item.tool_prefix == prefix and item.config_key in group.owned_keys for item in group.service_snapshots):
                        raise ValueError("MCP tool prefix conflicts with an existing service")
                if request.action in ("stop", "restart"):
                    await group.stop_service(key)
                if request.action == "stop":
                    return McpServiceOutcome(key, "applied", self._service_snapshot(key))
                if server is None:
                    raise ValueError("MCP service is missing from configuration")
                if request.action == "restart" and not server["enabled"]:
                    return McpServiceOutcome(key, "disabled", self._service_snapshot(key))
                effective = server.copy()
                effective["enabled"] = True
                status = ExternalMcpStatus([effective])
                try:
                    await self._context.start_activity(status.snapshot)
                    connected = await group.start_service(effective, status)
                finally:
                    status.finish_unresolved()
                    self._last_start_snapshot = status.snapshot()
                    await self._context.await_cleanup(self._context.stop_activity("external_mcp", settle=False))
                snapshot = self._service_snapshot(key)
                return McpServiceOutcome(
                    key, "applied" if connected else "failed", snapshot,
                    None if connected else snapshot.connection_error,
                )
            except asyncio.CancelledError:
                snapshot = self._service_snapshot(key)
                if request.action == "stop" and snapshot.state == "stopped":
                    return McpServiceOutcome(key, "applied", snapshot)
                raise
            except (ValueError, RuntimeError, OSError, AppError) as error:
                return McpServiceOutcome(key, "failed", self._service_snapshot(key), external_status_detail_from_exception(error))

    def _service_snapshot(self, key: str) -> McpServiceSnapshot:
        """取得本次已校验目标的最新不可变事实。"""
        return next(item for item in self._snapshots() if item.config_key == key)

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
        if self.started:
            observe("external_mcp.start.skipped", reason="already_started")
            return None

        self._last_start_snapshot = {}

        servers = self._load_servers()
        if self._group is not None:
            await self._stop_unlocked()

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
            self._group = group
            await group.start(servers, status=status)

        except BaseException as exc:
            status.finish_unresolved(external_status_detail_from_exception(exc))

            if group is not None:
                await self._context.await_cleanup(group.close())

            self._group = None

            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit, AppError),
            ):
                observe(
                    "external_mcp.start.failed",
                    level="ERROR",
                    error=external_status_detail_from_exception(exc),
                )
                raise
            observe(
                "external_mcp.start.failed",
                level="WARNING",
                error=external_status_detail_from_exception(exc),
            )
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
                connected=self.started,
                states=states,
            )

    async def stop(self) -> None:
        """关闭已建立的外部 MCP 连接，并清空运行时状态。"""
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        """在生命周期锁内关闭外部 MCP。"""
        group = self._group
        if group is not None:
            await self._context.await_cleanup(self._release_group(group))

        if group is not None:
            observe("external_mcp.stopped")

    async def _release_group(self, group: ExternalMcpGroup) -> None:
        """释放成功后再解除持有；失败时保留资源，供最终关闭重试。"""
        await group.close()
        if self._group is group:
            self._group = None

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
