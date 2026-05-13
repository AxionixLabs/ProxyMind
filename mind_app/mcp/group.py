# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import httpx
import typing
import asyncio
import contextlib
from datetime import timedelta
from types import TracebackType
from contextlib import asynccontextmanager
from loguru import logger
from mcp import ClientSession, types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session_group import (
    ClientSessionParameters, SseServerParameters
)
from mcp.shared.exceptions import McpError
from .config import (
    build_server_params,
    external_http_client,
    preflight_server,
    request_timeout_sec,
    slugify_mcp_name,
    tool_name_hook
)
from .status import (
    ExternalMcpStatus,
    cached_failure_reason,
    external_status_detail_from_exception,
    mark_server_failure,
    mark_server_success,
    remaining_budget,
    should_reraise_external,
    summarize_exception
)


class ExternalMcpGroup(object):
    def __init__(self) -> None:
        self.tools: dict[str, mcp_types.Tool] = {}
        self._tool_to_session: dict[str, ClientSession] = {}
        self._exit_stack = contextlib.AsyncExitStack()

    async def __aenter__(self) -> "ExternalMcpGroup":
        await self._exit_stack.__aenter__()
        return self

    async def _establish_session(
        self,
        server_params: typing.Any,
        session_params: ClientSessionParameters,
    ) -> tuple[mcp_types.Implementation, ClientSession]:
        session_stack = contextlib.AsyncExitStack()

        try:
            if isinstance(server_params, SseServerParameters):
                client = sse_client(
                    url=server_params.url,
                    headers=server_params.headers,
                    timeout=server_params.timeout,
                    sse_read_timeout=server_params.sse_read_timeout,
                    httpx_client_factory=external_http_client,
                )
                read, write = await session_stack.enter_async_context(client)
            else:
                httpx_client = external_http_client(
                    headers=server_params.headers,
                    timeout=httpx.Timeout(
                        server_params.timeout.total_seconds(),
                        read=server_params.sse_read_timeout.total_seconds(),
                    ),
                )
                await session_stack.enter_async_context(httpx_client)

                client = streamable_http_client(
                    url=server_params.url,
                    http_client=httpx_client,
                    terminate_on_close=server_params.terminate_on_close,
                )
                read, write, _ = await session_stack.enter_async_context(client)

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
                    client_info=session_params.client_info,
                )
            )

            result = await session.initialize()
            await self._exit_stack.enter_async_context(session_stack)
            return result.serverInfo, session

        except BaseException:
            with contextlib.suppress(BaseException):
                await session_stack.aclose()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        try:
            return await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            logger.debug(f"[MCP] external cleanup failed {summarize_exception(exc)}")
            return None
        finally:
            self.tools.clear()
            self._tool_to_session.clear()

    async def _aggregate_tools(
        self,
        server_info: mcp_types.Implementation,
        session: ClientSession,
        *,
        transport: str | None = None,
    ) -> int:
        tools_temp: dict[str, mcp_types.Tool] = {}
        tool_to_session_temp: dict[str, ClientSession] = {}
        alias = slugify_mcp_name(server_info.name, fallback="server")

        capabilities = session.get_server_capabilities()
        if capabilities is not None and capabilities.tools is None:
            return 0

        try:
            tools = (await session.list_tools()).tools
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            logger.debug(f"[MCP] external tools skipped {summarize_exception(exc)}")
            return 0

        for tool in tools:
            name = tool_name_hook(tool.name, server_info)
            meta = dict(tool.meta or {})
            meta.setdefault("server", alias)
            if transport:
                meta.setdefault("transport", str(transport).strip().lower())
            tools_temp[name] = tool.model_copy(update={"meta": meta})
            tool_to_session_temp[name] = session

        matching_tools = tools_temp.keys() & self.tools.keys()
        if matching_tools:
            raise McpError(
                mcp_types.ErrorData(
                    code=mcp_types.INVALID_PARAMS,
                    message=f"{matching_tools} already exist in group tools.",
                )
            )

        self.tools.update(tools_temp)
        self._tool_to_session.update(tool_to_session_temp)
        return len(tools_temp)

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
        session = self._tool_to_session[name]
        session_tool_name = self.tools[name].name
        return await session.call_tool(
            session_tool_name,
            arguments if args is None else args,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=meta,
        )

    async def connect_with_alias(self, server: dict[str, typing.Any]) -> tuple[str, int]:
        alias = slugify_mcp_name(server.get("name"), fallback="server")
        params = build_server_params(server)

        server_info, session = await self._establish_session(
            params,
            ClientSessionParameters(
                read_timeout_seconds=timedelta(seconds=request_timeout_sec(server))
            ),
        )

        alias_info = mcp_types.Implementation(
            name=alias,
            version=server_info.version,
            websiteUrl=server_info.websiteUrl,
            icons=server_info.icons,
        )
        tool_count = await self._aggregate_tools(
            alias_info,
            session,
            transport=str(server.get("transport") or "streamable_http"),
        )

        return alias, tool_count


