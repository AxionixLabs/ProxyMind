# -*- coding: utf-8 -*-

import asyncio
import logging
from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from mind_app.mcp import group as mcp_group
from mind_app.mcp.group import ExternalMcpGroup, open_optional_external_mcp_group
from mind_app.mcp.status import ExternalMcpStatus


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


@pytest.mark.anyio
async def test_external_mcp_close_hides_only_sdk_termination_warning(caplog) -> None:
    sdk_logger = logging.getLogger("mcp.client.streamable_http")
    group = ExternalMcpGroup()

    def emit_close_logs() -> None:
        sdk_logger.warning("Session termination failed: All connection attempts failed")
        sdk_logger.warning("unrelated SDK warning")

    with caplog.at_level(logging.WARNING, logger=sdk_logger.name):
        async with group:
            group._exit_stack.callback(emit_close_logs)

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

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        state["active"] += 1
        state["maximum"] = max(state["maximum"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return server["name"], 1, 1

    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    async with open_optional_external_mcp_group(servers, status=status) as group:
        assert group is not None

    snapshot = status.snapshot()
    assert state["maximum"] == mcp_group.EXTERNAL_MCP_CONNECT_CONCURRENCY
    assert [item["state"] for item in snapshot["items"]] == ["ready"] * 6


@pytest.mark.anyio
async def test_external_mcp_timeout_starts_after_concurrency_slot_is_acquired(
    monkeypatch,
) -> None:
    servers = _servers(2, startup_timeout_sec=0.02)
    status = ExternalMcpStatus(servers)

    async def preflight(_server) -> None:
        return None

    async def connect(_group, server) -> tuple[str, int, int]:
        if server["name"] == "server-0":
            await asyncio.sleep(0.05)
        return server["name"], 1, 1

    monkeypatch.setattr(mcp_group, "EXTERNAL_MCP_CONNECT_CONCURRENCY", 1)
    monkeypatch.setattr(mcp_group, "preflight_server", preflight)
    monkeypatch.setattr(ExternalMcpGroup, "connect_with_alias", connect)

    async with open_optional_external_mcp_group(servers, status=status) as group:
        assert group is not None

    first, second = status.snapshot()["items"]
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

    async def establish(_params, _session_params):
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack()

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
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
    async with group:
        with pytest.raises(RuntimeError, match="tool discovery failed"):
            await group.connect_with_alias({
                "name": "docs",
                "transport": "streamable_http",
                "url": "https://docs.example.test/mcp",
            })

    assert state["closed"] == 1


@pytest.mark.anyio
async def test_external_mcp_success_transfers_resources_to_group(
    monkeypatch,
) -> None:
    state = {"closed": 0}

    class SessionStack(object):
        async def aclose(self) -> None:
            state["closed"] += 1

    async def establish(_params, _session_params):
        server_info = SimpleNamespace(version="1", websiteUrl=None, icons=None)
        return server_info, object(), SessionStack()

    async def collect(
        _server_info,
        _session,
        *,
        transport=None,
        rules=None,
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
    async with group:
        alias, tool_count, discovered_count = await group.connect_with_alias({
            "name": "docs",
            "transport": "streamable_http",
            "url": "https://docs.example.test/mcp",
        })
        assert (alias, tool_count, discovered_count) == ("docs", 0, 0)
        assert state["closed"] == 0

    assert state["closed"] == 1


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
    )

    assert discovered_count == 3
    assert list(tools) == [
        "mcp__zentao__get_bug",
        "mcp__zentao__list_projects",
    ]
