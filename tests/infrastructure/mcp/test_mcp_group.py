# -*- coding: utf-8 -*-

import asyncio
import contextlib
import logging
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from mcp import types as mcp_types

from infrastructure.mcp import external_group as mcp_group
from infrastructure.mcp import transport as mcp_transport
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.external_status import ExternalMcpStatus
from infrastructure.mcp.errors import summarize_exception


def _servers(count: int, *, startup_timeout_sec: float = 1.0) -> list[dict]:
    return [
        {
            "name": f"server-{index}",
            "enabled": True,
            "transport": "streamable_http",
            "url": f"https://server-{index}.example.test/mcp",
            "startup_timeout_sec": startup_timeout_sec,
        }
        for index in range(count)
    ]


def _stdio_servers(count: int) -> list[dict]:
    return [
        {
            "name": f"stdio-{index}",
            "enabled": True,
            "transport": "stdio",
            "command": "server",
        }
        for index in range(count)
    ]


def _stub_connection_transport(monkeypatch):
    async def establish(_params, _session_params, _disconnected, stack):
        session = SimpleNamespace(
            get_server_capabilities=lambda: mcp_types.ServerCapabilities(tools=mcp_types.ToolsCapability()),
            list_tools=AsyncMock(return_value=mcp_types.ListToolsResult(tools=[
                mcp_types.Tool(name="ping", inputSchema={"type": "object"}),
            ])),
        )
        return mcp_types.Implementation(name="fixture", version="1"), session, stack

    monkeypatch.setattr(ExternalMcpGroup, "_establish_session", staticmethod(establish))
    return ExternalMcpGroup.connect_with_alias


@pytest.mark.anyio
async def test_external_mcp_close_hides_only_sdk_termination_warning(
    caplog,
    monkeypatch,
) -> None:
    sdk_logger = logging.getLogger("mcp.client.streamable_http")

    def emit_close_logs() -> None:
        sdk_logger.warning("Session termination failed: All connection attempts failed")
        sdk_logger.warning("unrelated SDK warning")

    class SessionStack(object):
        async def aclose(self) -> None:
            emit_close_logs()

    async def establish(_params, _session_params, _disconnected, _stack):
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack()

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
        default_approval_mode="auto",
        tool_approval_modes=None,
        config_server_key=None,
    ):
        return {}, 0

    monkeypatch.setattr(
        ExternalMcpGroup,
        "_establish_session",
        staticmethod(establish),
    )
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_collect_tools",
        staticmethod(collect),
    )

    group = ExternalMcpGroup()

    with caplog.at_level(logging.WARNING, logger=sdk_logger.name):
        await group.connect_with_alias({
            "name": "docs",
            "transport": "streamable_http",
            "url": "https://docs.example.test/mcp",
        })
        await group.close()

    messages = [record.getMessage() for record in caplog.records]
    assert "Session termination failed: All connection attempts failed" not in messages
    assert "unrelated SDK warning" in messages


