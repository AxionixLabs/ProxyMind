# -*- coding: utf-8 -*-

from collections.abc import AsyncIterator

import pytest

from agent.adapters.protocol.items import CanonicalItemReducer
from agent.application.turns.reviews import (
    create_review_command,
    run_observed_review_turn,
    run_review_turn,
)
from agent.ports import ModelCapabilityError
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
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
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


class _ObservationCapability:
    """记录只观察 Review 时使用的 replay 水位。"""

    def __init__(self, stream: _Stream) -> None:
        self.stream = stream
        self.request: ReviewStreamRequest | None = None
        self.after_event_seq: int | None = None
        self.replay_target_seq: int | None = None

    def observe_review(
        self,
        request: ReviewStreamRequest,
        *,
        after_event_seq: int | None = None,
        replay_target_seq: int | None = None,
        on_recovery_status=None,
    ) -> _Stream:
        self.request = request
        self.after_event_seq = after_event_seq
        self.replay_target_seq = replay_target_seq
        return self.stream


class _RejectedCapability:
    """在远端明确拒绝登记时返回稳定能力错误。"""

    async def review(self, _request: ReviewStreamRequest) -> _Stream:
        """模拟可重试但提交结果确定的服务端拒绝。"""
        raise ModelCapabilityError(
            "runtime_unavailable",
            "Review service is temporarily unavailable.",
            retryable=True,
            details={"submission_unknown": False},
        )


class _DisconnectedStream:
    """模拟已登记 Review 在首项事件前失去观察连接。"""

    def __init__(self) -> None:
        self.current_item: CanonicalItem | None = None
        self.end_reason = "fatal"
        self.last_event_seq = 0
        self.closed = False

    def __aiter__(self) -> "_DisconnectedStream":
        """返回自身作为失败的异步迭代器。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """在尚未交付事件时报告连接失败。"""
        raise ModelCapabilityError(
            "model_transport_error",
            "connection lost",
            retryable=True,
        )

    async def aclose(self) -> None:
        """记录失败观察流已关闭。"""
        self.closed = True


class _DisconnectedCapability:
    """返回已经确认登记的失败观察流。"""

    def __init__(self, stream: _DisconnectedStream) -> None:
        self.stream = stream

    async def review(
        self,
        _request: ReviewStreamRequest,
    ) -> _DisconnectedStream:
        """模拟登记回执成功后才发生的观察失败。"""
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


@pytest.mark.anyio
async def test_review_explicit_retryable_rejection_is_visible_failure() -> None:
    """确保明确的 503 拒绝不会误报为提交结果未知。"""
    sink = _Sink()
    request = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        llm_conf={},
        environment_snapshot=None,
    ).request

    result = await run_review_turn(
        request,
        None,
        capability=_RejectedCapability(),
        application=sink,
        hint="Focus on lifecycle correctness.",
    )

    assert result.status == "failed"
    assert result.error_code == "runtime_unavailable"
    assert [view.type for view in sink.views] == ["review.failed"]


@pytest.mark.anyio
async def test_review_disconnect_after_receipt_requires_reconciliation() -> None:
    """确保已拿到登记回执后的首事件前断线仍保留恢复门禁。"""
    stream = _DisconnectedStream()
    sink = _Sink()
    request = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        llm_conf={},
        environment_snapshot=None,
    ).request

    result = await run_review_turn(
        request,
        None,
        capability=_DisconnectedCapability(stream),
        application=sink,
        hint="Focus on lifecycle correctness.",
    )

    assert result.status == "reconciliation_required"
    assert result.error_code == "model_transport_error"
    assert [view.type for view in sink.views] == [
        "review.reconciliation_required",
    ]
    assert stream.closed


@pytest.mark.anyio
async def test_observed_review_reuses_projection_and_replay_identity() -> None:
    stream = _Stream(_events(include_turn_terminal=True))
    capability = _ObservationCapability(stream)
    sink = _Sink()
    request = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        llm_conf={},
        environment_snapshot=None,
    ).request

    result = await run_observed_review_turn(
        request,
        capability=capability,
        application=sink,
        hint="Focus on lifecycle correctness.",
        replay_target_seq=4,
    )

    assert result.status == "completed"
    assert capability.request == request
    assert capability.after_event_seq == 0
    assert capability.replay_target_seq == 4
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.completed",
    ]


@pytest.mark.anyio
async def test_review_reconciliation_terminal_is_presented_once() -> None:
    target = ReviewCustomTarget("Focus on lifecycle correctness.")
    workspace = ClientReviewWorkspace.create()
    events: tuple[StreamEvent, ...] = (
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
            target=target,
            workspace_revision=workspace.revision,
            prompt_version="mind-review/1",
        ),
        ReviewReconciliationRequiredEvent(
            type="review.reconciliation_required",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=2,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="reconciliation_required",
            review_item_id=REVIEW_ITEM_ID,
            status="reconciliation_required",
            error="submission outcome is uncertain",
            effect_id="effect_review_01",
        ),
    )
    stream = _Stream(events)
    sink = _Sink()

    request = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        llm_conf={},
        environment_snapshot=None,
    ).request

    result = await run_review_turn(
        request,
        None,
        capability=_Capability(stream),
        application=sink,
        hint="Focus on lifecycle correctness.",
    )

    assert result.status == "reconciliation_required"
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.reconciliation_required",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("review_terminal", "turn_status", "result_status", "view_type", "error"),
    (
        (
            ReviewFailedEvent(
                type="review.failed",
                proto="mind.chat",
                cid=CID,
                sid=SID,
                turn_id=TURN_ID,
                event_seq=2,
                item_id=REVIEW_ITEM_ID,
                item_kind="review",
                item_status="failed",
                review_item_id=REVIEW_ITEM_ID,
                status="failed",
                error="review worker failed",
            ),
            "failed",
            "failed",
            "review.failed",
            "review worker failed",
        ),
        (
            ReviewCancelledEvent(
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
            ),
            "cancelled",
            "cancelled",
            "review.cancelled",
            None,
        ),
    ),
)
async def test_review_terminal_events_keep_distinct_presentations(
    review_terminal: StreamEvent,
    turn_status: str,
    result_status: str,
    view_type: str,
    error: str | None,
) -> None:
    target = ReviewCustomTarget("Focus on lifecycle correctness.")
    workspace = ClientReviewWorkspace.create()
    events: tuple[StreamEvent, ...] = (
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
            target=target,
            workspace_revision=workspace.revision,
            prompt_version="mind-review/1",
        ),
        review_terminal,
        TurnCompletedEvent(
            type="turn.completed",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=3,
            status=turn_status,
            last_event_seq=3,
            completed_at=1.0,
            error=error,
        ),
    )
    request = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        llm_conf={},
        environment_snapshot=None,
    ).request
    sink = _Sink()

    result = await run_review_turn(
        request,
        None,
        capability=_Capability(_Stream(events)),
        application=sink,
        hint="Focus on lifecycle correctness.",
    )

    assert result.status == result_status
    assert result.error == error
    assert [view.type for view in sink.views] == [
        "review.started",
        view_type,
    ]
