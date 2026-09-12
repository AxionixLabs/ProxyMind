"""通过真实子进程、套接字和 MCP SDK 验证 P0 的可复现验收输入。"""

import asyncio
import contextlib
import sys
import tomllib

import httpx
import pytest

from collections.abc import AsyncIterator
from pathlib import Path

from mcp import (
    ClientSession,
    types as mcp_types,
)
from mcp.client.sse import sse_client
from mcp.client.stdio import (
    StdioServerParameters,
    stdio_client,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from infrastructure.config.schema import (
    apply_config_overrides,
    parse_config_override,
)
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.settings import normalize_mcp_servers
from infrastructure.mcp.transport import external_http_client
from tests.external_mcp.fixtures import (
    FixtureMode,
    FixtureReply,
    FixtureSpec,
    FixtureTransport,
    client_arguments,
    read_facts,
    remote_fixture,
    wait_for_fact,
    write_config,
)


@contextlib.asynccontextmanager
async def stdio_session(spec: FixtureSpec) -> AsyncIterator[ClientSession]:
    """在真实 stdio transport 内创建和释放客户端会话。"""
    async with stdio_client(StdioServerParameters(
        command=sys.executable, args=list(spec.arguments()),
        cwd=str(Path(__file__).resolve().parents[2]),
    )) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            yield session


def reply(result: mcp_types.CallToolResult) -> FixtureReply:
    """在验收边界校验测试工具的单条结构化文本结果。"""
    assert not result.isError
    assert len(result.content) == 1
    item = result.content[0]
    assert isinstance(item, mcp_types.TextContent)
    return FixtureReply.model_validate_json(item.text)


@pytest.mark.anyio
async def test_stdio_fixture_has_real_instance_identity_and_session_cleanup(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "stdio")
    async with stdio_session(spec) as session:
        assert {tool.name for tool in (await session.list_tools()).tools} == {"ping", "block"}
        first = reply(await session.call_tool("ping", {"value": "first"}))
        second = reply(await session.call_tool("ping", {"value": "second"}))
        assert first.instance_id == second.instance_id
        assert first.session_id == second.session_id
        assert (first.call_count, second.call_count) == (1, 2)
        assert (first.value, second.value) == ("first", "second")
    await wait_for_fact(spec.facts_path, "process.closed")
    events = [fact.event for fact in read_facts(spec.facts_path)]
    assert events.count("initialized") == 1
    assert events.count("session.closed") == 1
    async with stdio_session(spec) as session:
        restarted = reply(await session.call_tool("ping"))
        assert restarted.instance_id != first.instance_id


@pytest.mark.anyio
async def test_block_fixture_can_be_released_without_repeating_call(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "block")
    async with stdio_session(spec) as session:
        task = asyncio.create_task(session.call_tool("block"))
        try:
            await wait_for_fact(spec.facts_path, "tool.started")
            assert not task.done()
            spec.release_path.touch()
            result = reply(await asyncio.wait_for(task, timeout=5.0))
            assert result.call_count == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert sum(fact.event == "tool.started" for fact in read_facts(spec.facts_path)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["empty", "no-tools", "discovery-failure"])
async def test_zero_tools_and_failed_discovery_are_distinct_fixture_inputs(tmp_path, mode: FixtureMode) -> None:
    spec = FixtureSpec(tmp_path, mode, mode=mode)
    async with stdio_session(spec) as session:
        if mode == "discovery-failure":
            with pytest.raises(McpError, match="fixture tool discovery failed"):
                await session.list_tools()
        elif mode == "no-tools":
            capabilities = session.get_server_capabilities()
            assert capabilities is not None and capabilities.tools is None
        else:
            assert (await session.list_tools()).tools == []


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
async def test_remote_fixture_survives_client_disconnect_and_records_new_sessions(tmp_path, transport: FixtureTransport) -> None:
    spec = FixtureSpec(tmp_path, transport, transport)
    results: list[FixtureReply] = []
    async with asyncio.timeout(20.0), remote_fixture(spec) as remote:
        for _ in range(2):
            offset = len(read_facts(spec.facts_path))
            async with contextlib.AsyncExitStack() as stack:
                if transport == "sse":
                    read, write = await stack.enter_async_context(sse_client(
                        remote.url, timeout=5.0, sse_read_timeout=5.0,
                        httpx_client_factory=external_http_client,
                    ))
                else:
                    client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
                    read, write, _ = await stack.enter_async_context(streamable_http_client(remote.url, http_client=client))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                results.append(reply(await session.call_tool("ping")))
            assert remote.process.returncode is None
            await wait_for_fact(spec.facts_path, "session.closed", after=offset)
        assert results[0].instance_id == results[1].instance_id
        assert results[0].session_id != results[1].session_id
        assert [item.call_count for item in results] == [1, 2]
    assert remote.process.returncode is not None
    events = [fact.event for fact in read_facts(spec.facts_path)]
    assert events.count("initialized") == 2
    assert events.count("session.opened") == 2
    assert events.count("session.closed") == 2


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["startup-failure", "handshake-timeout"])
async def test_real_failure_fixture_cannot_be_mistaken_for_ready(tmp_path, mode: FixtureMode) -> None:
    spec = FixtureSpec(tmp_path, mode, mode=mode)
    group = ExternalMcpGroup()
    try:
        connected = await group.start([{
            "name": spec.name, "transport": "stdio", "command": sys.executable,
            "args": list(spec.arguments()), "startup_timeout_sec": 1.0,
            "cwd": str(Path(__file__).resolve().parents[2]),
        }])
        assert connected == 0
        assert not group.tools
        assert any(fact.event == "fault.injected" for fact in read_facts(spec.facts_path))
    finally:
        await group.close()


