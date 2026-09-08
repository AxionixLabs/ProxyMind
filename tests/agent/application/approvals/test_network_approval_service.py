# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.models import ApprovalOutcome
from agent.application.approvals.network import NetworkApprovalService
from agent.domain.approvals import NetworkProtocol, NetworkTarget
from infrastructure.platform.network import (
    BlockedNetworkRequest,
    NetworkDecision,
    StaticNetworkPolicy,
)


class _Coordinator:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.requests: list[dict[str, object]] = []

    async def request_outcome(self, approval):
        self.requests.append(dict(approval))
        return ApprovalOutcome.create(
            self.decision,
            source="user",
            reason="user",
        )


def _blocked() -> BlockedNetworkRequest:
    return BlockedNetworkRequest(
        target=NetworkTarget(
            host="api.example.com",
            protocol=NetworkProtocol.HTTPS,
            port=443,
        ),
        reason="static_policy_denied",
    )


@pytest.mark.anyio
async def test_network_approval_card_has_typed_target_and_proposal() -> None:
    coordinator = _Coordinator("accept")
    service = NetworkApprovalService(coordinator, StaticNetworkPolicy())

    await service.request(
        _blocked(),
        session_id="session-1",
        run_id="run-1",
        environment_id="workspace-write",
        execution_id="execution-1",
    )

    approval = coordinator.requests[0]
    assert approval["kind"] == "network_access"
    assert approval["execution_id"] == "execution-1"
    assert approval["host"] == "api.example.com"
    assert approval["protocol"] == "https"
    assert approval["port"] == 443
    assert approval["available_decisions"] == [
        "accept",
        "acceptForSession",
        "applyNetworkPolicyAmendment",
        "decline",
    ]
    assert approval["proposed_network_policy_amendment"] == {
        "host": "api.example.com",
        "protocol": "https",
        "port": 443,
        "action": "allow",
    }


@pytest.mark.anyio
async def test_network_accept_is_one_shot() -> None:
    policy = StaticNetworkPolicy()
    service = NetworkApprovalService(_Coordinator("accept"), policy)

    await service.request(
        _blocked(),
        session_id="session-1",
        run_id="run-1",
        environment_id="workspace-write",
        execution_id="execution-1",
    )

    assert policy.decide(_blocked().target) is NetworkDecision.ALLOW
    assert policy.decide(_blocked().target) is NetworkDecision.DENY


@pytest.mark.anyio
async def test_network_session_grant_is_scoped() -> None:
    policy = StaticNetworkPolicy()
    service = NetworkApprovalService(_Coordinator("acceptForSession"), policy)

    await service.request(
        _blocked(),
        session_id="session-1",
        run_id="run-1",
        environment_id="workspace-write",
        execution_id="execution-1",
    )

    assert policy.decide(
        _blocked().target,
        session_id="session-1",
    ) is NetworkDecision.ALLOW
    assert policy.decide(
        _blocked().target,
        session_id="session-2",
    ) is NetworkDecision.DENY


@pytest.mark.anyio
async def test_network_amendment_persists_before_runtime_install() -> None:
    policy = StaticNetworkPolicy()
    persisted = []

    async def sink(rule) -> None:
        persisted.append(rule)

    service = NetworkApprovalService(
        _Coordinator("applyNetworkPolicyAmendment"),
        policy,
        rule_sink=sink,
    )

    await service.request(
        _blocked(),
        session_id="session-1",
        run_id="run-1",
        environment_id="workspace-write",
        execution_id="execution-1",
    )

    assert len(persisted) == 1
    assert policy.decide(_blocked().target) is NetworkDecision.ALLOW


def test_network_handler_requires_stable_identity() -> None:
    with pytest.raises(ValueError, match="identity"):
        NetworkApprovalService(
            _Coordinator("decline"),
            StaticNetworkPolicy(),
        ).handler(
            session_id="",
            run_id="run-1",
            environment_id="workspace-write",
            execution_id="execution-1",
        )


@pytest.mark.anyio
async def test_network_request_rejects_missing_execution_identity() -> None:
    service = NetworkApprovalService(_Coordinator("decline"), StaticNetworkPolicy())

    with pytest.raises(ValueError, match="identity"):
        await service.request(
            _blocked(),
            session_id="session-1",
            run_id="run-1",
            environment_id="workspace-write",
            execution_id="",
        )


@pytest.mark.anyio
async def test_network_approval_identity_isolated_per_execution() -> None:
    coordinator = _Coordinator("decline")
    service = NetworkApprovalService(coordinator, StaticNetworkPolicy())

    for execution_id in ("execution-1", "execution-2"):
        await service.request(
            _blocked(),
            session_id="session-1",
            run_id="run-1",
            environment_id="workspace-write",
            execution_id=execution_id,
        )

    assert [item["execution_id"] for item in coordinator.requests] == [
        "execution-1",
        "execution-2",
    ]
    assert coordinator.requests[0]["request_id"] != coordinator.requests[1]["request_id"]