@pytest.mark.anyio
async def test_external_mcp_connects_six_servers_with_bounded_concurrency(
    monkeypatch,
) -> None:
    servers = _servers(6)
    status = ExternalMcpStatus(servers)
    state = {"active": 0, "maximum": 0}
    connect_owned = _stub_connection_transport(monkeypatch)

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        state["active"] += 1
        state["maximum"] = max(state["maximum"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return await connect_owned(_group, server)

    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    group = ExternalMcpGroup()
    connected = await group.start(servers, status=status)
    await group.close()

    snapshot = status.snapshot()
    assert connected == 6
    assert state["maximum"] == mcp_group.EXTERNAL_MCP_CONNECT_CONCURRENCY
    assert [item["state"] for item in snapshot["items"]] == ["ready"] * 6


@pytest.mark.anyio
async def test_external_mcp_serializes_stdio_process_startup(monkeypatch) -> None:
    servers = _stdio_servers(4)
    state = {"active": 0, "maximum": 0}
    connect_owned = _stub_connection_transport(monkeypatch)

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        state["active"] += 1
        state["maximum"] = max(state["maximum"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return await connect_owned(_group, server)

    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    group = ExternalMcpGroup()
    connected = await group.start(servers)
    await group.close()

    assert connected == 4
    assert state["maximum"] == mcp_group.EXTERNAL_MCP_STDIO_CONCURRENCY


@pytest.mark.anyio
async def test_queued_stdio_does_not_occupy_general_connection_slot(
    monkeypatch,
) -> None:
    servers = [
        *_stdio_servers(2),
        _servers(1)[0],
    ]
    remote_started = asyncio.Event()
    connect_owned = _stub_connection_transport(monkeypatch)

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        if server["name"] == "stdio-0":
            await asyncio.wait_for(remote_started.wait(), timeout=0.2)
        elif server["name"] == "server-0":
            remote_started.set()
        await asyncio.sleep(0)
        return await connect_owned(_group, server)

    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    group = ExternalMcpGroup()
    connected = await group.start(servers)
    await group.close()

    assert connected == 3
    assert remote_started.is_set()


@pytest.mark.anyio
async def test_stdio_preflight_does_not_block_event_loop(monkeypatch) -> None:
    marker_reached = asyncio.Event()

    def blocking_preflight(_server) -> None:
        time.sleep(0.02)

    async def mark_scheduled() -> None:
        await asyncio.sleep(0)
        marker_reached.set()

    monkeypatch.setattr(
        mcp_transport,
        "preflight_stdio_server",
        blocking_preflight,
    )

    marker = asyncio.create_task(mark_scheduled())
    await mcp_transport.preflight_server({
        "transport": "stdio",
        "command": "server",
    })

    assert marker.done()
    assert marker_reached.is_set()


@pytest.mark.anyio
async def test_external_mcp_timeout_starts_after_concurrency_slot_is_acquired(
    monkeypatch,
) -> None:
    servers = _servers(2, startup_timeout_sec=0.02)
    status = ExternalMcpStatus(servers)
    connect_owned = _stub_connection_transport(monkeypatch)

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        if server["name"] == "server-0":
            await asyncio.sleep(0.05)
        return await connect_owned(_group, server)

    monkeypatch.setattr(mcp_group, "EXTERNAL_MCP_CONNECT_CONCURRENCY", 1)
    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    group = ExternalMcpGroup()
    connected = await group.start(servers, status=status)
    await group.close()

    first, second = status.snapshot()["items"]
    assert connected == 1
    assert first["state"] == "failed"
    assert first["detail"] == "startup timed out after 0.02s"
    assert second["state"] == "ready"


@pytest.mark.anyio
async def test_external_mcp_failed_preparation_closes_private_resources(
    monkeypatch,
) -> None:
    state = {"closed": 0}

    class SessionStack(object):
        async def aclose(self) -> None:
            state["closed"] += 1

    async def establish(_params, _session_params, _disconnected, _stack):
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack()

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
        default_approval_mode="auto",
        tool_approval_modes=None,
        config_server_key=None,
    ):
        raise RuntimeError("tool discovery failed")

    monkeypatch.setattr(
        ExternalMcpGroup,
        "_establish_session",
        staticmethod(establish),
    )
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_collect_tools",
        staticmethod(collect),
    )

    group = ExternalMcpGroup()
    with pytest.raises(RuntimeError, match="tool discovery failed"):
        await group.connect_with_alias({
            "name": "docs",
            "transport": "streamable_http",
            "url": "https://docs.example.test/mcp",
        })
    await group.close()

    assert state["closed"] == 1


@pytest.mark.anyio
async def test_external_mcp_owner_closes_resources_in_entering_task(
    monkeypatch,
) -> None:
    state = {
        "closed": 0,
        "entered_task": None,
        "closed_task": None,
    }

    class SessionStack(object):
        def __init__(self, stack) -> None:
            self.stack = stack

        async def aclose(self) -> None:
            state["closed"] += 1
            state["closed_task"] = asyncio.current_task()
            await self.stack.aclose()

    async def establish(_params, _session_params, _disconnected, _stack):
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(anyio.create_task_group())
        state["entered_task"] = asyncio.current_task()
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack(stack)

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
        default_approval_mode="auto",
        tool_approval_modes=None,
        config_server_key=None,
    ):
        return {}, 0

    monkeypatch.setattr(
        ExternalMcpGroup,
        "_establish_session",
        staticmethod(establish),
    )
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_collect_tools",
        staticmethod(collect),
    )

    group = ExternalMcpGroup()
    alias, tool_count, discovered_count = await group.connect_with_alias({
        "name": "docs",
        "transport": "streamable_http",
        "url": "https://docs.example.test/mcp",
    })
    assert (alias, tool_count, discovered_count) == ("docs", 0, 0)
    assert state["closed"] == 0
    assert state["entered_task"] is not asyncio.current_task()

    await group.close()

    assert state["closed"] == 1
    assert state["closed_task"] is state["entered_task"]


