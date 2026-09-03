# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
from agent.application.approvals.fingerprints import approval_action_fingerprint
from agent.application.approvals.mcp import mcp_approval_payload
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalFactState,
    ApprovalIdentity,
    ApprovalActionKind,
    ApprovalReviewIdentity,
    ApprovalReviewRecord,
    ApprovalReviewRiskLevel,
    ApprovalReviewStatus,
    ApprovalReviewUserAuthorization,
    ExecutionIdentity,
    McpApprovalAction,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
)
from agent.stores.approvals.facts import SQLiteApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore


class _Interaction:
    approval_source = "user"

    def __init__(self, decision: str = "acceptForSession") -> None:
        self.decision = decision
        self.calls: list[str] = []

    async def begin_approval_session(self) -> None:
        return None

    def approval_snapshot_changed(self, snapshot) -> None:
        return None

    async def present_approval(self, request):
        self.calls.append(request.key.request_id)
        return self.decision

    async def end_approval_session(self) -> None:
        return None


def _payload(run_id: str, approval_id: str) -> dict[str, object]:
    return {
        "session_id": "session-bridge",
        "run_id": run_id,
        "approval_id": approval_id,
        "call_id": f"call-{approval_id}",
        "kind": "command",
        "tool": "exec_command",
        "command": ["echo", "bridge"],
        "cwd": "D:/workspace",
        "available_decisions": ["accept", "acceptForSession", "decline"],
    }


def _mcp_action(suffix: str) -> McpApprovalAction:
    descriptor = McpToolDescriptor(
        server="docs-api",
        exposed_name="mcp__docs-api__publish",
        tool_name="publish",
        schema_fingerprint=ActionFingerprint("schema"),
        annotations=McpToolAnnotations(read_only_hint=False),
        policy=McpApprovalPolicy(McpApprovalMode.PROMPT),
        config_server_key="Docs API",
    )
    return McpApprovalAction(
        identity=ApprovalIdentity(
            session_id="session-mcp",
            run_id=f"run-{suffix}",
            approval_id=f"approval-{suffix}",
            action_id=f"call-{suffix}",
        ),
        execution=ExecutionIdentity(
            environment_id="workspace-write",
            execution_id=f"run-{suffix}",
            tool_call_id=f"call-{suffix}",
        ),
        fingerprint=ActionFingerprint(f"action-{suffix}"),
        descriptor=descriptor,
        arguments_fingerprint=ActionFingerprint(f"arguments-{suffix}"),
    )


@pytest.mark.anyio
async def test_legacy_requests_are_recorded_by_typed_core(tmp_path) -> None:
    interaction = _Interaction()
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(interaction),
        fact_store=SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3"),
        grant_store=InMemorySessionGrantStore(),
    )

    first = await bridge.request_outcome(_payload("run-1", "approval-1"))
    second = await bridge.request_outcome(_payload("run-1", "approval-1"))

    assert first.decision == "acceptForSession"
    assert second.decision == "acceptForSession"
    assert interaction.calls == ["approval-1"]

    await bridge.close()


@pytest.mark.anyio
async def test_legacy_session_grant_skips_card_for_later_run(tmp_path) -> None:
    interaction = _Interaction()
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(interaction),
        fact_store=SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3"),
        grant_store=InMemorySessionGrantStore(),
    )

    first = await bridge.request_outcome(_payload("run-1", "approval-1"))
    second = await bridge.request_outcome(_payload("run-2", "approval-2"))

    assert first.decision == "acceptForSession"
    assert second.decision == "acceptForSession"
    assert interaction.calls == ["approval-1"]

    await bridge.close()


@pytest.mark.anyio
async def test_protocol_review_result_uses_typed_core_without_showing_card(
    tmp_path,
) -> None:
    interaction = _Interaction("decline")
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(interaction),
        fact_store=SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3"),
        grant_store=InMemorySessionGrantStore(),
    )
    await bridge.record_review(ApprovalReviewRecord(
        identity=ApprovalReviewIdentity(
            session_id="session-bridge",
            run_id="run-1",
            review_id="review-1",
            approval_id="approval-1",
            action_id="call-approval-1",
            target_item_id="call-approval-1",
            action_kind=ApprovalActionKind.COMMAND,
        ),
        action_fingerprint=approval_action_fingerprint(
            _payload("run-1", "approval-1"),
            "command",
        ),
        status=ApprovalReviewStatus.APPROVED,
        event_seq=2,
        presentation_epoch=1,
        started_at_ms=100,
        completed_at_ms=150,
        risk_level=ApprovalReviewRiskLevel.LOW,
        user_authorization=ApprovalReviewUserAuthorization.HIGH,
        rationale="The action is limited to a harmless local command.",
    ))

    outcome = await bridge.request_outcome(_payload("run-1", "approval-1"))

    assert outcome.decision == "accept"
    assert outcome.source == "auto_review"
    assert interaction.calls == []
    await bridge.close()


@pytest.mark.anyio
async def test_local_mcp_persistent_approval_writes_before_fact_resolution(
    tmp_path,
) -> None:
    class PersistentApprovals:
        def __init__(self) -> None:
            self.tools: list[str] = []

        async def approve_tool(self, descriptor: McpToolDescriptor) -> None:
            self.tools.append(descriptor.tool_name)

    interaction = _Interaction("acceptAndRemember")
    persistent = PersistentApprovals()
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(interaction),
        fact_store=SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3"),
        grant_store=InMemorySessionGrantStore(),
        persistent_mcp_approvals=persistent,
    )
    first_action = _mcp_action("first")
    second_action = _mcp_action("second")

    first = await bridge.request_action_outcome(
        first_action,
        mcp_approval_payload(first_action, {"value": 1}),
    )
    second = await bridge.request_action_outcome(
        second_action,
        mcp_approval_payload(second_action, {"value": 2}),
    )

    assert first.decision == "acceptAndRemember"
    assert second.decision == "acceptForSession"
    assert persistent.tools == ["publish"]
    assert interaction.calls == ["approval-first"]
    await bridge.close()


@pytest.mark.anyio
async def test_local_mcp_requested_fact_is_represented_after_restart(tmp_path) -> None:
    action = _mcp_action("replayed")
    facts = SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3")
    await facts.record_requested(action)
    interaction = _Interaction("accept")
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(interaction),
        fact_store=facts,
        grant_store=InMemorySessionGrantStore(),
    )

    outcome = await bridge.request_action_outcome(
        action,
        mcp_approval_payload(action, {"value": "same"}),
    )

    assert outcome.decision == "accept"
    assert interaction.calls == ["approval-replayed"]
    await bridge.close()


@pytest.mark.anyio
async def test_failed_mcp_policy_persistence_leaves_fact_requested(tmp_path) -> None:
    class FailingPersistentApprovals:
        async def approve_tool(self, descriptor: McpToolDescriptor) -> None:
            raise OSError(f"cannot persist {descriptor.tool_name}")

    action = _mcp_action("persistence-failed")
    facts = SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3")
    bridge = DomainApprovalCoordinator(
        ApprovalCoordinator(_Interaction("acceptAndRemember")),
        fact_store=facts,
        grant_store=InMemorySessionGrantStore(),
        persistent_mcp_approvals=FailingPersistentApprovals(),
    )

    with pytest.raises(OSError, match="cannot persist"):
        await bridge.request_action_outcome(
            action,
            mcp_approval_payload(action, {"value": 1}),
        )

    fact = await facts.find(action.identity)
    assert fact is not None
    assert fact.state is ApprovalFactState.REQUESTED
    await bridge.close()
