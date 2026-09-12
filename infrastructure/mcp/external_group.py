# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import functools
import os
import typing

import anyio
import httpx

from collections.abc import (
    Callable,
    Mapping,
)
from dataclasses import (
    dataclass,
    replace,
)
from datetime import timedelta
from types import MappingProxyType

from mcp import (
    ClientSession,
    types as mcp_types,
)
from mcp.client.session_group import (
    ClientSessionParameters,
    SseServerParameters,
    StreamableHttpParameters,
)
from mcp.client.sse import sse_client
from mcp.client.stdio import (
    StdioServerParameters,
    stdio_client
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.shared.session import ProgressFnT

from agent.ports.mcp_runtime import McpServiceSnapshot
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.errors import AppError
from infrastructure.mcp.external_status import (
    ExternalMcpStatus,
    external_status_detail_from_exception,
    should_reraise_external,
)
from infrastructure.mcp.settings import (
    NormalizedMcpServer,
    is_mcp_tool_allowed,
    normalize_mcp_approval_mode,
    request_timeout_sec,
    startup_timeout_sec,
)
from infrastructure.mcp.transport import (
    ObservedMcpReadStream,
    build_server_params,
    external_http_client,
    preflight_server,
)
from infrastructure.mcp.values import (
    slugify_mcp_name,
    tool_name_hook,
)
from observability import observe
from observability.third_party import (
    route_session_termination_warnings,
    route_stdio_client_logs,
)

EXTERNAL_MCP_CONNECT_CONCURRENCY = 2
EXTERNAL_MCP_STDIO_CONCURRENCY = 1
EXTERNAL_MCP_PREFLIGHT_TIMEOUT_SEC = 2.0
EXTERNAL_MCP_CLOSE_TIMEOUT_SEC = 5.0
EXTERNAL_MCP_TOOL_YIELD_INTERVAL = 32


@dataclass(frozen=True, slots=True)
class _ExternalMcpConnectionReady:
    """保存单个外部连接完成初始化后的可发布资源。"""
    alias: str
    session: ClientSession
    tools: dict[str, mcp_types.Tool]
    discovered_count: int
    disconnected: asyncio.Event


@dataclass(slots=True)
class _ExternalMcpConnection:
    """保存单个外部连接的所有者任务和关闭信号。"""
    server: str
    stop_event: asyncio.Event
    ready: asyncio.Future[_ExternalMcpConnectionReady]
    task: asyncio.Task[None]
    config_key: str
    cleaned: bool = False
    cleanup_error: str | None = None


class ExternalMcpGroup:
    """管理一组外部 MCP 会话，并把多个服务的工具合并成统一入口。"""

    def __init__(self, *, can_publish: Callable[[], bool] | None = None) -> None:
        """初始化外部 MCP 工具索引和连接所有者集合。"""
        self.tools: dict[str, mcp_types.Tool] = {}
        self.server_stats: dict[str, dict[str, typing.Any]] = {}
        self._tool_to_session: dict[str, ClientSession] = {}
        self._connections: list[_ExternalMcpConnection] = []
        self._closing: bool = False
        self._closed: bool = False
        self._close_lock: asyncio.Lock = asyncio.Lock()
        self._service_states: dict[str, McpServiceSnapshot] = {}
        self._staged_keys: set[str] = set()
        self._can_publish = can_publish

    @property
    def started(self) -> bool:
        """按连接事实判断可用性，合法的零工具连接同样算已启动。"""
        return any(item.state == "ready" for item in self._service_states.values())

    @property
    def service_snapshots(self) -> tuple[McpServiceSnapshot, ...]:
        """返回由本连接组拥有的不可变逐服务事实。"""
        return tuple(self._service_states.values())

    @property
    def owned_keys(self) -> frozenset[str]:
        """返回仍持有资源或待确认清理结果的服务键。"""
        return frozenset(item.config_key for item in self._connections)

    def freeze_tools(self, keys: frozenset[str]) -> "ExternalMcpTools":
        """复制已发布服务的目录；调用方必须在引用存续期间阻止相应连接重建。"""
        names = {
            name for key in keys for name in self._service_states[key].tools
        }
        return ExternalMcpTools(self, {name: tool.model_copy(deep=True) for name, tool in self.tools.items() if name in names})

    def _remember_server(self, server: NormalizedMcpServer) -> None:
        """在预检前建立连接状态，使没有进入 SDK 的失败同样可被观察。"""
        key = server.get("config_key", server["name"])
        self._service_states[key] = McpServiceSnapshot(
            config_key=key, tool_prefix=f"mcp__{server['name']}__",
            config_enabled=server.get("enabled", True), state="starting",
            transport=server.get("transport", "streamable_http"),
        )

    def _withdraw(self, key: str) -> None:
        """只撤下目标连接发布的工具和路由，不改变其他服务。"""
        snapshot = self._service_states.get(key)
        if snapshot is None:
            return
        for name in snapshot.tools:
            self.tools.pop(name, None)
            self._tool_to_session.pop(name, None)
        self.server_stats.pop(snapshot.tool_prefix[5:-2], None)
        self._service_states[key] = replace(snapshot, tools=(), discovered=0, filtered=0)

    def _mark_failed(self, key: str, detail: str) -> None:
        """记录已观察到的连接失败并撤下不可用目录。"""
        self._withdraw(key)
        snapshot = self._service_states.get(key)
        if snapshot is not None:
            self._service_states[key] = replace(snapshot, state="failed", connection_error=detail)

    async def start_service(
        self, server: NormalizedMcpServer, status: ExternalMcpStatus | None = None,
    ) -> bool:
        """复用现有建连时限和 owner 启动一个服务，不执行 required 全组收束。"""
        return await _connect_external_server(
            self, server, asyncio.Semaphore(1), asyncio.Semaphore(1), status,
        )

    async def stop_service(self, key: str) -> None:
        """只关闭目标，调用方取消后仍等待拥有资源的任务收束。"""
        async with self._close_lock:
            cancelled = False
            connection = next((item for item in self._connections if item.config_key == key), None)
            self._withdraw(key)
            snapshot = self._service_states.get(key)
            if snapshot is not None:
                self._service_states[key] = replace(snapshot, state="stopping", tools=(), discovered=0, filtered=0)
            if connection is not None:
                cleanup = asyncio.create_task(self._retire_connection(connection))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
            snapshot = self._service_states.get(key)
            if snapshot is not None:
                self._service_states[key] = replace(snapshot, state="stopped", connection_error=None)
            if cancelled:
                raise asyncio.CancelledError

    @staticmethod
    async def _establish_session(
        server_params: StdioServerParameters | SseServerParameters | StreamableHttpParameters,
        session_params: ClientSessionParameters,
        disconnected: asyncio.Event,
        session_stack: contextlib.AsyncExitStack,
    ) -> tuple[mcp_types.Implementation, ClientSession, contextlib.AsyncExitStack]:
        """在 owner 提供的栈内建立会话，半初始化失败也由同一 owner 负责清理。"""
        if isinstance(server_params, StdioServerParameters):
            session_stack.enter_context(route_stdio_client_logs())
            stderr_sink = session_stack.enter_context(open(
                os.devnull, mode="w", encoding=server_params.encoding,
                errors=server_params.encoding_error_handler,
            ))
            read, write = await session_stack.enter_async_context(stdio_client(server_params, errlog=stderr_sink))
        elif isinstance(server_params, SseServerParameters):
            client = sse_client(
                url=server_params.url, headers=server_params.headers,
                timeout=server_params.timeout, sse_read_timeout=server_params.sse_read_timeout,
                httpx_client_factory=functools.partial(external_http_client, disconnected=disconnected),
            )
            read, write = await session_stack.enter_async_context(client)
        else:
            httpx_client = external_http_client(
                disconnected=disconnected, headers=server_params.headers,
                timeout=httpx.Timeout(server_params.timeout.total_seconds(), read=server_params.sse_read_timeout.total_seconds()),
            )
            await session_stack.enter_async_context(httpx_client)
            client = streamable_http_client(
                url=server_params.url, http_client=httpx_client,
                terminate_on_close=server_params.terminate_on_close,
            )
            read, write, _ = await session_stack.enter_async_context(client)
        session = await session_stack.enter_async_context(ClientSession(
            ObservedMcpReadStream(read, disconnected), write,
            read_timeout_seconds=session_params.read_timeout_seconds,
            sampling_callback=session_params.sampling_callback,
            elicitation_callback=session_params.elicitation_callback,
            list_roots_callback=session_params.list_roots_callback,
            logging_callback=session_params.logging_callback,
            message_handler=session_params.message_handler,
            client_info=session_params.client_info,
        ))
        result = await session.initialize()
        return result.serverInfo, session, session_stack

    @staticmethod
    async def _collect_tools(
        server_info: mcp_types.Implementation,
        session: ClientSession,
        *,
        transport: str | None = None,
        rules: dict[str, list[str]] | None = None,
        default_approval_mode: str = "auto",
        tool_approval_modes: dict[str, str] | None = None,
        config_server_key: str | None = None,
    ) -> tuple[dict[str, mcp_types.Tool], int]:
        """读取单个外部服务的工具列表，并生成待提交的工具映射。"""
        tools_temp: dict[str, mcp_types.Tool] = {}

        alias = slugify_mcp_name(server_info.name, fallback="server")

        capabilities = session.get_server_capabilities()
        if capabilities is not None and capabilities.tools is None:
            # 服务明确声明不支持 tools 时，直接跳过，不视为连接失败。
            return tools_temp, 0

        try:
            tools = (await session.list_tools()).tools
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            observe(
                "external_mcp.tools.failed",
                level="WARNING",
                server=server_info.name,
                error=external_status_detail_from_exception(exc),
            )
            raise

        for index, tool in enumerate(tools, start=1):
            if is_mcp_tool_allowed(tool.name, rules):
                # 对外展示的工具名会加服务前缀，原始名称保留给会话调用。
                name = tool_name_hook(tool.name, server_info)
                meta = dict(tool.meta or {})
                meta["server"] = alias

                if transport:
                    meta.setdefault("transport", str(transport).strip().lower())
                if config_server_key:
                    meta["config_server_key"] = config_server_key
                overrides = tool_approval_modes or {}
                meta["approval_mode"] = normalize_mcp_approval_mode(
                    overrides.get(tool.name),
                    default=default_approval_mode,
                )
                meta.setdefault("approval_allow_session", True)
                meta.setdefault("approval_allow_persistent", True)

                tools_temp[name] = tool.model_copy(update={"meta": meta})

            if index % EXTERNAL_MCP_TOOL_YIELD_INTERVAL == 0:
                await asyncio.sleep(0)

        return tools_temp, len(tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None
    ) -> mcp_types.CallToolResult:
        """根据聚合后的工具名找到真实会话，并使用服务原始工具名发起调用。"""
        if self._closing or self._closed:
            raise AppError("External MCP group is closing")

        session = self._tool_to_session[name]
        session_tool_name = self.tools[name].name

        try:
            return await session.call_tool(
                session_tool_name,
                arguments if args is None else args,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta,
            )
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError, httpx.TransportError, McpError) as error:
            if not isinstance(error, McpError) or error.error.code == mcp_types.CONNECTION_CLOSED:
                for connection in tuple(self._connections):
                    snapshot = self._service_states.get(connection.config_key)
                    if snapshot is not None and name in snapshot.tools:
                        self._mark_failed(connection.config_key, external_status_detail_from_exception(error))
                        connection.stop_event.set()
            raise

    async def call_hook_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, typing.Any] | None = None,
        *,
        read_timeout_seconds: typing.Any = None,
    ) -> mcp_types.CallToolResult:
        """按 MCP server 别名和原始工具名调用 Hook 工具。"""
        normalized_server = str(server or "").strip()
        normalized_tool = str(tool or "").strip()
        for exposed_name, descriptor in self.tools.items():
            meta = descriptor.meta or {}
            if (
                str(meta.get("server") or "").strip() == normalized_server
                and descriptor.name == normalized_tool
            ):
                return await self.call_tool(
                    exposed_name,
                    arguments,
                    read_timeout_seconds=read_timeout_seconds,
                    meta={"hook": True},
                )
        raise KeyError(
            f"MCP Hook tool not found: {normalized_server}/{normalized_tool}"
        )

    async def start(
        self,
        servers: list[NormalizedMcpServer],
        status: ExternalMcpStatus | None = None
    ) -> int:
        """补齐缺失连接，required 判定完成后原子发布本批目录；失败只回收本批。"""
        if self._closing or self._closed:
            raise RuntimeError("External MCP group is closed")
        enabled: list[NormalizedMcpServer] = []
        for item in servers:
            if not bool(item.get("enabled", True)):
                continue
            key = item.get("config_key", item["name"])
            snapshot = self._service_states.get(key)
            if snapshot is not None and snapshot.state == "ready":
                continue
            if key in self.owned_keys:
                await self.stop_service(key)
            if status is not None:
                status.mark_linking(item)
            enabled.append(item)

        if not enabled:
            if status is not None:
                status.finish()
            return 0

        limiter = asyncio.Semaphore(EXTERNAL_MCP_CONNECT_CONCURRENCY)
        stdio_limiter = asyncio.Semaphore(EXTERNAL_MCP_STDIO_CONCURRENCY)
        batch_keys = frozenset(item.get("config_key", item["name"]) for item in enabled)
        self._staged_keys.update(batch_keys)
        committed = False

        connect_tasks = [
            asyncio.create_task(
                _connect_external_server(
                    self,
                    server,
                    limiter,
                    stdio_limiter,
                    status,
                ),
                name=f"external MCP {server.get('name') or 'server'} startup",
            )
            for server in enabled
        ]

        try:
            await asyncio.gather(*connect_tasks)
            prepared = [
                connection for connection in self._connections
                if connection.config_key in batch_keys
                and not connection.task.done()
                and connection.ready.done() and not connection.ready.cancelled()
                and connection.ready.exception() is None
                and not connection.ready.result().disconnected.is_set()
                and self._service_states[connection.config_key].state == "starting"
            ]
            prepared_keys = {connection.config_key for connection in prepared}
            connected_servers = len(prepared)

            failed_required = [
                str(server.get("name") or "server")
                for server in enabled
                if server.get("required") is True
                and server.get("config_key", server["name"]) not in prepared_keys
            ]
            if failed_required:
                names = ", ".join(failed_required)
                raise AppError(f"Required MCP server failed to start: {names}")

            self._check_publication()
            for connection in prepared:
                self._publish(connection.config_key, connection.ready.result())
            committed = True

            if status is not None:
                for server in enabled:
                    key = server.get("config_key", server["name"])
                    snapshot = self._service_states[key]
                    if key in prepared_keys:
                        status.mark_ready(server, server["name"], len(snapshot.tools), discovered_count=snapshot.discovered)
                    else:
                        status.mark_failed(server, snapshot.connection_error or "MCP connection closed before publication")

            if connected_servers <= 0:
                observe(
                    "external_mcp.unavailable",
                    level="WARNING",
                    configured=len(enabled),
                )

            if status is not None:
                status.finish()
            return connected_servers
        finally:
            cleanup = asyncio.create_task(self._finish_batch(connect_tasks, batch_keys, committed))
            cancelled = False
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
            finally:
                self._staged_keys.difference_update(batch_keys)
                if status is not None:
                    status.finish_unresolved()
            if cancelled:
                raise asyncio.CancelledError

    async def _finish_batch(
        self, tasks: list[asyncio.Task[bool]], keys: frozenset[str], committed: bool,
    ) -> None:
        """取消未完成启动并收束未提交批次，不触碰操作前连接。"""
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if committed:
            return
        connections = tuple(item for item in self._connections if item.config_key in keys)
        for connection in connections:
            snapshot = self._service_states[connection.config_key]
            if snapshot.state in ("starting", "ready"):
                self._service_states[connection.config_key] = replace(snapshot, state="stopping")
        results = await asyncio.gather(
            *(self._retire_connection(item) for item in connections), return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        for key in keys:
            snapshot = self._service_states.get(key)
            if snapshot is not None and snapshot.state in ("starting", "ready"):
                self._service_states[key] = replace(snapshot, state="stopped", tools=(), discovered=0, filtered=0)

    async def close(self) -> None:
        """通知所有连接所有者释放资源，并在完成后清空工具索引。"""
        async with self._close_lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        """在关闭锁内收束连接，并为协作退出设置固定等待窗口。"""
        if self._closed:
            return None

        self._closing = True

        connections = tuple(self._connections)
        for connection in connections:
            connection.stop_event.set()
            if not connection.ready.done() and not connection.task.done():
                connection.task.cancel()

        results = await asyncio.gather(
            *(self._retire_connection(connection) for connection in connections),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self.tools.clear()
        self.server_stats.clear()
        self._tool_to_session.clear()
        self._closed = True

    async def connect_with_alias(
        self,
        server: NormalizedMcpServer
    ) -> tuple[str, int, int]:
        """启动单个连接所有者，并使用配置名作为稳定别名聚合工具。"""
        if self._closing or self._closed:
            raise RuntimeError("External MCP group is closed")

        server_name = str(server.get("name") or "server")
        config_key = str(server.get("config_key", server_name))
        if any(item.config_key == config_key or item.server == server_name for item in self._connections):
            raise ValueError("MCP service or tool prefix is already owned by a connection")
        self._remember_server(server)
        stop_event = asyncio.Event()

        ready = asyncio.get_running_loop().create_future()

        task = asyncio.create_task(
            self._run_owned_connection(server, stop_event, ready),
            name=f"external MCP {server_name} owner",
        )

        connection = _ExternalMcpConnection(
            server=server_name,
            stop_event=stop_event,
            ready=ready,
            task=task,
            config_key=config_key,
        )
        self._connections.append(connection)
        task.add_done_callback(lambda done: self._connection_finished(connection, done))

        try:
            prepared = await asyncio.shield(ready)
            if task.done() or prepared.disconnected.is_set() or self._closing or self._service_states[config_key].state != "starting":
                raise ConnectionError("MCP connection closed before publication")
            matching_tools = prepared.tools.keys() & self.tools.keys()
            if matching_tools:
                # 工具名冲突会导致调用无法唯一路由，因此不提交当前连接。
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"{matching_tools} already exist in group tools."
                    )
                )

            if config_key not in self._staged_keys:
                self._publish(config_key, prepared)
            return prepared.alias, len(prepared.tools), prepared.discovered_count
        except BaseException as error:
            self._mark_failed(config_key, external_status_detail_from_exception(error))
            await self._retire_connection(connection)
            raise

    def _publish(self, key: str, prepared: _ExternalMcpConnectionReady) -> None:
        """在不让出执行权的步骤中共同发布目录、路由和连接事实。"""
        self._check_publication()
        snapshot = self._service_states[key]
        exposed = len(prepared.tools)
        self.tools.update(prepared.tools)
        self._tool_to_session.update({name: prepared.session for name in prepared.tools})
        self.server_stats[prepared.alias] = {
            "server": prepared.alias, "transport": snapshot.transport,
            "discovered": prepared.discovered_count, "exposed": exposed,
            "filtered": prepared.discovered_count - exposed,
        }
        self._service_states[key] = replace(
            snapshot, state="ready", tools=tuple(sorted(prepared.tools)),
            discovered=prepared.discovered_count, filtered=prepared.discovered_count - exposed,
        )

    def _check_publication(self) -> None:
        """拒绝将旧工作区或已移交实例的迟到连接发布给新使用范围。"""
        if self._closing or self._closed or (self._can_publish is not None and not self._can_publish()):
            raise RuntimeError("MCP runtime or workspace is no longer active")

    def _connection_finished(self, connection: _ExternalMcpConnection, task: asyncio.Task[None]) -> None:
        """消费 owner 终态，只归约仍属于本次连接的记录，防止旧任务回写。"""
        if connection not in self._connections:
            return
        error = None if task.cancelled() else task.exception()
        snapshot = self._service_states.get(connection.config_key)
        if snapshot is not None and snapshot.state in ("ready", "starting"):
            self._mark_failed(
                connection.config_key,
                external_status_detail_from_exception(error) if error else "MCP connection closed",
            )
        if connection.cleaned:
            self._connections.remove(connection)

    async def _run_owned_connection(
        self,
        server: NormalizedMcpServer,
        stop_event: asyncio.Event,
        ready: asyncio.Future[_ExternalMcpConnectionReady]
    ) -> None:
        """在固定任务内建立、维持并关闭单个外部连接。"""
        session_stack = contextlib.AsyncExitStack()
        disconnected = asyncio.Event()
        waiters: list[asyncio.Task[bool]] = []
        config_key = str(server.get("config_key", server.get("name", "server")))

        try:
            alias = slugify_mcp_name(server.get("name"), fallback="server")
            params = build_server_params(server)

            server_info, session, session_stack = await self._establish_session(
                params,
                ClientSessionParameters(
                    read_timeout_seconds=timedelta(
                        seconds=request_timeout_sec(server)
                    )
                ),
                disconnected,
                session_stack,
            )
            alias_info = mcp_types.Implementation(
                name=alias,
                version=server_info.version,
                websiteUrl=server_info.websiteUrl,
                icons=server_info.icons
            )
            tools, discovered_count = await self._collect_tools(
                alias_info,
                session,
                transport=str(server.get("transport") or "streamable_http"),
                rules=server.get("tool_filter") or {},
                default_approval_mode=str(
                    server.get("default_tools_approval_mode") or "auto"
                ),
                tool_approval_modes=(
                    dict(server.get("tool_approval_modes") or {})
                    if isinstance(server.get("tool_approval_modes"), dict)
                    else {}
                ),
                config_server_key=str(server.get("config_key") or "") or None,
            )

            if disconnected.is_set():
                raise ConnectionError("MCP transport closed during initialization")
            if not ready.done():
                ready.set_result(_ExternalMcpConnectionReady(
                    alias=alias,
                    session=session,
                    tools=tools,
                    discovered_count=discovered_count,
                    disconnected=disconnected,
                ))
            waiters = [asyncio.create_task(stop_event.wait()), asyncio.create_task(disconnected.wait())]
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if disconnected.is_set() and not stop_event.is_set():
                raise ConnectionError("MCP transport closed")

        except BaseException as exc:
            self._mark_failed(config_key, external_status_detail_from_exception(exc))
            if not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(exc)
            raise

        finally:
            for waiter in waiters:
                waiter.cancel()
            connection = next((item for item in self._connections if item.task is asyncio.current_task()), None)
            try:
                with route_session_termination_warnings():
                    await session_stack.aclose()
            except BaseException as error:
                if connection is not None:
                    connection.cleanup_error = external_status_detail_from_exception(error)
                self._mark_failed(config_key, external_status_detail_from_exception(error))
                raise
            else:
                if connection is not None:
                    connection.cleaned = True
            finally:
                await asyncio.gather(*waiters, return_exceptions=True)

    async def _retire_connection(
        self,
        connection: _ExternalMcpConnection
    ) -> None:
        """停止尚未发布或发布失败的连接所有者。"""
        connection.stop_event.set()
        if not connection.ready.done() and not connection.task.done():
            connection.task.cancel()

        self._withdraw(connection.config_key)
        _, pending = await asyncio.wait({connection.task}, timeout=EXTERNAL_MCP_CLOSE_TIMEOUT_SEC)
        if pending:
            connection.task.cancel()
            _, pending = await asyncio.wait(pending, timeout=EXTERNAL_MCP_CLOSE_TIMEOUT_SEC)
        if pending or connection.cleanup_error is not None or (not connection.cleaned and connection.ready.done()):
            detail = connection.cleanup_error or "MCP connection cleanup timed out"
            self._mark_failed(connection.config_key, detail)
            raise RuntimeError(detail)
        await asyncio.gather(connection.task, return_exceptions=True)
        if connection in self._connections:
            self._connections.remove(connection)
        snapshot = self._service_states.get(connection.config_key)
        if snapshot is not None and snapshot.state == "stopping":
            self._service_states[connection.config_key] = replace(snapshot, state="stopped", connection_error=None)


