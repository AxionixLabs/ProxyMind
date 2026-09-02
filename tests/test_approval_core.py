# -*- coding: utf-8 -*-

import asyncio
from dataclasses import dataclass

import pytest

from agent.application.approvals.core import (
    ApprovalCore,
    ApprovalPolicyEvaluator,
    PresentationArbiter,
    ReviewerBinding,
    ReviewerChain,
    RunApprovalRegistry,
)
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalActionKind,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalFactState,
    ApprovalIdentity,
    CommandApprovalAction,
    ExecutionIdentity,
    McpApprovalAction,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
    ApprovalResolutionReason,
)
from agent.ports.persistence import EffectJournalDecision
from agent.stores.approvals.facts import (
    InMemoryApprovalFactStore,
    SQLiteApprovalFactStore,
)
from agent.stores.approvals.grants import InMemorySessionGrantStore


def _action(suffix: str = "one") -> CommandApprovalAction:
    return CommandApprovalAction(
        identity=ApprovalIdentity(
            session_id="session-1",
            run_id=f"run-{suffix}",
            approval_id=f"approval-{suffix}",
            action_id=f"action-{suffix}",
        ),
        execution=ExecutionIdentity(
            environment_id="workspace-write",
            execution_id=f"execution-{suffix}",
            tool_call_id=f"call-{suffix}",
        ),
        fingerprint=ActionFingerprint("a" * 64),
        command=("echo", suffix),
        cwd="D:/workspace",
    )


class _Presentation:
    def __init__(self, decision: ApprovalDecisionKind) -> None:
        self.decision = decision
        self.calls = 0

    async def present(self, action: CommandApprovalAction) -> ApprovalDecision:
        self.calls += 1
        return ApprovalDecision(
            kind=self.decision,
            action_fingerprint=action.fingerprint,
        )


class _Reviewer:
    def __init__(self, decision: ApprovalDecision | None) -> None:
        self.decision = decision

    async def review(self, action: CommandApprovalAction) -> ApprovalDecision | None:
        if self.decision is None:
            return None
        return ApprovalDecision(
            kind=self.decision.kind,
            action_fingerprint=action.fingerprint,
            amendment=self.decision.amendment,
        )


@pytest.mark.anyio
async def test_core_policy_decision_is_persisted_without_presentation() -> None:
    action = _action()
    presentation = _Presentation(ApprovalDecisionKind.DECLINE)
    core = ApprovalCore(
        InMemoryApprovalFactStore(),
        InMemorySessionGrantStore(),
        policy=ApprovalPolicyEvaluator({
            ApprovalActionKind.COMMAND: ApprovalDecisionKind.ALLOW_ONCE,
        }),
        presentation=PresentationArbiter(presentation),
        clock=lambda: 10.0,
    )

    fact = await core.request(action)

    assert fact.state is ApprovalFactState.RESOLVED
    assert fact.outcome is not None
    assert fact.outcome.decision.kind is ApprovalDecisionKind.ALLOW_ONCE
    assert fact.outcome.source is ApprovalDecisionSource.POLICY
    assert presentation.calls == 0


@pytest.mark.anyio
async def test_reviewer_chain_fails_closed_and_skips_inapplicable_reviewers() -> None:
    action = _action()
    unavailable = ReviewerChain((
        ReviewerBinding(
            source=ApprovalDecisionSource.HOOK,
            reviewer=_Reviewer(None),
        ),
        ReviewerBinding(
            source=ApprovalDecisionSource.AUTO_REVIEW,
            reviewer=_Reviewer(
                ApprovalDecision(
                    kind=ApprovalDecisionKind.ALLOW_ONCE,
                    action_fingerprint=action.fingerprint,
                )
            ),
        ),
    ))
    result = await unavailable.review(action)
    assert result is not None
    assert result.source is ApprovalDecisionSource.AUTO_REVIEW

    class FailingReviewer:
        async def review(self, action: CommandApprovalAction) -> ApprovalDecision:
            raise RuntimeError("reviewer unavailable")

    failed = await ReviewerChain((
        ReviewerBinding(
            source=ApprovalDecisionSource.HOOK,
            reviewer=FailingReviewer(),
        ),
    )).review(action)
    assert failed is not None
    assert failed.decision.kind is ApprovalDecisionKind.UNAVAILABLE
    assert failed.reason is ApprovalResolutionReason.UNAVAILABLE


