# -*- coding: utf-8 -*-

import pytest

from agent.adapters.protocol.approval_reviews import (
    ApprovalReviewEventHandler,
    approval_review_record,
)
from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.domain.approvals import (
    ApprovalActionKind,
    ApprovalReviewRiskLevel,
    ApprovalReviewStatus,
    ApprovalReviewUserAuthorization,
)
from agent.ports import (
    ApprovalReviewCompleted,
    ApprovalReviewStarted,
    OutputSurfaceContext,
)
from protocol.schema.stream_events import (
    MarkerEvent,
    ToolApprovalReviewCompletedEvent,
    ToolApprovalReviewStartedEvent,
    parse_stream_event,
)


class _ReviewFeed:
    """记录协议适配器交付的评审事实与清理请求。"""

    def __init__(self) -> None:
        self.records = []
        self.cleared: list[tuple[str, str]] = []

    async def record_review(self, update) -> None:
        self.records.append(update)

    async def completed_approval_review(self, identity):
        return next(
            (
                item
                for item in reversed(self.records)
                if item.identity.approval_id == identity.approval_id
                and item.identity.action_id == identity.action_id
                and item.terminal
            ),
            None,
        )

    async def clear_approval_reviews(
        self,
        session_id: str,
        run_id: str,
    ) -> None:
        self.cleared.append((session_id, run_id))


class _Sink:
    """记录评审 activity 和 presentation 投影。"""

    def __init__(self) -> None:
        self.items: list[object] = []

    async def emit(self, item: object) -> None:
        self.items.append(item)


def _handler(
    feed: _ReviewFeed,
) -> tuple[ApprovalReviewEventHandler, _Sink]:
    activity_sink = _Sink()
    context = OutputSurfaceContext(
        surface_id="surface-review",
        cid="cid-review",
        sid="sid-review",
        turn_id="turn-review",
        agent_id="root",
    )
    return (
        ApprovalReviewEventHandler(
            feed,
            session_id="sid-review",
            run_id="turn-review",
            activity=TurnActivityProjector(context, activity_sink),
        ),
        activity_sink,
    )


def _review_event(
    event_type: str,
    status: str,
    *,
    event_seq: int,
    presentation_epoch: int = 1,
):
    payload = {
        "type": event_type,
        "proto": "mind.chat",
        "cid": "cid-review",
        "sid": "sid-review",
        "turn_id": "turn-review",
        "event_seq": event_seq,
        "presentation_epoch": presentation_epoch,
        "item_id": "review-1",
        "item_kind": "approval",
        "item_status": {
            "in_progress": "in_progress",
            "approved": "completed",
            "denied": "completed",
            "timed_out": "failed",
            "aborted": "cancelled",
        }[status],
        "review_id": "review-1",
        "approval_id": "approval-1",
        "call_id": "call-1",
        "target_item_id": "call-1",
        "kind": "write_stdin",
        "action": {
            "session_id": "terminal-1",
            "input": "continue\n",
        },
        "started_at_ms": 100,
        "review": {"status": status},
    }
    if status in {"approved", "denied"}:
        payload["review"] = {
            "status": status,
            "risk_level": "low",
            "user_authorization": "high",
            "rationale": "The action matches the current request.",
        }
    elif status == "timed_out":
        payload["review"] = {
            "status": status,
            "rationale": "The review exceeded its execution budget.",
        }
    if status != "in_progress":
        payload["completed_at_ms"] = 150
        payload["decision_source"] = "agent"
    return parse_stream_event(payload)


def test_wire_review_maps_to_typed_domain_record() -> None:
    event = _review_event(
        "tool.approval_review.completed",
        "approved",
        event_seq=2,
    )
    assert isinstance(event, ToolApprovalReviewCompletedEvent)

    record = approval_review_record(event)

    assert record.identity.session_id == "sid-review"
    assert record.identity.run_id == "turn-review"
    assert record.identity.review_id == "review-1"
    assert record.identity.approval_id == "approval-1"
    assert record.identity.action_id == "call-1"
    assert record.identity.target_item_id == "call-1"
    assert record.identity.action_kind is ApprovalActionKind.COMMAND
    assert record.status is ApprovalReviewStatus.APPROVED
    assert record.risk_level is ApprovalReviewRiskLevel.LOW
    assert record.user_authorization is ApprovalReviewUserAuthorization.HIGH
    assert record.completed_at_ms == 150


@pytest.mark.anyio
async def test_review_handler_clears_replaced_epoch_and_ignores_stale_review() -> None:
    feed = _ReviewFeed()
    handler, activity = _handler(feed)
    started = _review_event(
        "tool.approval_review.started",
        "in_progress",
        event_seq=1,
    )
    assert isinstance(started, ToolApprovalReviewStartedEvent)

    assert await handler.observe_presentation(started) is True
    assert await handler.handle(started) is True

    replacement = MarkerEvent(
        type="turn.start",
        proto="mind.chat",
        cid="cid-review",
        sid="sid-review",
        turn_id="turn-review",
        event_seq=2,
        presentation_epoch=2,
    )
    assert await handler.observe_presentation(replacement) is True

    stale = _review_event(
        "tool.approval_review.completed",
        "approved",
        event_seq=3,
        presentation_epoch=1,
    )
    assert await handler.observe_presentation(stale) is False
    assert await handler.handle(stale) is True

    assert [record.status for record in feed.records] == [
        ApprovalReviewStatus.IN_PROGRESS,
    ]
    assert isinstance(activity.items[0], ApprovalReviewStarted)
    assert feed.cleared == [("sid-review", "turn-review")]

    await handler.close()
    await handler.close()
    assert feed.cleared == [
        ("sid-review", "turn-review"),
        ("sid-review", "turn-review"),
    ]


@pytest.mark.anyio
async def test_review_handler_accepts_self_contained_replayed_completion() -> None:
    feed = _ReviewFeed()
    handler, activity = _handler(feed)
    completed = _review_event(
        "tool.approval_review.completed",
        "timed_out",
        event_seq=9,
        presentation_epoch=3,
    )

    assert await handler.observe_presentation(completed) is True
    assert await handler.handle(completed) is True
    assert feed.records[0].status is ApprovalReviewStatus.TIMED_OUT
    assert feed.records[0].presentation_epoch == 3
    assert isinstance(activity.items[0], ApprovalReviewCompleted)

    await handler.close()
