# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import types as mcp_types

from engine.enhance import handlers as enhance_handlers
from mind_app.output import BLOCK_OUTPUT
from mind_app.presentation.models import ProgressView
from mind_app.runtime.tools.enhance_reporter import ToolEnhanceReporter
from mind_nova.requests import chat as chat_request


def test_stream_heal_only_filters_transport_events(monkeypatch) -> None:
    """修复请求层只过滤心跳和空步骤，不执行展示逻辑。"""
    async def fake_streaming(*_args, **_kwargs):
        yield {"type": "ping"}
        yield {"type": "heal.step", "message": ""}
        yield {"type": "heal.step", "message": "locating"}
        yield {"type": "heal.failed", "error": "not found"}
        yield {"type": "heal.result", "result": {"ok": True}}

    monkeypatch.setattr(chat_request, "streaming", fake_streaming)

    async def collect() -> list[dict]:
        return [
            event async for event in chat_request.stream_heal(
                {},
                page_id="page",
                station="android",
                locator="id=title",
                page_dump="<xml />",
                screenshot_base64="image",
                wm_size={"width": 100, "height": 200},
            )
        ]

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == [
        "heal.step",
        "heal.failed",
        "heal.result",
    ]


def test_output_enhance_reporter_preserves_stream_ui_behavior() -> None:
    """增强适配器保持静默记录、可见块输出和状态调用。"""
    output = SimpleNamespace(
        feed=AsyncMock(),
        begin_tool_status=AsyncMock(),
        end_status=AsyncMock(),
    )
    presentation = SimpleNamespace(emit=AsyncMock())
    reporter = ToolEnhanceReporter(
        output,
        presentation,
        tool_name="heal_element",
    )

    async def run() -> None:
        await reporter.record("audit")
        await reporter.display("visible")
        await reporter.begin_status()
        await reporter.end_status()

    asyncio.run(run())

    assert output.feed.await_args_list == [
        (("audit",), {"echo": False, "display": BLOCK_OUTPUT}),
    ]
    presentation.emit.assert_awaited_once_with(ProgressView(
        text="visible",
        source="enhancement",
        tool_name="heal_element",
    ))
    output.begin_tool_status.assert_awaited_once_with()
    output.end_status.assert_awaited_once_with()


def test_heal_enhancement_reports_progress_outside_request_layer(monkeypatch) -> None:
    """修复增强层消费结构化事件并通过 reporter 上报进度。"""
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="heal")],
        structuredContent={
            "ok": True,
            "text": "heal",
            "target": "device-1",
            "data": {
                "serial": "device-1",
                "page_id": "page",
                "station": "android",
                "locator": "id=title",
                "page_dump": "<xml />",
                "screenshot_base64": "image",
                "wm_size": {"width": 100, "height": 200},
            },
        },
        isError=False,
    )
    reporter = SimpleNamespace(
        record=AsyncMock(),
        display=AsyncMock(),
        begin_status=AsyncMock(),
        end_status=AsyncMock(),
    )

    async def fake_heal_license() -> dict[str, bool]:
        return {"enabled": True}

    async def fake_stream_heal(*_args, **_kwargs):
        yield {"type": "heal.step", "message": "locating"}
        yield {
            "type": "heal.result",
            "result": {
                "details": {"reason": "matched nearby element"},
                "new_selector": {
                    "primary": {"by": "id", "value": "title"}
                },
            },
        }

    monkeypatch.setattr(enhance_handlers.Api, "heal_license", fake_heal_license)
    monkeypatch.setattr(enhance_handlers.request, "stream_heal", fake_stream_heal)

    enhanced = asyncio.run(enhance_handlers.enhance_heal_element(
        result,
        {},
        reporter,
    ))

    assert enhanced is not None
    assert enhanced["ok"] is True
    assert enhanced["data"]["locator"] == {"by": "id", "value": "title"}
    assert reporter.display.await_args_list == [
        (("locating",), {}),
        (("matched nearby element",), {}),
    ]
    reporter.begin_status.assert_awaited_once_with()
    reporter.end_status.assert_awaited_once_with()
