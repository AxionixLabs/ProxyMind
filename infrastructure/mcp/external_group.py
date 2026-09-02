# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
import contextlib
from datetime import timedelta
from dataclasses import dataclass
from mcp import (
    ClientSession,
    types as mcp_types,
)
from infrastructure.errors import AppError
from observability import (
    observe,
    observe_exception,
)
from observability.third_party import route_session_termination_warnings
from mcp.client.sse import sse_client
from mcp.client.stdio import (
    StdioServerParameters,
    stdio_client
)
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session_group import (
    ClientSessionParameters,
    SseServerParameters
)
from mcp.shared.exceptions import McpError
from infrastructure.mcp.settings import (
    is_mcp_tool_allowed,
    request_timeout_sec,
    startup_timeout_sec,
)
from infrastructure.mcp.transport import (
    build_server_params,
    external_http_client,
    preflight_server,
)
from infrastructure.mcp.values import (
    slugify_mcp_name,
    tool_name_hook,
)
from infrastructure.mcp.external_status import (
    ExternalMcpStatus,
    external_status_detail_from_exception,
    should_reraise_external,
)

EXTERNAL_MCP_CONNECT_CONCURRENCY = 2
EXTERNAL_MCP_STDIO_CONCURRENCY = 1
EXTERNAL_MCP_PREFLIGHT_TIMEOUT_SEC = 2.0
EXTERNAL_MCP_TOOL_YIELD_INTERVAL = 32


@dataclass(frozen=True, slots=True)
class _ExternalMcpConnectionReady(object):
    """保存单个外部连接完成初始化后的可发布资源。"""
    alias: str
    session: ClientSession
    tools: dict[str, mcp_types.Tool]
    discovered_count: int


@dataclass(slots=True)
class _ExternalMcpConnection(object):
    """保存单个外部连接的所有者任务和关闭信号。"""
    server: str
    stop_event: asyncio.Event
    ready: asyncio.Future[_ExternalMcpConnectionReady]
    task: asyncio.Task[None]


