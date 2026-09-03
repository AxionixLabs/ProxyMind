# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.core import (
    ApprovalCore,
    PresentationArbiter,
    ReviewerBinding,
    ReviewerChain,
)
from agent.application.approvals.reviews import ApprovalReviewInbox
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalActionKind,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalIdentity,
    ApprovalReviewIdentity,
    ApprovalReviewRecord,
    ApprovalReviewRiskLevel,
    ApprovalReviewStatus,
    ApprovalReviewUserAuthorization,
    CommandApprovalAction,
    ExecutionIdentity,
)
from agent.stores.approvals.facts import InMemoryApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore


def _action() -> CommandApprovalAction:
    return CommandApprovalAction(
        identity=ApprovalIdentity(
            session_id="session-1",
            run_id="turn-1",
            approval_id="approval-1",
            action_id="call-1",
        ),
        execution=ExecutionIdentity(
            environment_id="workspace-write",
            execution_id="turn-1",
            tool_call_id="call-1",
        ),
        fingerprint=ActionFingerprint("action-fingerprint"),
        command=("curl", "https://example.com"),
        cwd="D:/workspace",
    )


def _record(
    status: ApprovalReviewStatus,
    *,
    event_seq: int = 2,
    action_kind: ApprovalActionKind = ApprovalActionKind.COMMAND,
) -> ApprovalReviewRecord:
    terminal = status is not ApprovalReviewStatus.IN_PROGRESS
    decided = status in {
        ApprovalReviewStatus.APPROVED,
        ApprovalReviewStatus.DENIED,
    }
    return ApprovalReviewRecord(
        identity=ApprovalReviewIdentity(
            session_id="session-1",
            run_id="turn-1",
            review_id="review-1",
            approval_id="approval-1",
            action_id="call-1",
            action_kind=action_kind,
        ),
        status=status,
        event_seq=event_seq,
        presentation_epoch=1,
        started_at_ms=100,
        completed_at_ms=150 if terminal else None,
        risk_level=ApprovalReviewRiskLevel.HIGH if decided else None,
        user_authorization=(
            ApprovalReviewUserAuthorization.LOW if decided else None
        ),
        rationale=(
            "The action sends workspace data externally."
            if decided
            else (
                "Automatic approval review timed out."
                if status is ApprovalReviewStatus.TIMED_OUT
                else None
            )
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "decision_kind"),
    (
        (ApprovalReviewStatus.APPROVED, ApprovalDecisionKind.ALLOW_ONCE),
        (ApprovalReviewStatus.DENIED, ApprovalDecisionKind.DECLINE),
        (ApprovalReviewStatus.TIMED_OUT, ApprovalDecisionKind.TIMEOUT),
        (ApprovalReviewStatus.ABORTED, ApprovalDecisionKind.CANCEL),
    ),
)
async def test_review_inbox_maps_terminal_status_to_bound_decision(
    status: ApprovalReviewStatus,
    decision_kind: ApprovalDecisionKind,
) -> None:
    inbox = ApprovalReviewInbox()
    action = _action()
    await inbox.record(_record(status))

    decision = await inbox.review(action)

    assert decision is not None
    assert decision.kind is decision_kind
    assert decision.action_fingerprint == action.fingerprint


@pytest.mark.anyio
async def test_review_inbox_is_idempotent_and_rejects_terminal_conflict() -> None:
    inbox = ApprovalReviewInbox()
    denied = _record(ApprovalReviewStatus.DENIED)
    await inbox.record(denied)
    await inbox.record(denied)

    with pytest.raises(ValueError, match="terminal state"):
        await inbox.record(_record(
            ApprovalReviewStatus.APPROVED,
            event_seq=3,
        ))


@pytest.mark.anyio
async def test_review_inbox_requires_matching_action_kind() -> None:
    inbox = ApprovalReviewInbox()
    await inbox.record(_record(
        ApprovalReviewStatus.APPROVED,
        action_kind=ApprovalActionKind.NETWORK,
    ))

    with pytest.raises(ValueError, match="action kind"):
        await inbox.review(_action())


@pytest.mark.anyio
async def test_in_progress_review_falls_through_to_user_presentation() -> None:
    class Presentation:
        calls = 0

        async def present(self, action: CommandApprovalAction):
            self.calls += 1
            from agent.domain.approvals import ApprovalDecision

            return ApprovalDecision(
                kind=ApprovalDecisionKind.DECLINE,
                action_fingerprint=action.fingerprint,
            )

    inbox = ApprovalReviewInbox()
    presentation = Presentation()
    await inbox.record(_record(
        ApprovalReviewStatus.IN_PROGRESS,
        event_seq=1,
    ))
    core = ApprovalCore(
        InMemoryApprovalFactStore(),
        InMemorySessionGrantStore(),
        reviewer_chain=ReviewerChain((ReviewerBinding(
            source=ApprovalDecisionSource.AUTO_REVIEW,
            reviewer=inbox,
        ),)),
        presentation=PresentationArbiter(presentation),
    )

    fact = await core.request(_action())

    assert fact.outcome is not None
    assert fact.outcome.source is ApprovalDecisionSource.USER
    assert presentation.calls == 1


@pytest.mark.anyio
async def test_only_approved_review_can_execute_effect() -> None:
    class Journal:
        async def begin(self, _effect):
            from agent.ports.persistence import EffectJournalDecision

            return EffectJournalDecision("execute")

        async def commit(self, _effect, _payload):
            return None

        async def mark_unknown(self, _effect, _error, *, result_payload=None):
            return None

    action = _action()
    calls: list[str] = []
    for status in (
        ApprovalReviewStatus.APPROVED,
        ApprovalReviewStatus.DENIED,
        ApprovalReviewStatus.TIMED_OUT,
        ApprovalReviewStatus.ABORTED,
    ):
        inbox = ApprovalReviewInbox()
        await inbox.record(_record(status))
        core = ApprovalCore(
            InMemoryApprovalFactStore(),
            InMemorySessionGrantStore(),
            reviewer_chain=ReviewerChain((ReviewerBinding(
                source=ApprovalDecisionSource.AUTO_REVIEW,
                reviewer=inbox,
            ),)),
        )

        async def execute():
            calls.append(status.value)
            return {"ok": True}

        result = await core.execute_effect(
            action,
            effect=type("Effect", (), {
                "effect_id": f"effect-{status.value}",
                "fingerprint": "effect-fingerprint",
                "replay": "safe",
            })(),
            execute=execute,
            journal=Journal(),
        )
        if status is ApprovalReviewStatus.APPROVED:
            assert result.result_payload == {"ok": True}
        else:
            assert result.result_payload is None

    assert calls == ["approved"]


if __name__ == '__main__':
    pass
