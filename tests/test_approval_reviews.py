# -*- coding: utf-8 -*-

import dataclasses

import pytest

from agent.application.approvals.core import (
    ApprovalCore,
    PresentationArbiter,
    ReviewerBinding,
    ReviewerChain,
)
from agent.application.approvals.reviews import ApprovalReviewInbox
from agent.application.approvals.fingerprints import approval_action_fingerprint
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalActionKind,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalIdentity,
    ApprovalReviewIdentity,
    ApprovalReviewConflict,
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
        fingerprint=approval_action_fingerprint({
            "command": ["curl", "https://example.com"],
            "cwd": "D:/workspace",
        }, "command"),
        command=("curl", "https://example.com"),
        cwd="D:/workspace",
    )


def _record(
    status: ApprovalReviewStatus,
    *,
    event_seq: int = 2,
    action_kind: ApprovalActionKind = ApprovalActionKind.COMMAND,
    action_id: str = "call-1",
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
            action_id=action_id,
            target_item_id=action_id,
            action_kind=action_kind,
        ),
        action_fingerprint=approval_action_fingerprint({
            "command": ["curl", "https://example.com"],
            "cwd": "D:/workspace",
        }, "command"),
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


@pytest.mark.parametrize(
    ("kind", "payload", "changed_field", "changed_value"),
    (
        (
            "command",
            {
                "command": ["curl", "https://example.com"],
                "cwd": ".",
                "sandbox_permissions": "use_default",
            },
            "sandbox_permissions",
            "with_additional_permissions",
        ),
        (
            "write_stdin",
            {"session_id": "terminal-1", "input": "yes\n", "control": "none"},
            "control",
            "terminate",
        ),
        (
            "apply_patch",
            {
                "environment_id": "workspace",
                "patch": "diff",
                "files": ["a.py"],
            },
            "environment_id",
            "outside-workspace",
        ),
        (
            "network_access",
            {
                "target": "https://example.com",
                "host": "example.com",
                "port": 443,
            },
            "host",
            "other.example.com",
        ),
        (
            "request_permissions",
            {"permissions": {"network": ["example.com"]}},
            "permissions",
            {"network": ["other.example.com"]},
        ),
        (
            "mcp_tool_call",
            {
                "server": "github",
                "tool_name": "read_issue",
                "arguments": {"number": 1},
                "mcp_request_id": "request-1",
                "annotations": {"readOnlyHint": True},
            },
            "annotations",
            {"readOnlyHint": False},
        ),
    ),
)
def test_review_action_fingerprint_covers_security_fields(
    kind: str,
    payload: dict[str, object],
    changed_field: str,
    changed_value: object,
) -> None:
    changed = dict(payload)
    changed[changed_field] = changed_value

    assert approval_action_fingerprint(
        payload,
        kind,
    ) != approval_action_fingerprint(changed, kind)


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
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("review_id", "review-other"),
        ("approval_id", "approval-other"),
        ("action_id", "call-other"),
        ("target_item_id", "item-other"),
        ("action_kind", ApprovalActionKind.NETWORK),
    ),
)
async def test_review_inbox_rejects_identity_drift_between_events(
    field: str,
    value: str | ApprovalActionKind,
) -> None:
    inbox = ApprovalReviewInbox()
    started = _record(ApprovalReviewStatus.IN_PROGRESS, event_seq=1)
    completed = _record(ApprovalReviewStatus.APPROVED, event_seq=2)
    changed_identity = dataclasses.replace(
        completed.identity,
        **{field: value},
    )
    await inbox.record(started)

    with pytest.raises(ApprovalReviewConflict):
        await inbox.record(dataclasses.replace(
            completed,
            identity=changed_identity,
        ))


@pytest.mark.anyio
async def test_review_inbox_rejects_action_drift_between_events() -> None:
    inbox = ApprovalReviewInbox()
    await inbox.record(_record(ApprovalReviewStatus.IN_PROGRESS, event_seq=1))

    with pytest.raises(ApprovalReviewConflict, match="action changed"):
        await inbox.record(dataclasses.replace(
            _record(ApprovalReviewStatus.APPROVED, event_seq=2),
            action_fingerprint=ActionFingerprint("different-action"),
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
async def test_review_inbox_rejects_approval_bound_to_another_action() -> None:
    inbox = ApprovalReviewInbox()
    await inbox.record(_record(
        ApprovalReviewStatus.APPROVED,
        action_id="call-other",
    ))

    with pytest.raises(
        ApprovalReviewConflict,
        match="does not match requested action",
    ):
        await inbox.review(_action())


@pytest.mark.anyio
async def test_review_inbox_rejects_changed_action_payload() -> None:
    inbox = ApprovalReviewInbox()
    await inbox.record(dataclasses.replace(
        _record(ApprovalReviewStatus.APPROVED),
        action_fingerprint=ActionFingerprint("different-action"),
    ))

    with pytest.raises(
        ApprovalReviewConflict,
        match="action does not match request",
    ):
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
