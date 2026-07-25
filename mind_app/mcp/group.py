# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
import logging
import contextlib
from datetime import timedelta
from types import TracebackType
from contextlib import asynccontextmanager
from mcp import ClientSession, types as mcp_types
from engine.errors import ApplicationError
from engine.observability import (
    observe,
    observe_exception
)
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
from .config import (
    build_server_params,
    external_http_client,
    is_mcp_tool_allowed,
    preflight_server,
    request_timeout_sec,
    slugify_mcp_name,
    startup_timeout_sec,
    tool_name_hook
)
from .status import (
    ExternalMcpStatus,
    external_status_detail_from_exception,
    should_reraise_external
)

EXTERNAL_MCP_CONNECT_CONCURRENCY   = 4
EXTERNAL_MCP_PREFLIGHT_TIMEOUT_SEC = 2.0

_STREAMABLE_HTTP_LOGGER_NAME = "mcp.client.streamable_http"
_SESSION_TERMINATION_WARNING = "Session termination failed:"


class _SessionTerminationLogFilter(logging.Filter):
    """把 SDK 已处理的会话关闭失败转入诊断日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤关闭期 warning，并保留其它 SDK 日志。"""
        message = record.getMessage()
        if not message.startswith(_SESSION_TERMINATION_WARNING):
            return True
        observe(
            "external_mcp.cleanup.warning",
            level="WARNING",
            detail=message,
        )
        return False


@contextlib.contextmanager
def _route_session_termination_warnings() -> typing.Iterator[None]:
    """在外接会话关闭期间临时接管 SDK 关闭 warning。"""
    sdk_logger = logging.getLogger(_STREAMABLE_HTTP_LOGGER_NAME)
    log_filter = _SessionTerminationLogFilter()
    sdk_logger.addFilter(log_filter)
    try:
        yield
    finally:
        sdk_logger.removeFilter(log_filter)


class ExternalMcpGroup(object):
    """管理一组外部 MCP 会话，并把多个服务的工具合并成统一入口。"""

    def __init__(self) -> None:
        """初始化外部 MCP 工具索引和资源释放栈。"""
        self.tools: dict[str, mcp_types.Tool] = {}
        self.server_stats: dict[str, dict[str, typing.Any]] = {}
        self._tool_to_session: dict[str, ClientSession] = {}
        self._exit_stack = contextlib.AsyncExitStack()

    async def __aenter__(self) -> "ExternalMcpGroup":
        """进入外部 MCP 资源栈，后续连接都会挂到同一个栈上统一释放。"""
        await self._exit_stack.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        """退出时关闭所有已建立的外部连接，并清空工具索引。"""
        try:
            with _route_session_termination_warnings():
                return await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            observe_exception("external_mcp.cleanup.failed", exc, level="WARNING")
            return None
        finally:
            self.tools.clear()
            self.server_stats.clear()
            self._tool_to_session.clear()

    @staticmethod
    async def _establish_session(
        server_params: typing.Any,
        session_params: ClientSessionParameters,
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
        rules: dict[str, list[str]] | None = None,
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

        for tool in tools:
            if not is_mcp_tool_allowed(tool.name, rules):
                continue

            # 对外展示的工具名会加服务前缀，原始工具名保留在 tool.name 中用于调用。
            name = tool_name_hook(tool.name, server_info)
            meta = dict(tool.meta or {})
            meta.setdefault("server", alias)

            if transport:
                meta.setdefault("transport", str(transport).strip().lower())

            tools_temp[name] = tool.model_copy(update={"meta": meta})

        return tools_temp, len(tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None,
    ) -> mcp_types.CallToolResult:
        """根据聚合后的工具名找到真实会话，并使用服务原始工具名发起调用。"""
        session           = self._tool_to_session[name]
        session_tool_name = self.tools[name].name

        return await session.call_tool(
            session_tool_name,
            arguments if args is None else args,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=meta
        )

    async def connect_with_alias(
        self,
        server: dict[str, typing.Any]
    ) -> tuple[str, int, int]:
        """连接单个外部服务，并使用配置名作为稳定别名聚合工具。"""
        alias  = slugify_mcp_name(server.get("name"), fallback="server")
        params = build_server_params(server)

        server_info, session, session_stack = await self._establish_session(
            params,
            ClientSessionParameters(
                read_timeout_seconds=timedelta(seconds=request_timeout_sec(server))
            )
        )

        try:
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

            matching_tools = tools.keys() & self.tools.keys()
            if matching_tools:
                # 工具名冲突会导致调用无法唯一路由，因此不提交当前连接。
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"{matching_tools} already exist in group tools."
                    )
                )

            # 从此处开始不再 await，确保资源所有权和工具路由一次性提交。
            self._exit_stack.push_async_callback(session_stack.aclose)
            self.tools.update(tools)
            self._tool_to_session.update({name: session for name in tools})
            exposed_count = len(tools)
            self.server_stats[alias] = {
                "server"     : alias,
                "transport"  : str(
                    server.get("transport") or "streamable_http"
                ),
                "discovered" : discovered_count,
                "exposed"    : exposed_count,
                "filtered"    : max(0, discovered_count - exposed_count),
            }
            return alias, exposed_count, discovered_count
        except BaseException:
            with contextlib.suppress(BaseException):
                await session_stack.aclose()
            raise


