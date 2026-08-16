# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from mind_app.mcp.tools import build_tool_context
from mind_app.runtime.tools.mode_policy import filter_mode_tools


def _tool(name: str, **meta):
    return {"name": name, "meta": meta}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "app",
            [
                "coding",
                "runtime",
                "inspect",
                "device",
                "framix",
                "memrix",
                "screen",
                "audio",
                "view",
                "plan",
                "plan_steps",
                "spawn_agent",
                "external",
            ],
        ),
        (
            "api",
            [
                "coding",
                "security",
                "runtime",
                "inspect",
                "nexus",
                "view",
                "plan",
                "spawn_agent",
                "external",
            ],
        ),
    ],
)
def test_filter_mode_tools_applies_mode_policy(mode, expected) -> None:
    tools = [
        _tool("hidden", hidden=True),
        _tool("hidden_external", hidden=True, external=True),
        _tool("provider_coding", domain="coding"),
        _tool("coding", client_builtin=True, domain="coding"),
        _tool("security", domain="common", **{"class": "security"}),
        _tool("runtime", domain="common", **{"class": "runtime"}),
        _tool("inspect", domain="common", **{"class": "inspect"}),
        _tool("device", domain="device"),
        _tool("framix", domain="bench", **{"class": "framix"}),
        _tool("memrix", domain="bench", **{"class": "memrix"}),
        _tool("nexus", domain="bench", **{"class": "nexus"}),
        _tool("screen", domain="media", **{"class": "scrcpy"}),
        _tool("audio", domain="media", **{"class": "audio"}),
        _tool(
            "view",
            client_builtin=True,
            domain="client",
            **{"class": "view"},
        ),
        _tool(
            "plan",
            client_builtin=True,
            domain="client",
            **{"class": "plan"},
        ),
        _tool(
            "plan_steps",
            client_builtin=True,
            domain="client",
            **{"class": "loop"},
        ),
        _tool(
            "spawn_agent",
            client_builtin=True,
            domain="client",
            **{"class": "agent"},
        ),
        _tool("external", external=True, domain="foreign"),
        _tool("unclassified"),
    ]

    filtered = filter_mode_tools(mode, tools)

    assert [tool["name"] for tool in filtered] == expected


def test_filter_mode_tools_supports_function_and_non_tool_items() -> None:
    function_tool = {
        "type": "function",
        "function": {"name": "external_tool"},
        "meta": {"external": True},
    }
    message_item = {"type": "message", "content": "context"}

    filtered = filter_mode_tools("app", [function_tool, message_item])

    assert filtered == [function_tool, message_item]
    assert filtered[0] is not function_tool
    assert filtered[0]["meta"] is not function_tool["meta"]


def test_filter_mode_tools_default_only_removes_plan_steps() -> None:
    tools = [
        _tool("hidden", hidden=True),
        _tool("provider", domain="unknown"),
        _tool("external", external=True),
        _tool("coding", client_builtin=True, domain="coding"),
        _tool(
            "plan_steps",
            client_builtin=True,
            domain="client",
            **{"class": "loop"},
        ),
        _tool(
            "spawn_agent",
            client_builtin=True,
            domain="client",
            **{"class": "agent"},
        ),
    ]

    filtered = filter_mode_tools(None, tools)

    assert [tool["name"] for tool in filtered] == [
        "provider",
        "external",
        "coding",
        "spawn_agent",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["app", "api"])
async def test_filter_mode_tools_preserves_external_mcp_tools(mode) -> None:
    external = SimpleNamespace(
        tools={
            "mcp__docs__lookup": mcp_types.Tool(
                name="lookup",
                description="Look up documentation.",
                inputSchema={"type": "object"},
            )
        }
    )

    context = await build_tool_context(external_group=external)
    filtered = filter_mode_tools(mode, context.tools)

    assert [tool["name"] for tool in filtered] == ["mcp__docs__lookup"]
    assert filtered[0]["meta"] == {
        "external": True,
        "server": "docs",
        "transport": "external",
        "effect": {
            "scope": "external",
            "class": "non_replayable",
            "replay_policy": "manual",
        },
    }


@pytest.mark.parametrize("mode", ["chat", "fast", "xtra", "unknown"])
def test_filter_mode_tools_rejects_unknown_mode_for_empty_catalog(mode) -> None:
    with pytest.raises(ValueError, match="Invalid tool filter mode"):
        filter_mode_tools(mode, [])
