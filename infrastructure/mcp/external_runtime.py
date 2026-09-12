# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
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
    McpServicesBusy,
)
from agent.ports.tool_runtime import ExternalToolGroupPort
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
        self._accepting = True
        self._users: dict[str, int] = {}
        self._blocked: set[str] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    def _is_active(self) -> bool:
        """校验此实例仍可接收使用范围和建连结果。"""
        return self._accepting and self._context.config.workspace.resolve() == self._workspace

    def retire(self) -> None:
        """同步禁止新使用范围及迟到发布，已冻结引用继续由原消费者归还。"""
        self._accepting = False

    @contextmanager
    def use_tools(self, server: str | None = None) -> Iterator[ExternalToolGroupPort | None]:
        """原子冻结目录与引用；整个使用范围结束后才解除连接占用。"""
        group = self._group
        if not self._is_active() or group is None:
            yield None
            return
        keys = frozenset(
            item.config_key for item in group.service_snapshots
            if item.state == "ready" and item.config_key not in self._blocked
            and (server is None or item.tool_prefix == f"mcp__{server.strip()}__")
        )
        view = group.freeze_tools(keys)
        for key in keys:
            self._users[key] = self._users.get(key, 0) + 1
        if keys:
            self._idle.clear()
        try:
            yield view
        finally:
            view.release()
            for key in keys:
                count = self._users[key] - 1
                if count:
                    self._users[key] = count
                else:
                    del self._users[key]
            if not self._users:
                self._idle.set()

    @contextmanager
    def _block_services(self, keys: frozenset[str]) -> Iterator[None]:
        """不让出执行权地检查全部目标并禁止新引用，禁止只关闭未占用的部分。"""
        busy = tuple(sorted(key for key in keys if self._users.get(key, 0)))
        if busy:
            raise McpServicesBusy(busy)
        newly_blocked = keys - self._blocked
        self._blocked.update(newly_blocked)
        try:
            yield
        finally:
            self._blocked.difference_update(newly_blocked)

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
        if request.action == "status":
            key = request.target.config_key
            if not self._is_active() or request.runtime_id != self._runtime_id or request.workspace != str(self._workspace):
                return McpServiceOutcome(key, "failed", None, "MCP runtime or workspace is no longer active")
            try:
                self._load_servers()
            except (ValueError, OSError, AppError) as error:
                snapshot = next((item for item in self._snapshots() if item.config_key == key), None)
                return McpServiceOutcome(key, "failed", snapshot, external_status_detail_from_exception(error))
            snapshot = next((item for item in self._snapshots() if item.config_key == key), None)
            if snapshot is None:
                return McpServiceOutcome(key, "failed", None, "MCP service target no longer exists")
            return McpServiceOutcome(key, "unchanged", snapshot)
        async with self._lifecycle_lock:
            key = request.target.config_key
            if (
                not self._is_active()
                or request.runtime_id != self._runtime_id
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
                group = ExternalMcpGroup(can_publish=self._is_active)
                self._group = group
            try:
                if server is not None and request.action in ("start", "force", "restart"):
                    prefix = f"mcp__{server['name']}__"
                    if any(item.config_key != key and item.tool_prefix == prefix and item.config_key in group.owned_keys for item in group.service_snapshots):
                        raise ValueError("MCP tool prefix conflicts with an existing service")
                with self._block_services(frozenset({key})):
                    if request.action in ("stop", "restart") or key in group.owned_keys:
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
            except McpServicesBusy as error:
                return McpServiceOutcome(key, "busy", self._service_snapshot(key), str(error))
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
        """读取完整配置并补齐缺失连接，保持已有连接身份不变。"""
        async with self._lifecycle_lock:
            if not self._is_active():
                raise RuntimeError("MCP runtime or workspace is no longer active")
            await self._start_unlocked(
                self._load_servers(),
                include_disabled=include_disabled,
                defer_activity_stop=defer_activity_stop,
            )

    async def _start_unlocked(
        self,
        servers: list[NormalizedMcpServer],
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """校验补启动目标，在批次执行期间阻止目标产生新引用。"""
        ready = {item.config_key for item in self._snapshots() if item.state == "ready"}
        servers = [server for server in servers if server["config_key"] not in ready and (include_disabled or server["enabled"])]
        group = self._group
        if group is not None:
            for server in servers:
                if any(
                    item.config_key != server["config_key"]
                    and item.tool_prefix == f"mcp__{server['name']}__"
                    and item.config_key in group.owned_keys
                    for item in group.service_snapshots
                ):
                    raise ValueError("MCP tool prefix conflicts with an existing service")
        if not servers:
            return
        if include_disabled:
            servers = [
                {
                    **server,
                    "enabled": True
                }
                for server in servers
            ]
        with self._block_services(frozenset(server["config_key"] for server in servers)):
            await self._start_batch(servers, defer_activity_stop=defer_activity_stop)

    async def _start_batch(
        self, servers: list[NormalizedMcpServer], *, defer_activity_stop: bool,
    ) -> None:
        """沿用启动活动展示，由连接组负责本批发布及失败回收。"""
        self._last_start_snapshot = {}
        observe(
            "external_mcp.start",
            configured=len(servers),
        )

        status: ExternalMcpStatus = ExternalMcpStatus(servers)
        external_anim_started: bool = False
        group: ExternalMcpGroup | None = None

        try:
            if status.visible:
                await self._context.start_activity(status.snapshot)
                external_anim_started = True

            group = self._group
            if group is None:
                group = ExternalMcpGroup(can_publish=self._is_active)
                self._group = group
            await group.start(servers, status=status)

        except BaseException as exc:
            status.finish_unresolved(external_status_detail_from_exception(exc))

            observe(
                "external_mcp.start.failed",
                level="WARNING",
                error=external_status_detail_from_exception(exc),
            )
            raise
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
        """最终释放先拒绝新引用，再等待使用范围归还并收束连接。"""
        self.retire()
        await self._context.await_cleanup(self._finish_stop())

    async def _finish_stop(self) -> None:
        """在取消清理保护内等待消费者与已有管理动作完成。"""
        async with self._lifecycle_lock:
            await self._idle.wait()
            await self._stop_unlocked()

    async def stop_services(self) -> None:
        """全量交互停止在任何拆除前检查全部引用，不以最终释放绕过门禁。"""
        async with self._lifecycle_lock:
            if not self._is_active():
                raise RuntimeError("MCP runtime or workspace is no longer active")
            keys = frozenset(self._users) | (self._group.owned_keys if self._group is not None else frozenset())
            with self._block_services(keys):
                try:
                    await self._stop_unlocked()
                except asyncio.CancelledError:
                    if self._group is not None:
                        raise

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
        defer_activity_stop: bool = False
    ) -> None:
        """重新读取配置并刷新外部 MCP 连接。"""
        observe("external_mcp.restart")
        async with self._lifecycle_lock:
            if not self._is_active():
                raise RuntimeError("MCP runtime or workspace is no longer active")
            servers = self._load_servers()
            keys = frozenset(self._users) | (self._group.owned_keys if self._group is not None else frozenset())
            with self._block_services(keys):
                await self._stop_unlocked()
                await self._start_unlocked(servers, defer_activity_stop=defer_activity_stop)


if __name__ == '__main__':
    pass
