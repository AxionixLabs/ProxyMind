# -*- coding: utf-8 -*-

import pytest
from mcp import types as mcp_types

from mind_app.mcp.tool_result import (
    normalize_call_tool_result,
    normalize_tool_fields,
)
from mind_app.runtime.execution import AgentContext, ToolInvocation, TurnContext
from mind_app.runtime.tools.run import run_tool_step
from mind_core.permissions import preset_permissions


def test_normalize_external_structured_result_keeps_text_and_data() -> None:
    result = mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(type="text", text="first"),
            mcp_types.TextContent(type="text", text="second"),
        ],
        structuredContent={"answer": 42},
    )

    normalized = normalize_call_tool_result(result)

    assert normalized.fields == {
        "ok": True,
        "text": "first\nsecond",
        "attachments": [],
        "data": {"answer": 42},
    }
    assert normalized.display_text == "first\nsecond"


def test_normalize_pure_structured_result_uses_json_display() -> None:
    result = mcp_types.CallToolResult(
        content=[],
        structuredContent={"answer": 42},
    )

    normalized = normalize_call_tool_result(result)

    assert normalized.fields["text"] == ""
    assert normalized.fields["data"] == {"answer": 42}
    assert '"answer": 42' in normalized.display_text


def test_normalize_standard_envelope_preserves_project_fields() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="visible output")],
        structuredContent={
            "ok": True,
            "text": "structured output",
            "attachments": [{"kind": "file", "path": "result.txt"}],
            "data": {"count": 2},
            "target": "device-1",
        },
    )

    normalized = normalize_call_tool_result(result)

    assert normalized.fields["text"] == "visible output"
    assert normalized.fields["data"] == {"count": 2}
    assert normalized.fields["target"] == "device-1"
    assert normalized.fields["attachments"] == [
        {"kind": "file", "path": "result.txt"}
    ]


def test_normalize_mixed_media_and_resource_content() -> None:
    result = mcp_types.CallToolResult(content=[
        mcp_types.ImageContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
        mcp_types.ResourceLink(
            type="resource_link",
            name="report",
            uri="file:///tmp/report.txt",
            mimeType="text/plain",
        ),
        mcp_types.EmbeddedResource(
            type="resource",
            resource=mcp_types.TextResourceContents(
                uri="file:///tmp/details.txt",
                mimeType="text/plain",
                text="resource details",
            ),
        ),
    ])

    normalized = normalize_call_tool_result(result)

    assert normalized.fields["text"] == "resource details"
    assert [item["kind"] for item in normalized.fields["attachments"]] == [
        "image",
        "resource_link",
        "embedded_resource",
    ]
    assert normalized.fields["attachments"][0]["data_url"].startswith(
        "data:image/png;base64,"
    )


def test_media_summary_survives_post_enhancement_normalization() -> None:
    initial = normalize_call_tool_result(mcp_types.CallToolResult(content=[
        mcp_types.ImageContent(
            type="image",
            data="aW1hZ2U=",
            mimeType="image/png",
        ),
    ]))

    final = normalize_tool_fields(
        initial.fields,
        ok=initial.ok,
        display_fallback=initial.display_text,
    )

    assert final.display_text == "Image output (image/png)."
    assert "aW1hZ2U=" not in final.display_text


def test_normalize_empty_error_result_remains_visible() -> None:
    result = mcp_types.CallToolResult(content=[], isError=True)

    normalized = normalize_call_tool_result(result)

    assert not normalized.ok
    assert normalized.fields["text"] == ""
    assert normalized.display_text == "Tool failed with no textual error details."


@pytest.mark.anyio
async def test_external_structured_result_is_visible_after_tool_run() -> None:
    calls = []

    class Session(object):
        async def call_tool(self, *args, **kwargs):
            calls.append((args, kwargs))
            return mcp_types.CallToolResult(
                content=[],
                structuredContent={"answer": 42},
            )

    class Status(object):
        async def begin_tool_status(self) -> None:
            return None

        async def end_status(self) -> None:
            return None

    turn_context = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        mode="xtra",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
    )

    tool_run = await run_tool_step(
        Session(),
        status_control=Status(),
        presentation=object(),
        tools=[{"name": "mcp__docs__lookup"}],
        invocation=ToolInvocation(
            turn=turn_context,
            call_id="call_test",
            name="mcp__docs__lookup",
            arguments={"query": "answer"},
        ),
        pref_config={},
    )

    assert tool_run.fields["data"] == {"answer": 42}
    assert '"answer": 42' in tool_run.text
    assert calls[0][0] == ("mcp__docs__lookup", {"query": "answer"})
    assert calls[0][1]["call_id"] == "call_test"
    assert calls[0][1]["turn_context"] is turn_context
    assert calls[0][1]["pref_config"] == {}
    assert "cid" not in calls[0][1]
    assert "sid" not in calls[0][1]
    assert "permissions" not in calls[0][1]


if __name__ == '__main__':
    pass
