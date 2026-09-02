# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
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
