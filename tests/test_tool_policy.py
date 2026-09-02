# -*- coding: utf-8 -*-

from agent.application.views.tool_display import (
    NATIVE_TOOL_NAMES,
    ToolDisplayKind,
    is_two_stage_tool,
    tool_display_spec,
)
from agent.domain.tool_policy import merges_tool_start_event


def test_javascript_display_policy_is_shared_across_layers() -> None:
    spec = tool_display_spec("js_repl")

    assert spec.kind is ToolDisplayKind.JAVASCRIPT
    assert spec.source_field == "code"
    assert is_two_stage_tool("js_repl") is True
    assert merges_tool_start_event("js_repl") is False


def test_native_display_policy_defaults_unknown_tools_to_generic() -> None:
    spec = tool_display_spec("mcp__docs__search")

    assert spec.kind is ToolDisplayKind.GENERIC
    assert is_two_stage_tool("mcp__docs__search") is True
    assert merges_tool_start_event("mcp__docs__search") is True


def test_view_image_display_policy_uses_single_generic_result() -> None:
    spec = tool_display_spec("view_image")

    assert spec.kind is ToolDisplayKind.GENERIC
    assert is_two_stage_tool("view_image") is False
    assert "view_image" not in NATIVE_TOOL_NAMES