def test_fixture_configuration_is_isolated_reproducible_and_not_overwritten(tmp_path) -> None:
    path = write_config(tmp_path, ())
    before = path.read_bytes()
    config = tomllib.loads(before.decode("utf-8"))
    servers = normalize_mcp_servers(config["mcp_servers"])
    assert {item["config_key"] for item in servers} >= {"A", "B", "D", "E", "F", "Docs API", "Docs/API", "all"}
    disabled = next(item for item in servers if item["config_key"] == "D")
    assert disabled["enabled"] is False
    assert str(tmp_path) in " ".join(disabled["args"])
    filtered = next(item for item in servers if item["config_key"] == "Filtered")
    assert filtered["tool_filter"] == {"allow": []}
    with pytest.raises(FileExistsError):
        write_config(tmp_path, ())
    assert path.read_bytes() == before


@pytest.mark.anyio
async def test_disconnect_fixture_exits_after_one_actual_call(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "disconnect", mode="disconnect")
    async with asyncio.timeout(10.0), stdio_session(spec) as session:
        with pytest.raises(McpError):
            await session.call_tool("ping")
    facts = read_facts(spec.facts_path)
    assert sum(fact.event == "tool.started" for fact in facts) == 1
    assert not any(fact.event == "tool.completed" for fact in facts)
    assert any(fact.event == "fault.injected" for fact in facts)


@pytest.mark.anyio
async def test_remote_fixture_cleanup_runs_when_consumer_fails(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "cleanup", "streamable_http")
    with pytest.raises(RuntimeError, match="consumer failed"):
        async with remote_fixture(spec) as remote:
            assert remote.process.returncode is None
            raise RuntimeError("consumer failed")
    assert remote.process.returncode is not None


@pytest.mark.anyio
async def test_remote_fixture_cleanup_runs_when_consumer_is_cancelled(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "cancelled", "sse")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(10.0) as deadline:
            async with remote_fixture(spec) as remote:
                deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
    assert remote.process.returncode is not None


@pytest.mark.anyio
async def test_generated_remote_configuration_uses_real_bound_endpoints(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "H", "streamable_http")
    async with remote_fixture(spec) as remote:
        path = write_config(tmp_path, (remote,))
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        assert config["mcp_servers"]["H"]["url"] == remote.url
        group = ExternalMcpGroup()
        try:
            target = next(
                item for item in normalize_mcp_servers(config["mcp_servers"])
                if item["config_key"] == "H"
            )
            assert await group.start([target]) == 1
            result = reply(await group.call_tool("mcp__h__ping", {"value": "config"}))
            assert result.value == "config"
        finally:
            await group.close()


def test_real_client_arguments_replace_daily_mcp_table_without_environment_changes(tmp_path) -> None:
    path = write_config(tmp_path, ())
    arguments = client_arguments(path)
    assert arguments[:3] == (
        sys.executable, str(Path(__file__).resolve().parents[2] / "mind.py"), "-c",
    )
    base = {"mcp_servers": {"daily": {"command": "daily-service"}}, "model": "existing"}
    effective = apply_config_overrides(base, (parse_config_override(arguments[3]),))
    assert "daily" not in effective["mcp_servers"]
    assert effective["model"] == "existing"
    assert "daily" in base["mcp_servers"]
    assert effective["mcp_servers"] == tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]


@pytest.mark.anyio
async def test_close_stall_fixture_is_reclaimed_by_real_stdio_transport(tmp_path) -> None:
    spec = FixtureSpec(tmp_path, "close-stall", mode="close-stall")
    async with asyncio.timeout(10.0):
        async with stdio_session(spec) as session:
            assert reply(await session.call_tool("ping")).call_count == 1
    facts = read_facts(spec.facts_path)
    assert any(fact.event == "fault.injected" for fact in facts)
    assert not any(fact.event == "session.closed" for fact in facts)