@pytest.mark.anyio
async def test_registry_deduplicates_same_identity_and_rejects_conflict() -> None:
    action = _action()
    registry = RunApprovalRegistry()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def operation():
        calls.append("request")
        started.set()
        await release.wait()
        from agent.domain.approvals import ApprovalFact

        return ApprovalFact.requested(action)

    first = asyncio.create_task(registry.run(action, operation))
    await started.wait()
    second = asyncio.create_task(registry.run(action, operation))
    conflict = CommandApprovalAction(
        identity=action.identity,
        execution=action.execution,
        fingerprint=ActionFingerprint("b" * 64),
        command=action.command,
        cwd=action.cwd,
    )
    with pytest.raises(ValueError, match="identity"):
        await registry.run(conflict, operation)

    release.set()
    assert (await first) == (await second)
    assert calls == ["request"]


@pytest.mark.anyio
async def test_registry_shares_identical_mcp_actions_with_distinct_call_ids() -> None:
    registry = RunApprovalRegistry()
    descriptor = McpToolDescriptor(
        server="docs",
        exposed_name="mcp__docs__lookup",
        tool_name="lookup",
        schema_fingerprint=ActionFingerprint("schema"),
        annotations=McpToolAnnotations(read_only_hint=False),
        policy=McpApprovalPolicy(McpApprovalMode.PROMPT),
    )

    def action(call_id: str) -> McpApprovalAction:
        return McpApprovalAction(
            identity=ApprovalIdentity(
                session_id="session-1",
                run_id="run-mcp",
                approval_id=f"approval-{call_id}",
                action_id=call_id,
            ),
            execution=ExecutionIdentity(
                environment_id="workspace-write",
                execution_id="run-mcp",
                tool_call_id=call_id,
            ),
            fingerprint=ActionFingerprint("mcp-action"),
            descriptor=descriptor,
            arguments_fingerprint=ActionFingerprint("arguments"),
        )

    first_action = action("call-1")
    second_action = action("call-2")
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def operation():
        calls.append("request")
        started.set()
        await release.wait()
        from agent.domain.approvals import ApprovalFact

        return ApprovalFact.requested(first_action)

    first = asyncio.create_task(registry.run(first_action, operation))
    await started.wait()
    second = asyncio.create_task(registry.run(second_action, operation))
    release.set()

    assert await first == await second
    assert calls == ["request"]


@pytest.mark.anyio
async def test_session_grant_is_reused_for_later_run() -> None:
    presentation = _Presentation(ApprovalDecisionKind.ALLOW_FOR_SESSION)
    grants = InMemorySessionGrantStore()
    core = ApprovalCore(
        InMemoryApprovalFactStore(),
        grants,
        presentation=PresentationArbiter(presentation),
        clock=lambda: 20.0,
    )

    first = await core.request(_action("first"))
    second = await core.request(_action("second"))

    assert first.outcome is not None and second.outcome is not None
    assert first.outcome.decision.kind is ApprovalDecisionKind.ALLOW_FOR_SESSION
    assert second.outcome.decision.kind is ApprovalDecisionKind.ALLOW_FOR_SESSION
    assert presentation.calls == 1


class _McpPresentation:
    """记录 MCP 核心展示次数并返回会话决定。"""

    def __init__(self) -> None:
        self.calls = 0

    async def present(self, action: McpApprovalAction) -> ApprovalDecision:
        self.calls += 1
        return ApprovalDecision(
            kind=ApprovalDecisionKind.ALLOW_FOR_SESSION,
            action_fingerprint=action.fingerprint,
        )


def _mcp_action(
    suffix: str,
    *,
    session_id: str = "session-mcp",
    environment_id: str = "workspace-write",
    connector_id: str | None = "docs-connector",
) -> McpApprovalAction:
    descriptor = McpToolDescriptor(
        server="docs",
        exposed_name="mcp__docs__lookup",
        tool_name="lookup",
        schema_fingerprint=ActionFingerprint("schema"),
        annotations=McpToolAnnotations(read_only_hint=False),
        policy=McpApprovalPolicy(McpApprovalMode.PROMPT),
        connector_id=connector_id,
    )
    return McpApprovalAction(
        identity=ApprovalIdentity(
            session_id=session_id,
            run_id=f"run-{suffix}",
            approval_id=f"approval-{suffix}",
            action_id=f"call-{suffix}",
        ),
        execution=ExecutionIdentity(
            environment_id=environment_id,
            execution_id=f"run-{suffix}",
            tool_call_id=f"call-{suffix}",
        ),
        fingerprint=ActionFingerprint(f"action-{suffix}"),
        descriptor=descriptor,
        arguments_fingerprint=ActionFingerprint(f"arguments-{suffix}"),
    )


