# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.adapters.protocol.items import CanonicalItemReducer
from agent.adapters.protocol.review_events import ReviewEventProjector
from agent.adapters.protocol.turn_source import (
    ObservingReviewTurnStreamSource,
    SubmittingReviewTurnStreamSource,
)
from agent.application.turns.reviews import (
    create_review_command,
    discover_review_tools,
    review_request_hint,
    review_target_hint,
    review_wire_tools,
)
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    Viewport,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewCodeLocation,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewFinding,
    ReviewLineRange,
    ReviewOutput,
    ReviewUncommittedTarget,
)
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
    ReviewStartedEvent,
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


class _ReviewCapability:
    """记录 Review source 的提交参数。"""

    def __init__(self) -> None:
        self.calls = []
        self.stream = SimpleNamespace()

    async def review(
        self,
        request,
        *,
        on_recovery_status=None,
        on_approval_snapshot=None,
    ):
        self.calls.append((request, on_recovery_status, on_approval_snapshot))
        return self.stream


class _ReviewObserver:
    """记录 Review source 的 attach 参数。"""

    def __init__(self) -> None:
        self.calls = []
        self.stream = SimpleNamespace()

    def observe_review(
        self,
        request,
        *,
        after_event_seq=None,
        replay_target_seq=None,
        on_recovery_status=None,
    ):
        self.calls.append((
            request,
            after_event_seq,
            replay_target_seq,
            on_recovery_status,
        ))
        return self.stream


def _catalog():
    return [
        {
            "name": name,
            "description": name,
            "inputSchema": {"type": "object"},
            "meta": {
                "client_builtin": True,
                "domain": "coding",
                "class": "shell",
            },
        }
        for name in ("shell_command", "exec_command", "write_stdin")
    ]


def _tools():
    return review_wire_tools(_catalog())


def _command(*, target=None):
    resolved_target = target or ReviewCustomTarget(
        "Focus on lifecycle correctness."
    )
    workspace = (
        ClientReviewWorkspace.create()
        if isinstance(resolved_target, ReviewCustomTarget)
        else ClientReviewWorkspace.create(patch="diff --git a/a b/a\n")
    )
    return create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=resolved_target,
        workspace=workspace,
        pref_config={"primary": {"model": "test-model"}},
        environment_snapshot={"platform": "test"},
        tools=_tools(),
    )


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


def _review_event(event_type: str):
    common = {
        "type": event_type,
        "proto": "mind.chat",
        "cid": CID,
        "sid": SID,
        "turn_id": TURN_ID,
        "event_seq": 2 if event_type == "review.started" else 3,
        "presentation_epoch": 1,
        "item_id": REVIEW_ITEM_ID,
        "item_kind": "review",
        "review_item_id": REVIEW_ITEM_ID,
    }
    if event_type == "review.started":
        return ReviewStartedEvent(
            **common,
            item_status="in_progress",
            status="in_progress",
            target=ReviewCustomTarget("Focus on lifecycle correctness."),
            workspace_revision=ClientReviewWorkspace.create().revision,
            prompt_version="mind-review/1",
        )
    if event_type == "review.completed":
        return ReviewCompletedEvent(
            **common,
            item_status="completed",
            status="completed",
            output=_output(),
        )
    if event_type == "review.failed":
        return ReviewFailedEvent(
            **common,
            item_status="failed",
            status="failed",
            error="Review failed.",
        )
    if event_type == "review.cancelled":
        return ReviewCancelledEvent(
            **common,
            item_status="cancelled",
            status="cancelled",
            reason="interrupted",
        )
    return ReviewReconciliationRequiredEvent(
        **common,
        item_status="reconciliation_required",
        status="reconciliation_required",
        effect_id="effect-review",
        error="effect outcome is unknown",
    )


def test_create_review_command_projects_only_wire_llm_fields_and_tools() -> None:
    command = create_review_command(
        local_session_id="tui_session_01",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={
            "primary": {
                "name": "OpenAI",
                "kind": "openai_compatible",
                "enabled": True,
                "route": "responses",
                "model": "gpt-test",
                "apikey": "test-key",
                "base_url": "https://example.test/v1",
                "reasoning_effort": "xhigh",
            },
            "hosted_tools": {"groups": {"sandbox_cloud": False}},
        },
        environment_snapshot=None,
        tools=_tools(),
    )

    execution = command.request.to_dict()["execution"]
    assert execution["llm_conf"] == {
        "primary": {
            "provider": "openai_compatible",
            "route": "responses",
            "model": "gpt-test",
            "apikey": "test-key",
            "base_url": "https://example.test/v1",
            "reasoning_effort": "xhigh",
        },
    }
    assert [tool["name"] for tool in execution["tools"]] == [
        "exec_command",
        "shell_command",
        "write_stdin",
    ]
    assert all(
        tool["annotations"] == {"readOnlyHint": True}
        for tool in execution["tools"]
    )
    assert execution["hosted_tools"] is None