async def _connect_external_server(
    group: ExternalMcpGroup,
    server: dict[str, typing.Any],
    limiter: asyncio.Semaphore,
    status: ExternalMcpStatus | None,
) -> bool:
    """在独立启动时限内连接一个外部 MCP 服务并更新状态。"""
    name        = str(server.get("name") or "server")
    transport   = str(server.get("transport") or "streamable_http")
    start_limit = startup_timeout_sec(server)
    preflight_limit = min(start_limit, EXTERNAL_MCP_PREFLIGHT_TIMEOUT_SEC)
    phase       = "preflight"

    try:
        async with asyncio.timeout(preflight_limit):
            await preflight_server(server)

        phase = "startup"
        async with limiter:
            async with asyncio.timeout(start_limit):
                alias, tool_count, discovered_count = await group.connect_with_alias(
                    server
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


@asynccontextmanager
async def open_optional_external_mcp_group(
    servers: list[dict[str, typing.Any]],
    status: ExternalMcpStatus | None = None,
) -> typing.AsyncIterator[typing.Optional[ExternalMcpGroup]]:
    """尽力打开外部 MCP group；没有可用外部服务时返回 None，让主流程只用本地 MCP。"""
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
        yield None
        return

    group = ExternalMcpGroup()

    try:
        await group.__aenter__()
    except BaseException as exc:
        if should_reraise_external(exc):
            raise
        observe_exception("external_mcp.group.failed", exc, level="WARNING")
        if status is not None:
            detail = external_status_detail_from_exception(exc)
            for item in enabled:
                status.mark_failed(item, detail)
            status.finish()
        yield None
        return

    limiter = asyncio.Semaphore(EXTERNAL_MCP_CONNECT_CONCURRENCY)
    connect_tasks = [
        asyncio.create_task(
            _connect_external_server(
                group,
                server,
                limiter,
                status,
            ),
            name=f"external MCP {server.get('name') or 'server'}",
        )
        for server in enabled
    ]

    try:
        connection_results = await asyncio.gather(*connect_tasks)
        connected_servers  = sum(connection_results)

        failed_required = [
            str(server.get("name") or "server")
            for server, connected in zip(enabled, connection_results)
            if server.get("required") is True and not connected
        ]

        if failed_required:
            names = ", ".join(failed_required)
            raise ApplicationError(
                f"Required MCP server failed to start: {names}"
            )

        if connected_servers <= 0:
            observe(
                "external_mcp.unavailable",
                level="WARNING",
                configured=len(enabled),
            )
            if status is not None:
                status.finish()
            yield None
            return

        if status is not None:
            status.finish()
        yield group

    finally:
        if status is not None:
            status.finish_unresolved()

        # 调用方退出上下文时，确保所有未完成任务和外部 MCP 连接都被清理。
        for task in connect_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*connect_tasks, return_exceptions=True)

        try:
            await group.__aexit__(None, None, None)
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            observe_exception("external_mcp.cleanup.failed", exc, level="WARNING")


if __name__ == '__main__':
    pass
