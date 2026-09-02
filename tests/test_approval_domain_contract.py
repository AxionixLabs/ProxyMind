# -*- coding: utf-8 -*-

import dataclasses

import pytest

from agent.domain.approvals import (
    ActionFingerprint,
    AmendmentOperation,
    ApprovalActionKind,
    ApprovalAmendment,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalFact,
    ApprovalFactState,
    ApprovalGrantKey,
    ApprovalIdentity,
    ApprovalResolutionReason,
    CommandApprovalAction,
    ExecutionIdentity,
    McpApprovalGrantKey,
    McpApprovalMode,
    McpApprovalPolicy,
    McpApprovalRisk,
    McpToolAnnotations,
    McpToolDescriptor,
    NetworkApprovalAction,
    NetworkProtocol,
    NetworkTarget,
    PatchApprovalAction,
    PermissionApprovalAction,
    SessionGrant,
    allowed_decisions,
    is_terminal_decision,
    validate_decision,
    mcp_approval_risk,
    mcp_requires_approval,
)


def _identity(suffix: str = "1") -> ApprovalIdentity:
    return ApprovalIdentity(
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        approval_id=f"approval-{suffix}",
        action_id=f"action-{suffix}",
    )


def _execution(suffix: str = "1") -> ExecutionIdentity:
    return ExecutionIdentity(
        environment_id=f"environment-{suffix}",
        execution_id=f"execution-{suffix}",
        tool_call_id=f"tool-call-{suffix}",
    )


def _fingerprint(suffix: str = "1") -> ActionFingerprint:
    return ActionFingerprint(f"sha256:{suffix}")


def _command(suffix: str = "1") -> CommandApprovalAction:
    return CommandApprovalAction(
        identity=_identity(suffix),
        execution=_execution(suffix),
        fingerprint=_fingerprint(suffix),
        command=("python", "-c", "print('ok')"),
        cwd="D:/workspace",
    )


def test_action_value_objects_are_immutable_and_scoped() -> None:
    action = _command()

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(action, "cwd", "D:/other")

    key = ApprovalGrantKey.from_action(action)
    assert key.session_id == "session-1"
    assert key.environment_id == "environment-1"
    assert key.action_kind is ApprovalActionKind.COMMAND
    assert key.action_fingerprint == action.fingerprint


def test_action_specific_decisions_are_explicit() -> None:
    command_decisions = allowed_decisions(ApprovalActionKind.COMMAND)
    patch_decisions = allowed_decisions(ApprovalActionKind.PATCH)
    permission_decisions = allowed_decisions(ApprovalActionKind.PERMISSION)
    network_decisions = allowed_decisions(ApprovalActionKind.NETWORK)

    assert ApprovalDecisionKind.APPLY_AMENDMENT in command_decisions
    assert ApprovalDecisionKind.APPLY_AMENDMENT not in patch_decisions
    assert ApprovalDecisionKind.GRANT_FOR_RUN in permission_decisions
    assert ApprovalDecisionKind.GRANT_FOR_RUN not in network_decisions

    permission = PermissionApprovalAction(
        identity=_identity("permission"),
        execution=_execution("permission"),
        fingerprint=_fingerprint("permission"),
        permission_scope="network:restricted",
        reason="需要访问受管网络",
    )
    once = ApprovalDecision(
        kind=ApprovalDecisionKind.ALLOW_ONCE,
        action_fingerprint=permission.fingerprint,
    )

    with pytest.raises(ValueError, match="not valid"):
        validate_decision(permission, once)


def test_amendment_must_match_the_action_fingerprint() -> None:
    action = NetworkApprovalAction(
        identity=_identity("network"),
        execution=_execution("network"),
        fingerprint=_fingerprint("network"),
        target=NetworkTarget(
            host="api.example.com",
            protocol=NetworkProtocol.HTTPS,
            port=443,
        ),
        reason="目标不在静态允许列表",
    )
    decision = ApprovalDecision(
        kind=ApprovalDecisionKind.APPLY_AMENDMENT,
        action_fingerprint=action.fingerprint,
        amendment=ApprovalAmendment(
            operation=AmendmentOperation.ALLOW,
            action_fingerprint=_fingerprint("other"),
        ),
    )

    with pytest.raises(ValueError, match="amendment"):
        validate_decision(action, decision)

    with pytest.raises(ValueError, match="amendment"):
        ApprovalFact.requested(action).resolve(
            decision,
            source=ApprovalDecisionSource.USER,
            reason=ApprovalResolutionReason.USER,
            resolved_at=100.0,
        )


def test_approval_fact_has_first_terminal_state() -> None:
    action = _command()
    requested = ApprovalFact.requested(action)

    assert requested.state is ApprovalFactState.REQUESTED
    assert requested.version == 0
    assert requested.outcome is None

    decision = ApprovalDecision(
        kind=ApprovalDecisionKind.ALLOW_ONCE,
        action_fingerprint=action.fingerprint,
    )
    resolved = requested.resolve(
        decision,
        source=ApprovalDecisionSource.USER,
        reason=ApprovalResolutionReason.USER,
        resolved_at=100.0,
    )

    assert resolved.state is ApprovalFactState.RESOLVED
    assert resolved.version == 1
    assert resolved.outcome is not None
    assert resolved.outcome.decision == decision
    assert is_terminal_decision(
        ApprovalDecision(
            kind=ApprovalDecisionKind.CANCEL,
            action_fingerprint=action.fingerprint,
        )
    )

    with pytest.raises(ValueError, match="already terminal"):
        resolved.resolve(
            decision,
            source=ApprovalDecisionSource.USER,
            reason=ApprovalResolutionReason.USER,
            resolved_at=101.0,
        )

    with pytest.raises(ValueError, match="already terminal"):
        resolved.abandon(
            source=ApprovalDecisionSource.POLICY,
            resolved_at=102.0,
        )