def test_review_wire_tools_reject_missing_or_non_client_capabilities() -> None:
    with pytest.raises(RuntimeError, match="shell_command"):
        review_wire_tools(_catalog()[1:])

    catalog = _catalog()
    catalog[0]["meta"]["external"] = True
    with pytest.raises(RuntimeError, match="shell_command"):
        review_wire_tools(catalog)


@pytest.mark.anyio
async def test_discover_review_tools_uses_one_runtime_catalog() -> None:
    runtime = SimpleNamespace(with_mcp_session=AsyncMock())

    async def run_callback(pref_config, callback):
        assert pref_config == {"primary": {"model": "test"}}
        return await callback(SimpleNamespace(), _catalog())

    runtime.with_mcp_session.side_effect = run_callback
    tools = await discover_review_tools(
        runtime,
        {"primary": {"model": "test"}},
    )

    assert {tool["name"] for tool in tools} == {
        "shell_command",
        "exec_command",
        "write_stdin",
    }


@pytest.mark.anyio
async def test_review_sources_preserve_submit_and_attach_identity() -> None:
    request = _command().request
    context = SimpleNamespace(cid=CID, sid=SID, turn_id=TURN_ID)
    recovery = AsyncMock()
    approvals = AsyncMock()
    submit_capability = _ReviewCapability()
    submit = SubmittingReviewTurnStreamSource(submit_capability, request)

    submitted = await submit.open(
        context,
        pref_config={},
        message="review",
        tools=[],
        options={},
        on_recovery_status=recovery,
        on_approval_snapshot=approvals,
    )

    assert submitted is submit_capability.stream
    assert submit_capability.calls == [(request, recovery, approvals)]
    assert submit.records_local_start
    assert submit.initial_wait_visible
    assert submit.continuation_capability is None

    observer = _ReviewObserver()
    observed_source = ObservingReviewTurnStreamSource(
        observer,
        request,
        after_event_seq=1,
        replay_target_seq=4,
    )
    observed = await observed_source.open(
        context,
        pref_config={},
        message="review",
        tools=[],
        options={},
        on_recovery_status=recovery,
        on_approval_snapshot=approvals,
    )

    assert observed is observer.stream
    assert observer.calls == [(request, 1, 4, recovery)]
    assert observed_source.historical_replay_target_seq == 4
    assert not observed_source.initial_wait_visible


@pytest.mark.anyio
async def test_review_projector_uses_only_canonical_completed_output() -> None:
    sink = _Sink()
    replies = []
    projector = ReviewEventProjector(
        sink,
        hint="current changes",
        assistant_reply_sink=replies.append,
    )
    reducer = CanonicalItemReducer(cid=CID, sid=SID, turn_id=TURN_ID)
    started = _review_event("review.started")
    completed = _review_event("review.completed")

    assert await projector.observe(started, reducer.apply(started))
    assert await projector.observe(completed, reducer.apply(completed))

    assert [view.type for view in sink.views] == [
        "review.started",
        "review.finished",
        "review.completed",
    ]
    assert projector.assistant_text("raw reviewer JSON") == (
        "One lifecycle issue remains.\n\n"
        "Review comment:\n\n"
        "- Reject stale terminal — agent/runtime.py:10-12\n"
        "  The stale event can replace the active item."
    )
    assert replies == [projector.assistant_text("")]
    assert not projector.assistant_output_visible
    assert not projector.run_lifecycle_visible


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "expected"),
    (
        ("review.failed", ["review.finished", "review.failed"]),
        ("review.cancelled", ["review.finished", "review.cancelled"]),
        ("review.reconciliation_required", ["review.reconciliation_required"]),
    ),
)
async def test_review_projector_preserves_distinct_terminal_views(
    event_type: str,
    expected: list[str],
) -> None:
    sink = _Sink()
    projector = ReviewEventProjector(sink, hint="current changes")
    reducer = CanonicalItemReducer(cid=CID, sid=SID, turn_id=TURN_ID)
    started = _review_event("review.started")
    terminal = _review_event(event_type)
    await projector.observe(started, reducer.apply(started))
    await projector.observe(terminal, reducer.apply(terminal))

    assert [view.type for view in sink.views] == ["review.started", *expected]


@pytest.mark.anyio
async def test_review_projector_maps_local_uncertainty_without_duplicates() -> None:
    sink = _Sink()
    projector = ReviewEventProjector(sink, hint="current changes")

    await projector.failure("reconciliation_required", "connection lost")
    await projector.failure("reconciliation_required", "connection lost")

    assert [view.type for view in sink.views] == [
        "review.reconciliation_required",
    ]


@pytest.mark.parametrize(
    ("target", "hint"),
    (
        (ReviewUncommittedTarget(), "current changes"),
        (ReviewBaseBranchTarget("main"), "changes against 'main'"),
        (ReviewCommitTarget("a" * 40, "Fix race"), "commit aaaaaaa: Fix race"),
        (ReviewCustomTarget("Focus on races"), "Focus on races"),
    ),
)
def test_review_target_and_request_hints_share_typed_source(target, hint) -> None:
    assert review_target_hint(target) == hint
    assert review_request_hint(_command(target=target).request) == hint
