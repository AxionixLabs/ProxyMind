# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.protocol import ModelStreamRequest
from agent.adapters.protocol import client as protocol_client_module


class _FixtureStream:
    """提供三类前端共享的固定 mind.chat 事件序列。"""

    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events
        self.end_reason = None
        self.last_event_seq = 0

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            self.last_event_seq = event.event_seq or self.last_event_seq
            yield event
        self.end_reason = "settled"

    async def aclose(self) -> None:
        return None


def _fixture_events() -> tuple[object, ...]:
    common = {
        "proto": "mind.chat",
        "cid": "cid_frontend_fixture",
        "sid": "sid_frontend_fixture",
        "turn_id": "turn_frontend_fixture",
        "presentation_epoch": 1,
        "round": 1,
    }
    return (
        SimpleNamespace(type="turn.start", event_seq=1, **common),
        SimpleNamespace(
            type="text.delta",
            event_seq=2,
            item_id="turn_frontend_fixture:s1",
            item_kind="text",
            item_status="in_progress",
            segment_id="turn_frontend_fixture:s1",
            text="partial",
            **common,
        ),
        SimpleNamespace(
            type="text.done",
            event_seq=3,
            item_id="turn_frontend_fixture:s1",
            item_kind="text",
            item_status="completed",
            segment_id="turn_frontend_fixture:s1",
            final_text="complete",
            **common,
        ),
        SimpleNamespace(
            type="text.meta",
            event_seq=4,
            item_id="turn_frontend_fixture:s1",
            item_kind="text",
            item_status="completed",
            segment_id="turn_frontend_fixture:s1",
            sources=[{"title": "fixture", "url": "https://example.test"}],
            **common,
        ),
        SimpleNamespace(
            type="tool.calls.start",
            event_seq=5,
            batch_id="turn_frontend_fixture:tool-round-1",
            call_ids=("call_frontend_fixture",),
            count=1,
            ready=True,
            **common,
        ),
        SimpleNamespace(
            type="tool.call",
            event_seq=6,
            item_id="call_frontend_fixture",
            item_kind="tool_call",
            item_status="waiting_result",
            call_id="call_frontend_fixture",
            name="shell_command",
            arguments={"command": "Get-Location"},
            **common,
        ),
        SimpleNamespace(
            type="tool.calls.done",
            event_seq=7,
            batch_id="turn_frontend_fixture:tool-round-1",
            call_ids=("call_frontend_fixture",),
            count=1,
            **common,
        ),
        SimpleNamespace(
            type="tool.approval_required",
            event_seq=8,
            item_id="approval_frontend_fixture",
            item_kind="approval",
            item_status="waiting_approval",
            approval_id="approval_frontend_fixture",
            call_id="call_frontend_fixture",
            kind="command",
            status="pending",
            ack=None,
            reason="fixture approval",
            available_decisions=("accept", "decline", "cancel"),
            **common,
        ),
        SimpleNamespace(
            type="tool.output",
            event_seq=9,
            item_id="call_frontend_fixture:output",
            item_kind="tool_output",
            item_status="completed",
            call_id="call_frontend_fixture",
            name="shell_command",
            status="completed",
            payload={
                "ok": True,
                "tool": "shell_command",
                "source": "client",
                "args": {"command": "Get-Location"},
                "text": "Path",
                "attachments": [],
                "data": {"cwd": "D:\\workspace"},
            },
            **common,
        ),
        SimpleNamespace(
            type="turn.logical_settled",
            event_seq=10,
            status="completed",
            next_input=None,
            **common,
        ),
    )


def _request() -> ModelStreamRequest:
    """构造前端 fixture 使用的冻结模型请求。"""
    return ModelStreamRequest(
        cid="cid_frontend_fixture",
        sid="sid_frontend_fixture",
        turn_id="turn_frontend_fixture",
        pref_config={},
        message="fixture",
        tools=(),
    )


async def _consume_frontend(client: MindChatProtocolClient) -> dict[str, object]:
    """模拟任意前端读取 Protocol Client 的统一投影。"""
    stream = client.stream(_request())
    observed = []
    async for event in stream:
        item = stream.current_item
        observed.append({
            "type": event.type,
            "event_seq": event.event_seq,
            "item_id": item.item_id if item is not None else None,
        })
    return {
        "events": observed,
        "items": tuple(
            (
                item.item_id,
                item.item_kind,
                item.item_status,
                item.payload_value(),
            )
            for item in stream.canonical_items
        ),
        "assistant_text": stream.assistant_text,
        "sources": stream.sources,
        "pending_approvals": tuple(
            item.item_id for item in stream.pending_approval_items
        ),
        "cursor": stream.last_event_seq,
        "end_reason": stream.end_reason,
    }


@pytest.mark.anyio
async def test_tui_desktop_and_web_share_one_canonical_projection(monkeypatch) -> None:
    """验证不同前端只依赖同一组事件、Item 和结算游标语义。"""
    expected = None
    for frontend_name in ("tui", "desktop", "web"):
        del frontend_name
        monkeypatch.setattr(
            protocol_client_module,
            "stream_chat",
            lambda *_args, **_kwargs: _FixtureStream(_fixture_events()),
        )
        snapshot = await _consume_frontend(MindChatProtocolClient())
        if expected is None:
            expected = snapshot
        else:
            assert snapshot == expected

    assert expected is not None
    assert expected["assistant_text"] == "complete"
    assert {
        item[1] for item in expected["items"]
    } == {"text", "tool_call", "approval", "tool_output"}
    assert expected["pending_approvals"] == ("approval_frontend_fixture",)
    assert expected["cursor"] == 10
    assert expected["end_reason"] == "settled"
