# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from agent.adapters.protocol import client as review_adapter
from agent.adapters.protocol.client import (
    MindChatProtocolClient,
    ProtocolEventCursorStore,
)
from agent.ports import (
    ModelCapabilityError,
    ReviewCapability,
    ReviewObservationCapability,
)
from agent.protocol import ReviewStreamRequest
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
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewStartedEvent,
    TurnCompletedEvent,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REQUEST_ID = "review_request_01"
REVIEW_ITEM_ID = "review_item_01"


def _tools() -> tuple[dict, ...]:
    """构造最小严格只读工具目录。"""
    return ({
        "name": "exec_command",
        "description": "Read a UTF-8 repository file.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },)


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


def _wire_request(*, patch: str = "") -> MindReviewRequest:
    """构造可由客户端持久化的 inline Review 请求。"""
    return MindReviewRequest(
        session_mode="existing",
        request_id=REQUEST_ID,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Review the current changes."),
        workspace=ClientReviewWorkspace.create(patch=patch),
        execution=ReviewExecutionOptions(
            llm_conf={"primary": {"model": "test-model"}},
            tools=_tools(),
            metadata={"cid": CID, "sid": SID},
        ),
    )


def _stream_request(*, patch: str = "") -> ReviewStreamRequest:
    """从正式 wire 载荷构造本地冻结 Review 请求。"""
    return ReviewStreamRequest.from_dict(
        _wire_request(patch=patch).request_payload()
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
    local_request = _stream_request()
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
    assert len(stream.canonical_items) == 1
    assert stream.canonical_items[0].item_kind == "review"
    assert stream.canonical_items[0].item_status == "completed"
    submitted_request = submit.await_args.args[0]
    assert submitted_request.request_payload() == _wire_request().request_payload()


@pytest.mark.anyio
async def test_review_observation_replays_without_resubmitting(
    monkeypatch,
) -> None:
    """确保冷恢复只 attach/replay，并继续归约 Review Canonical Item。"""
    local_request = _stream_request()
    observe = Mock(return_value=_ReviewWireStream(_events()))
    submit = AsyncMock()
    monkeypatch.setattr(review_adapter, "_observe_turn", observe)
    monkeypatch.setattr(review_adapter, "_submit_review", submit)
    client = MindChatProtocolClient()

    stream = client.observe_review(
        local_request,
        after_event_seq=0,
        replay_target_seq=3,
    )
    observed_items = []
    delivered = []
    async for event in stream:
        delivered.append(event.type)
        observed_items.append(
            stream.current_item.item_id
            if stream.current_item is not None
            else None
        )

    assert isinstance(client, ReviewObservationCapability)
    assert delivered == [
        "review.started",
        "review.completed",
        "turn.completed",
    ]
    assert observed_items == [REVIEW_ITEM_ID, REVIEW_ITEM_ID, None]
    assert stream.canonical_items[0].item_kind == "review"
    assert stream.canonical_items[0].item_status == "completed"
    observe.assert_called_once()
    assert observe.call_args.kwargs["initial_event_seq"] == 0
    assert observe.call_args.kwargs["replay_target_seq"] == 3
    submit.assert_not_awaited()


@pytest.mark.anyio
async def test_review_capability_submits_empty_workspace_with_read_only_tools(
    monkeypatch,
) -> None:
    """确保规范空 workspace 由冻结只读工具提供代码观察能力。"""
    submit = AsyncMock(return_value=_submission(_events()))
    monkeypatch.setattr(review_adapter, "_submit_review", submit)
    request = _stream_request()

    stream = await MindChatProtocolClient().review(request)
    delivered = [event async for event in stream]

    assert delivered[-1].type == "turn.completed"
    submit.assert_awaited_once()


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
            _stream_request()
        )

    assert raised.value.code == "review_submission_unknown"
    assert raised.value.retryable is True
    assert raised.value.details["submission_unknown"] is True


@pytest.mark.anyio
async def test_review_stream_eof_before_turn_terminal_requires_recovery(
    monkeypatch,
) -> None:
    """确保 Review Item 完成不会单独解除 Turn 结算门禁。"""
    request = _stream_request()
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
        _stream_request()
    )

    with pytest.raises(ModelCapabilityError) as raised:
        _ = [event async for event in stream]

    assert raised.value.code == "review_protocol_error"
    assert raised.value.details["reconciliation_required"] is True
    assert cursors.current(cid=CID, sid=SID) == 0


@pytest.mark.anyio
async def test_review_stream_rejects_a_second_terminal_from_interrupt_race(
    monkeypatch,
) -> None:
    """确保中断与重连竞争不能提交两个 Review Item 终态。"""
    request = _stream_request()
    started = _events()[0]
    cancelled = ReviewCancelledEvent(
        type="review.cancelled",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=2,
        item_id=REVIEW_ITEM_ID,
        item_kind="review",
        item_status="cancelled",
        review_item_id=REVIEW_ITEM_ID,
        status="cancelled",
        reason="interrupted",
    )
    duplicate = ReviewCancelledEvent(
        type="review.cancelled",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=3,
        item_id=REVIEW_ITEM_ID,
        item_kind="review",
        item_status="cancelled",
        review_item_id=REVIEW_ITEM_ID,
        status="cancelled",
        reason="interrupt replay race",
    )
    monkeypatch.setattr(
        review_adapter,
        "_submit_review",
        AsyncMock(return_value=_submission((started, cancelled, duplicate))),
    )
    cursors = ProtocolEventCursorStore()
    stream = await MindChatProtocolClient(cursors).review(request)

    with pytest.raises(ModelCapabilityError) as raised:
        _ = [event async for event in stream]

    assert raised.value.code == "review_protocol_error"
    assert "unique" in str(raised.value)
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
        _stream_request()
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
