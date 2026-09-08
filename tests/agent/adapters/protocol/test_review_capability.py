# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.adapters.protocol import client as review_adapter
from agent.adapters.protocol.review_request import build_review_stream_request
from agent.adapters.protocol.client import (
    MindChatProtocolClient,
    ProtocolEventCursorStore,
)
from agent.ports import (
    ModelCapabilityError,
    ReviewCapability,
)
from protocol.client.review import (
    ReviewRequestError,
    ReviewSubmission,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewReceipt,
    MindReviewRequest,
    ReviewCustomTarget,
    ReviewExecutionOptions,
    ReviewOutput,
    ReviewSession,
)
from protocol.schema.stream_events import (
    ReviewCompletedEvent,
    ReviewStartedEvent,
    TurnCompletedEvent,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REQUEST_ID = "review_request_01"
REVIEW_ITEM_ID = "review_item_01"


class _ReviewWireStream:
    """提供可控制终态的 Review wire 流测试替身。"""

    def __init__(self, events: tuple) -> None:
        """绑定按顺序交付的正式事件。"""
        self._events = events
        self.end_reason = "settled"
        self.last_event_seq = len(events)
        self.closed = False
        self.recovery_probe_requested = False

    def __aiter__(self):
        """返回测试事件异步迭代器。"""
        return self._iterate()

    async def _iterate(self):
        """按固定顺序交付事件。"""
        for event in self._events:
            yield event

    async def aclose(self) -> None:
        """记录传输已关闭。"""
        self.closed = True

    def request_recovery_probe(self) -> None:
        """记录已请求立即恢复核对。"""
        self.recovery_probe_requested = True


def _wire_request(*, patch: str = "diff --git a/a.py b/a.py\n") -> MindReviewRequest:
    """构造可由客户端持久化的 inline Review 请求。"""
    return MindReviewRequest(
        request_id=REQUEST_ID,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Review the current changes."),
        workspace=ClientReviewWorkspace.create(patch=patch),
        execution=ReviewExecutionOptions(
            llm_conf={"primary": {"model": "test-model"}},
            metadata={"cid": CID, "sid": SID},
        ),
    )


def _submission(events: tuple) -> ReviewSubmission:
    """构造已确认登记的 Review 回执和观察流。"""
    return ReviewSubmission(
        receipt=MindReviewReceipt(
            request_id=REQUEST_ID,
            status="accepted",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            review_session=ReviewSession(cid=CID, sid=SID),
            delivery="inline",
        ),
        events=_ReviewWireStream(events),
    )


def _events(*, include_turn_terminal: bool = True) -> tuple:
    """构造有序 Review Item 事件及可选 Turn 终态。"""
    request = _wire_request()
    events = (
        ReviewStartedEvent(
            type="review.started",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=1,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="in_progress",
            review_item_id=REVIEW_ITEM_ID,
            status="in_progress",
            target=request.target,
            workspace_revision=request.workspace.revision,
            prompt_version="mind-review/1",
        ),
        ReviewCompletedEvent(
            type="review.completed",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=2,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="completed",
            review_item_id=REVIEW_ITEM_ID,
            status="completed",
            output=ReviewOutput(
                findings=(),
                overall_correctness="correct",
                overall_explanation="No findings.",
                overall_confidence_score=0.9,
            ),
        ),
    )
    if not include_turn_terminal:
        return events
    return events + (
        TurnCompletedEvent(
            type="turn.completed",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=3,
            status="completed",
            last_event_seq=3,
            completed_at=1.0,
        ),
    )


@pytest.mark.anyio
async def test_review_capability_submits_then_validates_terminal_stream(
    monkeypatch,
) -> None:
    """确保 Review 回执确认后才交付已对账的模型流。"""
    local_request = build_review_stream_request(_wire_request())
    submit = AsyncMock(return_value=_submission(_events()))
    monkeypatch.setattr(review_adapter, "_submit_review", submit)
    cursors = ProtocolEventCursorStore()
    client = MindChatProtocolClient(cursors)

    stream = await client.review(local_request)
    delivered = [event async for event in stream]

    assert isinstance(client, ReviewCapability)
    assert [event.type for event in delivered] == [
        "review.started",
        "review.completed",
        "turn.completed",
    ]
    assert cursors.current(cid=CID, sid=SID) == 3
    submitted_request = submit.await_args.args[0]
    assert submitted_request.request_payload() == _wire_request().request_payload()


@pytest.mark.anyio
async def test_review_capability_rejects_empty_custom_before_http(
    monkeypatch,
) -> None:
    """确保无快照且无只读工具时不发送 HTTP 请求。"""
    submit = AsyncMock()
    monkeypatch.setattr(review_adapter, "_submit_review", submit)
    request = build_review_stream_request(_wire_request(patch=""))

    with pytest.raises(ModelCapabilityError) as raised:
        await MindChatProtocolClient().review(request)

    assert raised.value.code == "review_code_context_unavailable"
    assert raised.value.details["submission_unknown"] is False
    submit.assert_not_awaited()


@pytest.mark.anyio
async def test_review_capability_maps_unknown_submission_to_recovery(
    monkeypatch,
) -> None:
    """确保无法确认登记结果时保留未知提交分类。"""
    monkeypatch.setattr(
        review_adapter,
        "_submit_review",
        AsyncMock(side_effect=ReviewRequestError(
            "connection lost",
            retryable=True,
            submission_unknown=True,
        )),
    )

    with pytest.raises(ModelCapabilityError) as raised:
        await MindChatProtocolClient().review(
            build_review_stream_request(_wire_request())
        )

    assert raised.value.code == "review_submission_unknown"
    assert raised.value.retryable is True
    assert raised.value.details["submission_unknown"] is True


@pytest.mark.anyio
async def test_review_stream_eof_before_turn_terminal_requires_recovery(
    monkeypatch,
) -> None:
    """确保 Review Item 完成不会单独解除 Turn 结算门禁。"""
    request = build_review_stream_request(_wire_request())
    monkeypatch.setattr(
        review_adapter,
        "_submit_review",
        AsyncMock(return_value=_submission(
            _events(include_turn_terminal=False)
        )),
    )
    cursors = ProtocolEventCursorStore()
    stream = await MindChatProtocolClient(cursors).review(request)

    with pytest.raises(ModelCapabilityError) as raised:
        _ = [event async for event in stream]

    assert raised.value.code == "review_observation_incomplete"
    assert raised.value.details["reconciliation_required"] is True
    assert raised.value.details["submission_unknown"] is False
    assert cursors.current(cid=CID, sid=SID) == 0


@pytest.mark.anyio
async def test_review_stream_rejects_conflicting_turn_terminal(
    monkeypatch,
) -> None:
    """确保 Turn 终态不能与 Review Item 终态矛盾。"""
    events = list(_events())
    events[-1] = TurnCompletedEvent(
        type="turn.completed",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=3,
        status="failed",
        last_event_seq=3,
        completed_at=1.0,
        error="conflicting terminal",
    )
    monkeypatch.setattr(
        review_adapter,
        "_submit_review",
        AsyncMock(return_value=_submission(tuple(events))),
    )
    cursors = ProtocolEventCursorStore()
    stream = await MindChatProtocolClient(cursors).review(
        build_review_stream_request(_wire_request())
    )

    with pytest.raises(ModelCapabilityError) as raised:
        _ = [event async for event in stream]

    assert raised.value.code == "review_protocol_error"
    assert raised.value.details["reconciliation_required"] is True
    assert cursors.current(cid=CID, sid=SID) == 0


@pytest.mark.anyio
async def test_review_interrupt_probes_active_observation(monkeypatch) -> None:
    """确保 Review 中断前后均会唤醒当前权威状态核对。"""
    wire_stream = _ReviewWireStream(())
    submission = _submission(())
    submission = ReviewSubmission(
        receipt=submission.receipt,
        events=wire_stream,
    )
    monkeypatch.setattr(
        review_adapter,
        "_submit_review",
        AsyncMock(return_value=submission),
    )
    interrupt = AsyncMock(return_value=SimpleNamespace(
        status="accepted",
        request_id="interrupt_review_01",
        turn_id=TURN_ID,
        client_message_id=None,
    ))
    monkeypatch.setattr(review_adapter, "_interrupt_turn", interrupt)
    client = MindChatProtocolClient()
    stream = await client.review(
        build_review_stream_request(_wire_request())
    )

    receipt = await client.interrupt_turn(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
    )
    await stream.aclose()

    assert receipt.status == "accepted"
    assert wire_stream.recovery_probe_requested is True
    interrupt.assert_awaited_once()