class ExternalMcpGroup(object):
    """管理一组外部 MCP 会话，并把多个服务的工具合并成统一入口。"""

    def __init__(self) -> None:
        """初始化外部 MCP 工具索引和连接所有者集合。"""
        self.tools: dict[str, mcp_types.Tool] = {}
        self.server_stats: dict[str, dict[str, typing.Any]] = {}

        self._tool_to_session: dict[str, ClientSession] = {}
        self._connections: list[_ExternalMcpConnection] = []

        self._closing: bool = False
        self._closed: bool = False

    @staticmethod
    async def _establish_session(
        server_params: typing.Any,
        session_params: ClientSessionParameters
    ) -> tuple[mcp_types.Implementation, ClientSession, contextlib.AsyncExitStack]:
        """按服务传输类型建立 MCP 会话，并返回尚未转交所有权的资源栈。"""
        session_stack = contextlib.AsyncExitStack()

        try:
            # 各传输入口不同，但最终都产出 MCP read/write 流。
            if isinstance(server_params, StdioServerParameters):
                read, write = await session_stack.enter_async_context(
                    stdio_client(server_params)
                )
            elif isinstance(server_params, SseServerParameters):
                client = sse_client(
                    url=server_params.url,
                    headers=server_params.headers,
                    timeout=server_params.timeout,
                    sse_read_timeout=server_params.sse_read_timeout,
                    httpx_client_factory=external_http_client
                )
                read, write = await session_stack.enter_async_context(client)
            else:
                httpx_client = external_http_client(
                    headers=server_params.headers,
                    timeout=httpx.Timeout(
                        server_params.timeout.total_seconds(),
                        read=server_params.sse_read_timeout.total_seconds()
                    )
                )
                await session_stack.enter_async_context(httpx_client)

                client = streamable_http_client(
                    url=server_params.url,
                    http_client=httpx_client,
                    terminate_on_close=server_params.terminate_on_close
                )
                read, write, _ = await session_stack.enter_async_context(client)

            # ClientSession 进入上下文后再 initialize，避免半初始化资源泄漏。
            session = await session_stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=session_params.read_timeout_seconds,
                    sampling_callback=session_params.sampling_callback,
                    elicitation_callback=session_params.elicitation_callback,
                    list_roots_callback=session_params.list_roots_callback,
                    logging_callback=session_params.logging_callback,
                    message_handler=session_params.message_handler,
                    client_info=session_params.client_info
                )
            )

            result = await session.initialize()
            return result.serverInfo, session, session_stack

        except BaseException:
            # 建连过程任一步失败，都只清理本次临时栈，不影响 group 中已有连接。
            with contextlib.suppress(BaseException):
                await session_stack.aclose()
            raise

    @staticmethod
    async def _collect_tools(
        server_info: mcp_types.Implementation,
        session: ClientSession,
        *,
        transport: str | None = None,
        rules: dict[str, list[str]] | None = None
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
            observe_exception(
                "external_mcp.tools.failed",
                exc,
                level="WARNING",
                server=server_info.name,
            )
            return tools_temp, 0

        for index, tool in enumerate(tools, start=1):
            if is_mcp_tool_allowed(tool.name, rules):
                # 对外展示的工具名会加服务前缀，原始名称保留给会话调用。
                name = tool_name_hook(tool.name, server_info)
                meta = dict(tool.meta or {})
                meta.setdefault("server", alias)

                if transport:
                    meta.setdefault("transport", str(transport).strip().lower())

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

        return await session.call_tool(
            session_tool_name,
            arguments if args is None else args,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=meta
        )

    async def start(
        self,
        servers: list[dict[str, typing.Any]],
        status: ExternalMcpStatus | None = None
    ) -> int:
        """并发启动已启用的外部服务，并返回成功连接数量。"""
        if self._closing or self._closed:
            raise RuntimeError("External MCP group is closed")
        if self._connections:
            raise RuntimeError("External MCP group is already started")

        enabled: list[dict[str, typing.Any]] = []
        for item in servers:
            if not bool(item.get("enabled", True)):
                continue
            if status is not None:
                status.mark_linking(item)
            enabled.append(item)

        if not enabled:
            if status is not None:
                status.finish()
            return 0

        limiter = asyncio.Semaphore(EXTERNAL_MCP_CONNECT_CONCURRENCY)
        stdio_limiter = asyncio.Semaphore(EXTERNAL_MCP_STDIO_CONCURRENCY)

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
            connection_results = await asyncio.gather(*connect_tasks)
            connected_servers = sum(connection_results)

            failed_required = [
                str(server.get("name") or "server")
                for server, connected in zip(enabled, connection_results)
                if server.get("required") is True and not connected
            ]
            if failed_required:
                names = ", ".join(failed_required)
                raise AppError(f"Required MCP server failed to start: {names}")

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
            for task in connect_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*connect_tasks, return_exceptions=True)
            if status is not None:
                status.finish_unresolved()

    async def close(self) -> None:
        """通知所有连接所有者释放资源，并在完成后清空工具索引。"""
        if self._closed:
            return None

        self._closing = True

        connections = tuple(self._connections)
        for connection in connections:
            connection.stop_event.set()
            if not connection.ready.done() and not connection.task.done():
                connection.task.cancel()

        results = await asyncio.gather(
            *(connection.task for connection in connections),
            return_exceptions=True,
        )

        critical_error: BaseException | None = None
        try:
            for connection, result in zip(connections, results):
                if not isinstance(result, BaseException):
                    continue
                if should_reraise_external(result):
                    critical_error = result
                    continue
                observe_exception(
                    "external_mcp.cleanup.failed",
                    result,
                    level="WARNING",
                    server=connection.server,
                )
        finally:
            self._connections.clear()
            self.tools.clear()
            self.server_stats.clear()
            self._tool_to_session.clear()
            self._closed = True

        if critical_error is not None:
            raise critical_error

    async def connect_with_alias(
        self,
        server: dict[str, typing.Any]
    ) -> tuple[str, int, int]:
        """启动单个连接所有者，并使用配置名作为稳定别名聚合工具。"""
        if self._closing or self._closed:
            raise RuntimeError("External MCP group is closed")

        server_name = str(server.get("name") or "server")
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
        )
        self._connections.append(connection)

        try:
            prepared = await asyncio.shield(ready)
            matching_tools = prepared.tools.keys() & self.tools.keys()
            if matching_tools:
                # 工具名冲突会导致调用无法唯一路由，因此不提交当前连接。
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"{matching_tools} already exist in group tools."
                    )
                )

            # 发布阶段不再 await，保证工具目录和路由映射一起生效。
            self.tools.update(prepared.tools)

            self._tool_to_session.update({
                name: prepared.session
                for name in prepared.tools
            })

            exposed_count = len(prepared.tools)

            self.server_stats[prepared.alias] = {
                "server": prepared.alias,
                "transport": str(
                    server.get("transport") or "streamable_http"
                ),
                "discovered": prepared.discovered_count,
                "exposed": exposed_count,
                "filtered": max(
                    0,
                    prepared.discovered_count - exposed_count,
                ),
            }
            return prepared.alias, exposed_count, prepared.discovered_count
        except BaseException:
            with contextlib.suppress(BaseException):
                await self._retire_connection(connection)
            raise

    async def _run_owned_connection(
        self,
        server: dict[str, typing.Any],
        stop_event: asyncio.Event,
        ready: asyncio.Future[_ExternalMcpConnectionReady]
    ) -> None:
        """在固定任务内建立、维持并关闭单个外部连接。"""
        session_stack: contextlib.AsyncExitStack | None = None

        try:
            alias = slugify_mcp_name(server.get("name"), fallback="server")
            params = build_server_params(server)

            server_info, session, session_stack = await self._establish_session(
                params,
                ClientSessionParameters(
                    read_timeout_seconds=timedelta(
                        seconds=request_timeout_sec(server)
                    )
                )
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
                rules=server.get("tools") or {},
            )

            if not ready.done():
                ready.set_result(_ExternalMcpConnectionReady(
                    alias=alias,
                    session=session,
                    tools=tools,
                    discovered_count=discovered_count,
                ))
            await stop_event.wait()

        except BaseException as exc:
            if not ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    ready.cancel()
                else:
                    ready.set_exception(exc)
            raise

        finally:
            if session_stack is not None:
                with route_session_termination_warnings():
                    await session_stack.aclose()

    async def _retire_connection(
        self,
        connection: _ExternalMcpConnection
    ) -> None:
        """停止尚未发布或发布失败的连接所有者。"""
        connection.stop_event.set()
        if not connection.ready.done() and not connection.task.done():
            connection.task.cancel()

        await asyncio.gather(connection.task, return_exceptions=True)

        with contextlib.suppress(ValueError):
            self._connections.remove(connection)


async def _connect_external_server(
    group: ExternalMcpGroup,
    server: dict[str, typing.Any],
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

        if status is not None:
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
        if should_reraise_external(exc):
            raise

        if isinstance(exc, asyncio.TimeoutError):
            limit = preflight_limit if phase == "preflight" else start_limit
            detail = f"{phase} timed out after {limit:g}s"
        else:
            detail = external_status_detail_from_exception(exc)

        observe_exception(
            "external_mcp.server.failed",
            exc,
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