class ExternalMcpTools:
    """提供单次使用范围的冻结目录；引用退出后失效，不可保存到后续 Turn。

    连接仍归原 group 所有，运行时门禁在此视图存续期间禁止相应服务重建。
    断线会使原路由失败，不尝试迁移请求或重放工具调用。
    """

    def __init__(self, group: ExternalMcpGroup, tools: dict[str, mcp_types.Tool]) -> None:
        """冻结可调用名称，不复制连接所有权。"""
        self._group = group
        self._tools = MappingProxyType(tools)
        self._active = True

    @property
    def tools(self) -> Mapping[str, mcp_types.Tool]:
        """返回本次使用范围独立持有的只读目录。"""
        return self._tools

    def release(self) -> None:
        """在使用范围退出时使后续调用失效。"""
        self._active = False

    async def call_tool(
        self, name: str, arguments: dict[str, ThawedJsonValue] | None = None,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: ProgressFnT | None = None, *,
        meta: dict[str, ThawedJsonValue] | None = None,
    ) -> mcp_types.CallToolResult:
        """只调用本范围公开的原连接，不接受中途加入的工具。"""
        if not self._active:
            raise RuntimeError("MCP tool scope has ended")
        if name not in self._tools:
            raise KeyError(name)
        return await self._group.call_tool(
            name, arguments, read_timeout_seconds, progress_callback, meta=meta,
        )

    async def call_hook_tool(
        self, server: str, tool: str, arguments: dict[str, ThawedJsonValue] | None = None,
        *, read_timeout_seconds: timedelta | None = None,
    ) -> mcp_types.CallToolResult:
        """按冻结目录中的配置别名查找 Hook 工具。"""
        for name, descriptor in self._tools.items():
            if (descriptor.meta or {}).get("server") == server.strip() and descriptor.name == tool.strip():
                return await self.call_tool(name, arguments, read_timeout_seconds, meta={"hook": True})
        raise KeyError(f"MCP Hook tool not found: {server}/{tool}")