def test_abandoned_fact_cannot_be_reopened_or_granted() -> None:
    action = _command("abandoned")
    abandoned = ApprovalFact.requested(action).abandon(
        source=ApprovalDecisionSource.POLICY,
        resolved_at=200.0,
    )

    assert abandoned.state is ApprovalFactState.ABANDONED
    assert abandoned.outcome is not None
    assert abandoned.outcome.decision.kind is ApprovalDecisionKind.ABANDONED


def test_session_grant_requires_matching_session_scope() -> None:
    action = PatchApprovalAction(
        identity=_identity("patch"),
        execution=_execution("patch"),
        fingerprint=_fingerprint("patch"),
        files=("README.md",),
        summary="更新文档",
    )
    key = ApprovalGrantKey.from_action(action)
    decision = ApprovalDecision(
        kind=ApprovalDecisionKind.ALLOW_FOR_SESSION,
        action_fingerprint=action.fingerprint,
    )
    grant = SessionGrant(key=key, decision=decision, granted_at=300.0)

    assert grant.key == key
    assert grant.decision.kind is ApprovalDecisionKind.ALLOW_FOR_SESSION

    with pytest.raises(ValueError, match="session grant"):
        SessionGrant(
            key=key,
            decision=ApprovalDecision(
                kind=ApprovalDecisionKind.ALLOW_ONCE,
                action_fingerprint=action.fingerprint,
            ),
            granted_at=301.0,
        )


def test_identity_and_network_target_reject_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="session_id"):
        ApprovalIdentity(
            session_id=" ",
            run_id="run",
            approval_id="approval",
            action_id="action",
        )

    with pytest.raises(ValueError, match="port"):
        NetworkTarget(
            host="api.example.com",
            protocol=NetworkProtocol.HTTPS,
            port=0,
        )

    with pytest.raises(ValueError, match="tuple"):
        CommandApprovalAction(
            identity=_identity("list"),
            execution=_execution("list"),
            fingerprint=_fingerprint("list"),
            command=["python"],
            cwd="D:/workspace",
        )

    with pytest.raises(ValueError, match="resolved_at"):
        ApprovalFact.requested(_command("timestamp")).resolve(
            ApprovalDecision(
                kind=ApprovalDecisionKind.ALLOW_ONCE,
                action_fingerprint=_fingerprint("timestamp"),
            ),
            source=ApprovalDecisionSource.USER,
            reason=ApprovalResolutionReason.USER,
            resolved_at=float("nan"),
        )


def _mcp_descriptor(
    mode: McpApprovalMode,
    *,
    read_only: bool | None = None,
    destructive: bool | None = None,
    open_world: bool | None = None,
) -> McpToolDescriptor:
    return McpToolDescriptor(
        server="github",
        exposed_name="mcp__github__create_issue",
        tool_name="create_issue",
        schema_fingerprint=ActionFingerprint("schema:github:create_issue"),
        annotations=McpToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            open_world_hint=open_world,
        ),
        policy=McpApprovalPolicy(mode),
        title="Create issue",
        connector_id="github",
        transport="streamable_http",
    )


def test_mcp_policy_modes_use_conservative_unknown_annotation_rules() -> None:
    assert not mcp_requires_approval(_mcp_descriptor(McpApprovalMode.APPROVE))
    assert mcp_requires_approval(_mcp_descriptor(McpApprovalMode.PROMPT))
    assert not mcp_requires_approval(_mcp_descriptor(
        McpApprovalMode.WRITES,
        read_only=True,
    ))
    assert mcp_requires_approval(_mcp_descriptor(McpApprovalMode.WRITES))
    assert not mcp_requires_approval(_mcp_descriptor(
        McpApprovalMode.AUTO,
        read_only=True,
    ))
    assert mcp_requires_approval(_mcp_descriptor(McpApprovalMode.AUTO))
    assert not mcp_requires_approval(_mcp_descriptor(
        McpApprovalMode.AUTO,
        destructive=False,
        open_world=False,
    ))


def test_mcp_risk_and_grant_scope_keep_connector_identity() -> None:
    descriptor = _mcp_descriptor(
        McpApprovalMode.AUTO,
        destructive=True,
    )
    key = McpApprovalGrantKey(
        session_id="session-1",
        environment_id="workspace-write",
        server=descriptor.server,
        connector_id=descriptor.connector_id,
        tool_name=descriptor.tool_name,
    )

    assert mcp_approval_risk(descriptor.annotations) is McpApprovalRisk.DESTRUCTIVE
    assert key.connector_id == "github"
    assert key.tool_name == "create_issue"


def test_mcp_descriptor_rejects_untyped_annotations() -> None:
    with pytest.raises(ValueError, match="annotations"):
        McpToolDescriptor(
            server="github",
            exposed_name="mcp__github__create_issue",
            tool_name="create_issue",
            schema_fingerprint=ActionFingerprint("schema"),
            annotations={"read_only_hint": True},
            policy=McpApprovalPolicy(McpApprovalMode.AUTO),
        )
