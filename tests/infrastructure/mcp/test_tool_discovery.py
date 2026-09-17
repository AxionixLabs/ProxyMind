# -*- coding: utf-8 -*-

import asyncio
import contextlib
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

import pytest
from mcp import (
    ClientSession,
    types as mcp_types,
)

from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.tool_discovery import collect_tool_catalog


def page(names: list[str], cursor: str | None = None) -> mcp_types.ListToolsResult:
    return mcp_types.ListToolsResult(
        tools=[mcp_types.Tool(name=name, inputSchema={"type": "object"}) for name in names],
        nextCursor=cursor,
    )


@pytest.mark.anyio
async def test_all_pages_are_filtered_and_annotated_as_one_catalog() -> None:
    session = Mock(spec=ClientSession)
    session.get_server_capabilities.return_value = mcp_types.ServerCapabilities(tools=mcp_types.ToolsCapability())
    session.list_tools = AsyncMock(side_effect=[page(["first"], "next"), page([], "last"), page(["second", "hidden"])])
    tools, discovered = await ExternalMcpGroup._collect_tools(
        mcp_types.Implementation(name="audit", version="1"), session,
        rules={"deny": ["hidden"]}, default_approval_mode="prompt", config_server_key="audit",
    )
    assert discovered == 3
    assert list(tools) == ["mcp__audit__first", "mcp__audit__second"]
    assert all(tool.meta is not None and tool.meta["approval_mode"] == "prompt" for tool in tools.values())
    assert [call.kwargs["params"] for call in session.list_tools.await_args_list] == [
        None, mcp_types.PaginatedRequestParams(cursor="next"), mcp_types.PaginatedRequestParams(cursor="last"),
    ]


@pytest.mark.anyio
async def test_distinct_raw_names_cannot_overwrite_one_exposed_tool() -> None:
    session = Mock(spec=ClientSession)
    session.get_server_capabilities.return_value = mcp_types.ServerCapabilities(tools=mcp_types.ToolsCapability())
    session.list_tools = AsyncMock(side_effect=[page(["search files"], "next"), page(["search_files"])])
    with pytest.raises(ValueError, match="collide after normalization"):
        await ExternalMcpGroup._collect_tools(mcp_types.Implementation(name="audit", version="1"), session)


@pytest.mark.anyio
@pytest.mark.parametrize("pages, error", [
    ([page(["first"], "cycle"), page(["second"], "cycle")], "repeated cursor"),
    ([page(["same"], "next"), page(["same"])], "duplicate tool names"),
    ([page(["same", "same"])], "duplicate tool names"),
    ([page([], "汉" * (64 * 1024 // 3 + 1))], "byte limit"),
    ([page([], "\ud800")], "invalid cursor"),
    ([page([str(index) for index in range(2049)])], "item limit"),
    ([page([], str(index)) for index in range(100)], "page limit"),
])
async def test_catalog_rejects_unbounded_or_ambiguous_pages(
    pages: list[mcp_types.ListToolsResult], error: str,
) -> None:
    session = Mock(spec=ClientSession)
    session.list_tools = AsyncMock(side_effect=pages)
    with pytest.raises(ValueError, match=error):
        await collect_tool_catalog(session)
    assert session.list_tools.await_count == len(pages)


@pytest.mark.anyio
async def test_item_limit_counts_across_pages() -> None:
    session = Mock(spec=ClientSession)
    session.list_tools = AsyncMock(side_effect=[
        page([str(index) for index in range(2048)], "next"), page(["overflow"]),
    ])
    with pytest.raises(ValueError, match="item limit"):
        await collect_tool_catalog(session)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
async def test_later_page_failure_does_not_publish_partial_catalog_and_closes_owner(failure: str) -> None:
    closed = asyncio.Event()
    waiting = asyncio.Event()
    calls: list[str | None] = []

    async def list_tools(*, params: mcp_types.PaginatedRequestParams | None) -> mcp_types.ListToolsResult:
        calls.append(params.cursor if params else None)
        if params is None:
            return page(["first"], "second")
        if failure == "error":
            raise ValueError("fixture later page failed")
        waiting.set()
        await asyncio.Event().wait()
        return page([])

    session = Mock(spec=ClientSession)
    session.get_server_capabilities.return_value = mcp_types.ServerCapabilities(tools=mcp_types.ToolsCapability())
    session.list_tools = AsyncMock(side_effect=list_tools)

    async def establish(
        _params, _session_params, _disconnected, stack: contextlib.AsyncExitStack, *, config_key: str, auth,
    ):
        stack.callback(closed.set)
        return mcp_types.Implementation(name="audit", version="1"), session, stack

    group = ExternalMcpGroup()
    with patch.object(ExternalMcpGroup, "_establish_session", side_effect=establish), patch(
        "infrastructure.mcp.external_group.preflight_server", new=AsyncMock(),
    ):
        task = asyncio.create_task(group.start([{
            "name": "audit", "config_key": "audit", "url": "https://audit.example/mcp",
            "transport": "streamable_http", "startup_timeout_sec": 0.05 if failure == "timeout" else 2.0,
        }]))
        if failure == "cancel":
            await asyncio.wait_for(waiting.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == 0
        assert not group.tools and closed.is_set()
        await group.close()
    assert calls == [None, "second"]
    assert not group.owned_keys