async def _connect_external_server(
    group: ExternalMcpGroup,
    server: NormalizedMcpServer,
    limiter: asyncio.Semaphore,
    stdio_limiter: asyncio.Semaphore,
    status: ExternalMcpStatus | None
) -> bool:
    """在独立启动时限内连接一个外部 MCP 服务并更新状态。"""
    name = str(server.get("name") or "server")

    transport = str(
        server.get("transport") or "streamable_http"
    ).strip().lower()

    start_limit = startup_timeout_sec(server)
    preflight_limit = min(start_limit, EXTERNAL_MCP_PREFLIGHT_TIMEOUT_SEC)

    phase = "preflight"
    try:
        group._remember_server(server)
        async with asyncio.timeout(preflight_limit):
            await preflight_server(server)

        phase = "startup"
        if transport == "stdio":
            async with stdio_limiter:
                async with limiter:
                    async with asyncio.timeout(start_limit):
                        alias, tool_count, discovered_count = (
                            await group.connect_with_alias(server)
                        )
        else:
            async with limiter:
                async with asyncio.timeout(start_limit):
                    alias, tool_count, discovered_count = (
                        await group.connect_with_alias(server)
                    )

        if status is not None and server.get("config_key", name) not in group._staged_keys:
            status.mark_ready(
                server,
                alias,
                tool_count,
                discovered_count=discovered_count,
            )
        observe(
            "external_mcp.server.connected",
            server=alias,
            transport=transport,
            discovered=discovered_count,
            exposed=tool_count,
            filtered=discovered_count - tool_count,
        )
        return True
    except BaseException as exc:
        key = str(server.get("config_key", name))
        if should_reraise_external(exc):
            group._mark_failed(key, external_status_detail_from_exception(exc))
            raise

        if isinstance(exc, asyncio.TimeoutError):
            limit = preflight_limit if phase == "preflight" else start_limit
            detail = f"{phase} timed out after {limit:g}s"
        else:
            detail = external_status_detail_from_exception(exc)

        group._mark_failed(key, detail)
        observe(
            "external_mcp.server.failed",
            level="WARNING",
            server=name,
            transport=transport,
            phase=phase,
            detail=detail,
        )
        if status is not None:
            status.mark_failed(server, detail)
        return False


if __name__ == '__main__':
    pass
