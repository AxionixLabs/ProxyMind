# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types as mcp_types

from agent.application.tools.definitions import (
    BuiltinTool,
    ClientTool,
)
from agent.application.tools.results import (
    LocalToolResult,
    LocalToolSource,
)
from infrastructure.mcp.local_tool_registry import ToolRegistry
from infrastructure.mcp.composite_session import CompositeToolSession
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.policies import preset_permissions


def _local_result(
    tool: str,
    source: LocalToolSource = LocalToolSource.CLIENT,
) -> LocalToolResult:
    return LocalToolResult(
        tool=tool,
        source=source,
        ok=True,
        text="done",
    )


def _mcp_result() -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="done")],
    )


def _child_turn() -> TurnContext:
    agent = AgentContext.root("sid_root").child(
        "explore",
        "inspect",
        agent_id="agent_child",
    )
    return TurnContext.create(
        agent=agent,
        cid="cid_child",
        sid="sid_child",
        source="subagent",
        pref_config={},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        turn_id="turn_child",
    )


def _client_tool(name: str = "inspect_context") -> ClientTool:
    async def handler(arguments, runtime):
        return _local_result(name)

    return ClientTool(
        name=name,
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )


def _builtin_tool(name: str = "request_permissions") -> BuiltinTool:
    async def handler(arguments, runtime):
        return _local_result(name, LocalToolSource.BUILTIN)

    return BuiltinTool(
        name=name,
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )


def test_local_tool_registry_rejects_mixed_sources() -> None:
    with pytest.raises(ValueError, match="cannot mix tool sources"):
        ToolRegistry([_client_tool(), _builtin_tool()])


def test_local_tool_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate client tool"):
        ToolRegistry([_client_tool(), _client_tool()])


def test_local_tool_registry_owns_source_metadata() -> None:
    tool = ClientTool(
        name="inspect_context",
        description="test",
        input_schema={"type": "object"},
        handler=_client_tool().handler,
        meta={"client_builtin": False, "title": "Inspect context"},
    )

    listed = ToolRegistry([tool]).list_tools().tools

    assert listed[0].meta == {
        "client_builtin": True,
        "title": "Inspect context",
    }


def test_local_tool_result_freezes_structured_fields() -> None:
    data = {"items": [{"value": 1}]}
    result = LocalToolResult(
        tool="inspect_context",
        source=LocalToolSource.CLIENT,
        ok=True,
        text="done",
        data=data,
    )

    data["items"][0]["value"] = 2

    assert result.data["items"][0]["value"] == 1


def test_local_tool_result_rejects_non_json_data() -> None:
    with pytest.raises(TypeError, match="non-serializable"):
        LocalToolResult(
            tool="inspect_context",
            source=LocalToolSource.CLIENT,
            ok=True,
            text="done",
            data={"value": object()},
        )


@pytest.mark.anyio
async def test_local_tool_registry_rejects_result_identity_mismatch() -> None:
    async def handler(arguments, runtime):
        return _local_result("other_tool")

    registry = ToolRegistry([ClientTool(
        name="inspect_context",
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )])

    with pytest.raises(ValueError, match="identity does not match"):
        await registry.call_tool(
            SimpleNamespace(),
            "inspect_context",
            turn_context=_child_turn(),
            pref_config={},
        )


@pytest.mark.anyio
async def test_client_tool_receives_complete_turn_context() -> None:
    received = []

    async def handler(arguments, runtime):
        received.append((arguments, runtime))
        return _local_result("inspect_context")

    registry = ToolRegistry([ClientTool(
        name="inspect_context",
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )])
    session = CompositeToolSession(client_registry=registry)
    turn = _child_turn()
    pref_config = {"primary": {"model": "test-model"}}

    result = await session.call_tool(
        "inspect_context",
        {"value": 1},
        call_id="call_child",
        turn_context=turn,
        pref_config=pref_config,
    )

    assert result.isError is False
    arguments, runtime = received[0]
    assert arguments == {"value": 1}
    assert runtime.turn_context is turn
    assert runtime.turn_context.agent.agent_id == "agent_child"
    assert runtime.turn_context.agent.root_session_id == "sid_root"
    assert runtime.turn_context.agent.depth == 1
    assert runtime.pref_config == pref_config
    assert runtime.pref_config is not pref_config
    assert runtime.call_id == "call_child"


@pytest.mark.anyio
async def test_client_tool_rejects_missing_turn_context() -> None:
    handler = AsyncMock(return_value=_local_result("inspect_context"))
    registry = ToolRegistry([ClientTool(
        name="inspect_context",
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )])
    session = CompositeToolSession(client_registry=registry)

    with pytest.raises(TypeError, match="turn context is required"):
        await session.call_tool(
            "inspect_context",
            {},
            pref_config={},
        )

    handler.assert_not_awaited()


@pytest.mark.anyio
async def test_builtin_tool_uses_separate_registry_and_wire_metadata() -> None:
    received = []

    async def handler(arguments, runtime):
        received.append((arguments, runtime))
        return _local_result(
            "request_permissions",
            LocalToolSource.BUILTIN,
        )

    registry = ToolRegistry([BuiltinTool(
        name="request_permissions",
        description="test",
        input_schema={"type": "object"},
        handler=handler,
    )])
    session = CompositeToolSession(builtin_registry=registry)

    listed = await session.list_tools()
    assert listed.tools[0].name == "request_permissions"
    assert listed.tools[0].meta["builtin"] is True

    result = await session.call_tool(
        "request_permissions",
        {"permissions": {}},
        call_id="call_builtin",
        turn_context=_child_turn(),
        pref_config={"primary": {"model": "test-model"}},
    )

    assert result.isError is False
    assert received[0][0] == {"permissions": {}}
    assert received[0][1].call_id == "call_builtin"


@pytest.mark.anyio
async def test_external_tool_does_not_receive_turn_context() -> None:
    external = SimpleNamespace(
        tools={"mcp__docs__lookup": object()},
        call_tool=AsyncMock(return_value=_mcp_result()),
    )
    session = CompositeToolSession(external_group=external)

    await session.call_tool(
        "mcp__docs__lookup",
        {"query": "context"},
        meta={"server": "docs"},
        call_id="call_external",
        turn_context=_child_turn(),
        pref_config={"local": True},
    )

    external.call_tool.assert_awaited_once_with(
        "mcp__docs__lookup",
        {"query": "context"},
        read_timeout_seconds=None,
        progress_callback=None,
        meta={"server": "docs"},
    )


def test_composite_session_projects_external_tool_display_name() -> None:
    external = SimpleNamespace(
        tools={
            "mcp__docs__lookup": SimpleNamespace(name="lookup"),
        },
    )
    session = CompositeToolSession(external_group=external)

    assert session.display_name_for_tool("mcp__docs__lookup") == "lookup"
    assert session.display_name_for_tool("apply_patch") == "apply_patch"


@pytest.mark.anyio
async def test_service_tool_does_not_receive_turn_context() -> None:
    service = SimpleNamespace(
        call_tool=AsyncMock(return_value=_mcp_result()),
    )
    session = CompositeToolSession(service_session=service)

    await session.call_tool(
        "service_tool",
        {"value": 1},
        meta={"domain": "service"},
        call_id="call_service",
        turn_context=_child_turn(),
        pref_config={"local": True},
    )

    service.call_tool.assert_awaited_once_with(
        "service_tool",
        {"value": 1},
        read_timeout_seconds=None,
        progress_callback=None,
        meta={"domain": "service"},
    )