@pytest.mark.anyio
async def test_external_mcp_timeout_closes_resources_in_owner_task(
    monkeypatch,
) -> None:
    state = {
        "entered_task": None,
        "closed_task": None,
    }
    collection_started = asyncio.Event()

    class SessionStack(object):
        def __init__(self, stack) -> None:
            self.stack = stack

        async def aclose(self) -> None:
            state["closed_task"] = asyncio.current_task()
            await self.stack.aclose()

    async def preflight(_server) -> None:
        return None

    async def establish(_params, _session_params, _disconnected, _stack):
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(anyio.create_task_group())
        state["entered_task"] = asyncio.current_task()
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack(stack)

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
        default_approval_mode="auto",
        tool_approval_modes=None,
        config_server_key=None,
    ):
        collection_started.set()
        await asyncio.Future()

    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_establish_session",
        staticmethod(establish),
    )
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_collect_tools",
        staticmethod(collect),
    )

    group = ExternalMcpGroup()
    connected = await group.start(_servers(1, startup_timeout_sec=0.01))
    await group.close()

    assert collection_started.is_set()
    assert connected == 0
    assert state["entered_task"] is not asyncio.current_task()
    assert state["closed_task"] is state["entered_task"]


@pytest.mark.anyio
async def test_external_mcp_close_retains_owner_when_cleanup_cannot_be_confirmed(
    monkeypatch,
) -> None:
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    class SessionStack(object):
        async def aclose(self) -> None:
            close_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise

    async def establish(_params, _session_params, _disconnected, _stack):
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack()

    async def collect(
        _server_info,
        _session,
        **_options,
    ):
        return {}, 0

    monkeypatch.setattr(
        ExternalMcpGroup,
        "_establish_session",
        staticmethod(establish),
    )
    monkeypatch.setattr(
        ExternalMcpGroup,
        "_collect_tools",
        staticmethod(collect),
    )
    monkeypatch.setattr(mcp_group, "EXTERNAL_MCP_CLOSE_TIMEOUT_SEC", 0.1)

    group = ExternalMcpGroup()
    await group.connect_with_alias({
        "name": "docs",
        "transport": "streamable_http",
        "url": "https://docs.example.test/mcp",
    })

    first_close = asyncio.create_task(group.close())
    await asyncio.wait_for(close_started.wait(), timeout=2.0)
    second_close = asyncio.create_task(group.close())
    results = await asyncio.gather(first_close, second_close, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    with pytest.raises(RuntimeError, match="CancelledError"):
        await group.close()

    assert close_started.is_set()
    assert close_cancelled.is_set()
    assert group.tools == {}
    assert group.server_stats == {}
    assert group._tool_to_session == {}
    assert len(group._connections) == 1
    assert group.service_snapshots[0].state == "failed"


@pytest.mark.anyio
async def test_external_mcp_disconnect_is_not_replayed() -> None:
    state = {"calls": 0}

    class DisconnectedSession(object):
        async def call_tool(self, *_args, **_kwargs):
            state["calls"] += 1
            raise anyio.EndOfStream

    group = ExternalMcpGroup()
    exposed_name = "mcp__docs__lookup"
    group.tools[exposed_name] = mcp_types.Tool(
        name="lookup",
        inputSchema={},
    )
    group._tool_to_session[exposed_name] = DisconnectedSession()

    with pytest.raises(anyio.EndOfStream):
        await group.call_tool(exposed_name, {"query": "value"})

    assert state["calls"] == 1
    await group.close()


@pytest.mark.anyio
async def test_external_mcp_failed_stdio_does_not_leak_child_output(
    tmp_path,
    capfd,
    caplog,
) -> None:
    server_script = tmp_path / "failed_mcp_stdio_fixture.py"
    server_script.write_text(
        "import sys\n"
        "print('MCP_STDOUT_MARKER', flush=True)\n"
        "print('MCP_STDERR_MARKER', file=sys.stderr, flush=True)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    server = {
        "name": "failed-stdio",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(tmp_path),
        "startup_timeout_sec": 10.0,
        "timeout_sec": 10.0,
    }
    status = ExternalMcpStatus([server])
    group = ExternalMcpGroup()

    with caplog.at_level(logging.ERROR, logger="mcp.client.stdio"):
        connected = await group.start([server], status=status)
    await group.close()

    captured = capfd.readouterr()
    visible = f"{captured.out}\n{captured.err}"
    assert "MCP_STDOUT_MARKER" not in visible
    assert "MCP_STDERR_MARKER" not in visible
    assert "Failed to parse JSONRPC message from server" not in visible
    assert not [
        record
        for record in caplog.records
        if record.name == "mcp.client.stdio"
    ]
    assert connected == 0
    assert status.snapshot()["items"][0]["state"] == "failed"


@pytest.mark.anyio
async def test_external_mcp_real_stdio_round_trip_and_repeated_close(
    tmp_path,
) -> None:
    server_script = tmp_path / "mcp_stdio_fixture.py"
    server_script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fixture')\n"
        "@server.tool()\n"
        "def ping(value: str) -> str:\n"
        "    return f'pong:{value}'\n"
        "if __name__ == '__main__':\n"
        "    server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    group = ExternalMcpGroup()

    connected = await group.start([{
        "name": "stage4",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(tmp_path),
        "startup_timeout_sec": 10.0,
        "timeout_sec": 10.0,
    }])
    result = await group.call_tool(
        "mcp__stage4__ping",
        {"value": "ok"},
    )

    assert connected == 1
    assert result.content == [mcp_types.TextContent(
        type="text",
        text="pong:ok",
    )]

    await group.close()
    await group.close()

    assert group.tools == {}
    assert group.server_stats == {}


def test_external_mcp_error_summary_redacts_transport_credentials() -> None:
    detail = summarize_exception(RuntimeError(
        "failed https://user:password@example.test/"
        "0123456789abcdef0123456789abcdef?api_key=query-secret#token "
        "Authorization: Bearer header-secret password=plain-secret"
    ))

    assert "user:password" not in detail.casefold()
    assert "query-secret" not in detail
    assert "header-secret" not in detail
    assert "plain-secret" not in detail
    assert detail.count("<redacted>") >= 4


@pytest.mark.anyio
async def test_external_mcp_filters_discovered_tools_before_registration() -> None:
    class Session(object):
        def get_server_capabilities(self):
            return SimpleNamespace(tools=object())

        async def list_tools(self):
            return SimpleNamespace(tools=[
                mcp_types.Tool(name="get_bug", inputSchema={}),
                mcp_types.Tool(name="delete_bug", inputSchema={}),
                mcp_types.Tool(name="list_projects", inputSchema={}),
            ])

    group = ExternalMcpGroup()
    tools, discovered_count = await group._collect_tools(
        mcp_types.Implementation(name="zentao", version="1"),
        Session(),
        transport="stdio",
        rules={
            "allow": ["*_bug", "list_*"],
            "deny": ["delete_*"],
        },
        default_approval_mode="writes",
        tool_approval_modes={"get_bug": "prompt"},
        config_server_key="ZenTao",
    )

    assert discovered_count == 3
    assert list(tools) == [
        "mcp__zentao__get_bug",
        "mcp__zentao__list_projects",
    ]
    assert tools["mcp__zentao__get_bug"].meta["approval_mode"] == "prompt"
    assert tools["mcp__zentao__list_projects"].meta["approval_mode"] == "writes"
    assert tools["mcp__zentao__get_bug"].meta["config_server_key"] == "ZenTao"


@pytest.mark.anyio
async def test_external_mcp_tool_collection_yields_to_event_loop() -> None:
    class Session(object):
        def get_server_capabilities(self):
            return SimpleNamespace(tools=object())

        async def list_tools(self):
            return SimpleNamespace(tools=[
                mcp_types.Tool(name=f"tool_{index}", inputSchema={})
                for index in range(64)
            ])

    marker_reached = asyncio.Event()

    async def mark_scheduled() -> None:
        await asyncio.sleep(0)
        marker_reached.set()

    marker = asyncio.create_task(mark_scheduled())
    group = ExternalMcpGroup()
    tools, discovered_count = await group._collect_tools(
        mcp_types.Implementation(name="docs", version="1"),
        Session(),
    )

    assert marker.done()
    assert marker_reached.is_set()
    assert discovered_count == 64
    assert len(tools) == 64