@asynccontextmanager
async def open_optional_external_mcp_group(
    servers: list[dict[str, typing.Any]],
    status: ExternalMcpStatus | None = None,
) -> typing.AsyncIterator[typing.Optional[ExternalMcpGroup]]:
    enabled: list[dict[str, typing.Any]] = []
    for item in servers:
        if not bool(item.get("enabled", True)):
            continue

        name = str(item.get("name") or "server")
        transport = str(item.get("transport") or "streamable_http")
        reason = cached_failure_reason(item)
        if reason:
            if status is not None:
                status.mark_cached(item, reason)
            logger.debug(
                f"[MCP] external cached skip name={name} transport={transport} reason={reason}"
            )
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
    connected_servers = 0
    deadline = time.monotonic() + 5.0

    try:
        await group.__aenter__()
    except BaseException as exc:
        if should_reraise_external(exc):
            raise
        logger.debug(f"[MCP] external group skipped {summarize_exception(exc)}")
        if status is not None:
            detail = external_status_detail_from_exception()
            for item in enabled:
                status.mark_failed(item, detail)
            status.finish()
        yield None
        return

    preflight_tasks = {asyncio.create_task(preflight_server(server)): server for server in enabled}
    pending = set(preflight_tasks)

    try:
        while pending:
            remaining = remaining_budget(deadline)
            if remaining <= 0:
                logger.debug("[MCP] external connect budget exhausted")
                if status is not None:
                    detail = external_status_detail_from_exception()
                    for task in pending:
                        status.mark_failed(preflight_tasks[task], detail)
                break

            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                logger.debug("[MCP] external connect budget exhausted")
                if status is not None:
                    detail = external_status_detail_from_exception()
                    for task in pending:
                        status.mark_failed(preflight_tasks[task], detail)
                break

            for task in done:
                server = preflight_tasks[task]
                name = str(server.get("name") or "server")
                transport = str(server.get("transport") or "streamable_http")

                try:
                    await task
                except BaseException as exc:
                    if should_reraise_external(exc):
                        raise
                    logger.debug(
                        f"[MCP] external connect failed name={name} transport={transport} "
                        f"{summarize_exception(exc)}"
                    )
                    detail = external_status_detail_from_exception()
                    mark_server_failure(server)
                    if status is not None:
                        status.mark_failed(server, detail)
                    continue

                try:
                    remaining = remaining_budget(deadline)
                    if remaining <= 0:
                        raise asyncio.TimeoutError("external connect budget exhausted")

                    async with asyncio.timeout(remaining):
                        alias, tool_count = await group.connect_with_alias(server)
                    connected_servers += 1
                    mark_server_success(server)
                    if status is not None:
                        status.mark_ready(server, alias, tool_count)
                    logger.debug(
                        f"[MCP] external connected name={alias} "
                        f"transport={transport} tools={tool_count}"
                    )
                except BaseException as exc:
                    if should_reraise_external(exc):
                        raise
                    logger.debug(
                        f"[MCP] external connect failed name={name} transport={transport} "
                        f"{summarize_exception(exc)}"
                    )
                    detail = external_status_detail_from_exception()
                    mark_server_failure(server)
                    if status is not None:
                        status.mark_failed(server, detail)

        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            pending = set()

        if connected_servers <= 0:
            logger.debug("[MCP] external unavailable, local helix only")
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

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        try:
            await group.__aexit__(None, None, None)
        except BaseException as exc:
            if should_reraise_external(exc):
                raise
            logger.debug(f"[MCP] external cleanup failed {summarize_exception(exc)}")