@pytest.mark.anyio
async def test_mcp_session_grant_ignores_arguments_but_keeps_scope_isolated() -> None:
    presentation = _McpPresentation()
    core = ApprovalCore(
        InMemoryApprovalFactStore(),
        InMemorySessionGrantStore(),
        presentation=PresentationArbiter(presentation),
    )

    first = await core.request(_mcp_action("first"))
    same_scope = await core.request(_mcp_action("other-arguments"))
    other_environment = await core.request(_mcp_action(
        "other-environment",
        environment_id="full-access",
    ))
    other_connector = await core.request(_mcp_action(
        "other-connector",
        connector_id="other",
    ))
    other_session = await core.request(_mcp_action(
        "other-session",
        session_id="session-other",
    ))

    assert first.outcome is not None and same_scope.outcome is not None
    assert same_scope.outcome.source is ApprovalDecisionSource.POLICY
    assert other_environment.outcome is not None
    assert other_connector.outcome is not None
    assert other_session.outcome is not None
    assert presentation.calls == 4


@dataclass
class _Journal:
    decision: EffectJournalDecision
    committed: list[dict[str, object]]
    unknown: list[str]

    async def begin(self, effect):
        return self.decision

    async def commit(self, effect, result_payload):
        self.committed.append(result_payload)

    async def mark_unknown(self, effect, error, *, result_payload=None):
        self.unknown.append(type(error).__name__)


@pytest.mark.anyio
async def test_effect_is_committed_only_after_approval_and_execution() -> None:
    action = _action("effect")
    core = ApprovalCore(
        InMemoryApprovalFactStore(),
        InMemorySessionGrantStore(),
        policy=ApprovalPolicyEvaluator({
            ApprovalActionKind.COMMAND: ApprovalDecisionKind.ALLOW_ONCE,
        }),
    )
    journal = _Journal(EffectJournalDecision("execute"), [], [])
    calls: list[str] = []

    result = await core.execute_effect(
        action,
        effect=type("Effect", (), {
            "effect_id": "effect-1",
            "fingerprint": "a" * 64,
            "replay": "safe",
        })(),
        execute=lambda: _effect_payload(calls),
        journal=journal,
    )

    assert result.result_payload == {"ok": True}
    assert calls == ["execute"]
    assert journal.committed == [{"ok": True}]
    assert journal.unknown == []


async def _effect_payload(calls: list[str]) -> dict[str, object]:
    calls.append("execute")
    return {"ok": True}


@pytest.mark.anyio
async def test_sqlite_fact_store_preserves_first_terminal_decision(tmp_path) -> None:
    store = SQLiteApprovalFactStore(tmp_path / "approvals.sqlite3")
    action = _action("sqlite")
    await store.record_requested(action)

    decision = ApprovalDecision(
        kind=ApprovalDecisionKind.DECLINE,
        action_fingerprint=action.fingerprint,
    )
    resolved = await store.resolve(
        action.identity,
        decision,
        source=ApprovalDecisionSource.USER,
        reason=ApprovalResolutionReason.USER,
        resolved_at=30.0,
    )
    loaded = await store.find(action.identity)

    assert loaded == resolved
    assert loaded is not None and loaded.version == 1
    assert await store.resolve(
        action.identity,
        decision,
        source=ApprovalDecisionSource.USER,
        reason=ApprovalResolutionReason.USER,
        resolved_at=31.0,
    ) == resolved

    with pytest.raises(ValueError, match="already terminal"):
        await store.resolve(
            action.identity,
            ApprovalDecision(
                kind=ApprovalDecisionKind.CANCEL,
                action_fingerprint=action.fingerprint,
            ),
            source=ApprovalDecisionSource.USER,
            reason=ApprovalResolutionReason.USER,
            resolved_at=32.0,
        )
