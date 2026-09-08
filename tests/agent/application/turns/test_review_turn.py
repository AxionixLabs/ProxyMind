# -*- coding: utf-8 -*-

from collections.abc import AsyncIterator

import pytest

from agent.adapters.protocol.items import CanonicalItemReducer
from agent.application.turns.reviews import (
    create_review_command,
    run_review_turn,
)
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    Viewport,
)
from agent.protocol import (
    CanonicalItem,
    ReviewStreamRequest,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCodeLocation,
    ReviewCustomTarget,
    ReviewFinding,
    ReviewLineRange,
    ReviewOutput,
)
from protocol.schema.stream_events import (
    MarkerEvent,
    ReviewCompletedEvent,
    ReviewStartedEvent,
    StreamEvent,
    TurnCompletedEvent,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REVIEW_ITEM_ID = "review_item_01"


class _Sink(ApplicationSink):
    """记录 Review 应用展示。"""

    def __init__(self) -> None:
        self.views: list[ApplicationView] = []

    @property
    def viewport(self) -> Viewport:
        return Viewport(width=80, height=24)

    def emit(self, view: ApplicationView) -> None:
        self.views.append(view)


class _Stream:
    """按正式 reducer 顺序交付 Review 事件。"""

    def __init__(self, events: tuple[StreamEvent, ...]) -> None:
        self._events = events
        self._reducer = CanonicalItemReducer(
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
        )
        self.current_item: CanonicalItem | None = None
        self.end_reason = None
        self.last_event_seq = 0
        self.closed = False

    def __aiter__(self) -> AsyncIterator[StreamEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StreamEvent]:
        for event in self._events:
            self.current_item = self._reducer.apply(event)
            self.last_event_seq = event.event_seq or self.last_event_seq
            yield event

    async def aclose(self) -> None:
        self.closed = True


class _Capability:
    """返回预置 Review 事件流。"""

    def __init__(self, stream: _Stream) -> None:
        self.stream = stream
        self.request: ReviewStreamRequest | None = None

    async def review(self, request: ReviewStreamRequest) -> _Stream:
        self.request = request
        return self.stream


def _output() -> ReviewOutput:
    return ReviewOutput(
        findings=(ReviewFinding(
            title="Reject stale terminal",
            body="The stale event can replace the active item.",
            confidence_score=0.95,
            priority=1,
            code_location=ReviewCodeLocation(
                path="agent/runtime.py",
                line_range=ReviewLineRange(start=10, end=12),
            ),
        ),),
        overall_correctness="incorrect",
        overall_explanation="One lifecycle issue remains.",
        overall_confidence_score=0.9,
    )


def _events(*, include_turn_terminal: bool) -> tuple[StreamEvent, ...]:
    target = ReviewCustomTarget("Focus on lifecycle correctness.")
    workspace = ClientReviewWorkspace.create()
    events: tuple[StreamEvent, ...] = (
        MarkerEvent(
            type="turn.started",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=1,
        ),
        ReviewStartedEvent(
            type="review.started",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=2,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="in_progress",
            review_item_id=REVIEW_ITEM_ID,
            status="in_progress",
            target=target,
            workspace_revision=workspace.revision,
            prompt_version="mind-review/1",
        ),
        ReviewCompletedEvent(
            type="review.completed",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=3,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="completed",
            review_item_id=REVIEW_ITEM_ID,
            status="completed",
            output=_output(),
        ),
    )
    if not include_turn_terminal:
        return events
    return events + (TurnCompletedEvent(
        type="turn.completed",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=4,
        status="completed",
        last_event_seq=4,
        completed_at=1.0,
    ),)


@pytest.mark.anyio
async def test_review_turn_uses_canonical_output_and_turn_terminal() -> None:
    stream = _Stream(_events(include_turn_terminal=True))
    capability = _Capability(stream)
    sink = _Sink()
    ends = []
    command = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        llm_conf={"primary": {"model": "test-model"}},
        environment_snapshot={"platform": "test"},
    )

    result = await run_review_turn(
        command.request,
        command.environment_snapshot_value(),
        capability=capability,
        application=sink,
        hint="Focus on lifecycle correctness.",
        on_stream_end=ends.append,
    )

    assert result.status == "completed"
    assert result.assistant_text == (
        "One lifecycle issue remains.\n\n"
        "Review comment:\n\n"
        "- Reject stale terminal — agent/runtime.py:10-12\n"
        "  The stale event can replace the active item."
    )
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.completed",
    ]
    assert capability.request == command.request
    assert ends == ["settled"]
    assert stream.closed


@pytest.mark.anyio
async def test_review_turn_without_turn_terminal_requires_reconciliation() -> None:
    stream = _Stream(_events(include_turn_terminal=False))
    capability = _Capability(stream)
    sink = _Sink()
    ends = []
    command = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        llm_conf={},
        environment_snapshot=None,
    )

    result = await run_review_turn(
        command.request,
        None,
        capability=capability,
        application=sink,
        hint="Focus on lifecycle correctness.",
        on_stream_end=ends.append,
    )

    assert result.status == "reconciliation_required"
    assert sink.views[-1].type == "review.reconciliation_required"
    assert ends == ["protocol_error"]
    assert stream.closed
