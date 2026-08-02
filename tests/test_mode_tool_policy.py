# -*- coding: utf-8 -*-

import pytest

from mind_app.runtime.tools.mode_policy import filter_mode_tools


def _tool(name: str, **meta):
    return {"name": name, "meta": meta}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "chat",
            ["coding", "device", "screen", "view", "plan", "spawn_agent", "external"],
        ),
        ("fast", ["coding", "security", "view", "plan", "spawn_agent", "external"]),
        ("xtra", ["coding", "view", "plan", "spawn_agent", "external"]),
    ],
)
def test_filter_mode_tools_applies_mode_policy(mode, expected) -> None:
    tools = [
        _tool("hidden", hidden=True),
        _tool("coding", domain="coding"),
        _tool("security", domain="common", **{"class": "security"}),
        _tool("device", domain="device"),
        _tool("screen", domain="media", **{"class": "scrcpy"}),
        _tool("view", domain="client", **{"class": "view"}),
        _tool("plan", domain="client", **{"class": "plan"}),
        _tool("spawn_agent", domain="client", **{"class": "spawn"}),
        _tool("external", external=True),
    ]

    filtered = filter_mode_tools(mode, tools)

    assert [tool["name"] for tool in filtered] == expected


def test_filter_mode_tools_supports_function_and_non_tool_items() -> None:
    function_tool = {
        "type": "function",
        "function": {"name": "apply_patch"},
        "meta": {"domain": "coding"},
    }
    message_item = {"type": "message", "content": "context"}

    filtered = filter_mode_tools("xtra", [function_tool, message_item])

    assert filtered == [function_tool, message_item]
    assert filtered[0] is not function_tool
    assert filtered[0]["meta"] is not function_tool["meta"]


def test_filter_mode_tools_rejects_unknown_mode_for_empty_catalog() -> None:
    with pytest.raises(ValueError, match="Invalid tool filter mode"):
        filter_mode_tools("unknown", [])
