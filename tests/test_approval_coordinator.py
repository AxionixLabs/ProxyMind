# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.application.approvals.coordinator import ApprovalCoordinator
from frontends.interaction.noninteractive import NonInteractiveInteraction


class ControlledInteraction(object):
    approval_source = "user"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.snapshots = []
        self.started: dict[str, asyncio.Event] = {}
        self.decisions: dict[str, asyncio.Future[str]] = {}
        self.session_started = 0
        self.session_ended = 0
        self.ended = asyncio.Event()

    async def begin_approval_session(self) -> None:
        self.session_started += 1

    def approval_snapshot_changed(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    async def present_approval(self, request):
        request_id = request.key.approval_id or request.key.request_id
        self.calls.append(request_id)
        self.started.setdefault(request_id, asyncio.Event()).set()
        future = self.decisions.setdefault(
            request_id,
            asyncio.get_running_loop().create_future(),
        )
        return await future

    async def end_approval_session(self) -> None:
        self.session_ended += 1
        self.ended.set()

    async def wait_started(self, request_id: str) -> None:
        event = self.started.setdefault(request_id, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=1)

    def finish(self, request_id: str, decision: str) -> None:
        future = self.decisions[request_id]
        if not future.done():
            future.set_result(decision)


@pytest.mark.anyio
async def test_approval_coordinator_presents_fifo_in_one_session() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")
    second = asyncio.create_task(coordinator.request({"id": "second"}))
    await asyncio.sleep(0)

    assert interaction.calls == ["first"]
    assert coordinator.snapshot.current is not None
    assert coordinator.snapshot.current.key.request_id == "first"
    assert coordinator.snapshot.pending_count == 1

    interaction.finish("first", "accept")
    assert await first == "accept"
    await interaction.wait_started("second")
    interaction.finish("second", "decline")

    assert await second == "decline"
    await asyncio.wait_for(interaction.ended.wait(), timeout=1)
    assert interaction.calls == ["first", "second"]
    assert interaction.session_started == 1
    assert interaction.session_ended == 1
    assert coordinator.snapshot.unresolved_count == 0
    assert interaction.snapshots[-1].unresolved_count == 0


@pytest.mark.anyio
async def test_approval_cancellation_releases_next_request() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")
    second = asyncio.create_task(coordinator.request({"id": "second"}))
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    await interaction.wait_started("second")
    interaction.finish("second", "decline")
    assert await second == "decline"


@pytest.mark.anyio
async def test_duplicate_identity_shares_one_decision_future() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    approval = {
        "id": "approval-shared",
        "call_id": "call-shared",
        "tool": "shell_command",
        "command": "echo shared",
    }
    first = asyncio.create_task(coordinator.request_outcome(approval))
    await interaction.wait_started("approval-shared")
    second = asyncio.create_task(coordinator.request_outcome(approval))
    await asyncio.sleep(0)

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert coordinator.snapshot.unresolved_count == 1

    interaction.finish("approval-shared", "accept")
    outcome = await first

    assert outcome.decision == "accept"
    assert outcome.reason == "user"
    assert interaction.calls == ["approval-shared"]


@pytest.mark.anyio
async def test_presenter_cannot_mutate_authoritative_request() -> None:
    class MutatingInteraction(ControlledInteraction):
        async def present_approval(self, request):
            assert request.presentation.context.kind == "command"
            return await super().present_approval(request)

    interaction = MutatingInteraction()
    coordinator = ApprovalCoordinator(interaction)
    approval = {
        "id": "approval-shared",
        "tool": "shell_command",
        "command": "echo original",
    }
    first = asyncio.create_task(coordinator.request(approval))
    await interaction.wait_started("approval-shared")
    second = asyncio.create_task(coordinator.request(dict(approval)))
    await asyncio.sleep(0)

    interaction.finish("approval-shared", "accept")

    assert await asyncio.gather(first, second) == ["accept", "accept"]
    assert interaction.calls == ["approval-shared"]


@pytest.mark.anyio
async def test_duplicate_identity_rejects_changed_request() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({
        "id": "approval-same",
        "tool": "shell_command",
        "command": "echo safe",
    }))
    await interaction.wait_started("approval-same")

    conflict = await coordinator.request_outcome({
        "id": "approval-same",
        "tool": "shell_command",
        "command": "echo changed",
    })

    assert conflict.decision == "decline"
    assert conflict.source == "policy"
    assert conflict.reason == "identity_conflict"
    interaction.finish("approval-same", "decline")
    assert await first == "decline"


@pytest.mark.anyio
async def test_queued_approval_waits_for_explicit_resolution() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")
    queued = asyncio.create_task(coordinator.request_outcome({"id": "queued"}))
    await asyncio.sleep(0)

    assert not queued.done()
    assert interaction.calls == ["first"]
    assert await coordinator.resolve("queued", "decline", source="policy")
    outcome = await queued
    assert outcome.decision == "decline"
    assert outcome.source == "policy"
    assert outcome.reason == "external"
    interaction.finish("first", "decline")
    assert await first == "decline"


@pytest.mark.anyio
async def test_external_resolution_removes_queued_request_by_call_id() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")
    queued = asyncio.create_task(coordinator.request_outcome({
        "id": "queued",
        "call_id": "call-queued",
    }))
    await asyncio.sleep(0)

    assert await coordinator.resolve(
        "call-queued",
        "accept",
        source="policy",
    )
    outcome = await queued

    assert outcome.decision == "accept"
    assert outcome.reason == "external"
    assert interaction.calls == ["first"]
    assert not await coordinator.resolve("call-queued", "decline")
    interaction.finish("first", "decline")
    assert await first == "decline"


@pytest.mark.anyio
async def test_external_resolution_rejects_ambiguous_secondary_id() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({
        "id": "approval-first",
        "call_id": "shared-call",
    }))
    await interaction.wait_started("approval-first")
    second = asyncio.create_task(coordinator.request({
        "id": "approval-second",
        "call_id": "shared-call",
    }))
    await asyncio.sleep(0)

    assert not await coordinator.resolve("shared-call", "accept")
    interaction.finish("approval-first", "decline")
    assert await first == "decline"
    await interaction.wait_started("approval-second")
    interaction.finish("approval-second", "decline")
    assert await second == "decline"


@pytest.mark.anyio
async def test_external_resolution_rejects_cross_namespace_collision() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({
        "id": "approval-first",
        "request_id": "shared-identity",
    }))
    await interaction.wait_started("approval-first")
    second = asyncio.create_task(coordinator.request({
        "id": "shared-identity",
        "request_id": "request-second",
    }))
    await asyncio.sleep(0)

    assert not await coordinator.resolve("shared-identity", "accept")
    interaction.finish("approval-first", "decline")
    assert await first == "decline"
    await interaction.wait_started("shared-identity")
    interaction.finish("shared-identity", "decline")
    assert await second == "decline"


@pytest.mark.anyio
async def test_queue_limit_declines_overload_without_presenting_it() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction, queue_limit=1)
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")

    outcome = await coordinator.request_outcome({"id": "overflow"})

    assert outcome.decision == "decline"
    assert outcome.source == "policy"
    assert outcome.reason == "overloaded"
    assert interaction.calls == ["first"]
    interaction.finish("first", "decline")
    assert await first == "decline"


@pytest.mark.anyio
async def test_snapshot_observer_failure_does_not_break_approval() -> None:
    class FaultyObserverInteraction(ControlledInteraction):
        def approval_snapshot_changed(self, snapshot) -> None:
            _ = snapshot
            raise RuntimeError("snapshot observer failed")

    interaction = FaultyObserverInteraction()
    observed: list[tuple[BaseException, str, int]] = []

    def observe_failure(
        error: BaseException,
        coordinator_id: str,
        revision: int,
    ) -> None:
        observed.append((error, coordinator_id, revision))

    coordinator = ApprovalCoordinator(
        interaction,
        snapshot_error_handler=observe_failure,
    )
    request = asyncio.create_task(coordinator.request({"id": "first"}))
    await interaction.wait_started("first")

    interaction.finish("first", "accept")

    assert await request == "accept"
    await asyncio.wait_for(interaction.ended.wait(), timeout=1)
    assert coordinator.snapshot.unresolved_count == 0
    assert observed
    assert all(isinstance(item[0], RuntimeError) for item in observed)
    assert len({item[1] for item in observed}) == 1
    assert [item[2] for item in observed] == sorted(item[2] for item in observed)


@pytest.mark.anyio
async def test_noninteractive_decline_has_policy_outcome() -> None:
    coordinator = ApprovalCoordinator(NonInteractiveInteraction())

    outcome = await coordinator.request_outcome({"id": "first"})

    assert outcome.decision == "decline"
    assert outcome.source == "policy"
    assert outcome.reason == "policy"
    await coordinator.close()


@pytest.mark.anyio
async def test_close_settles_current_and_pending_requests() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request_outcome({"id": "first"}))
    await interaction.wait_started("first")
    second = asyncio.create_task(coordinator.request_outcome({"id": "second"}))
    await asyncio.sleep(0)

    await coordinator.close()
    outcomes = await asyncio.gather(first, second)

    assert [outcome.decision for outcome in outcomes] == [
        "decline",
        "decline",
    ]
    assert {outcome.reason for outcome in outcomes} == {"closed"}
    assert coordinator.snapshot.closed
    assert coordinator.snapshot.unresolved_count == 0


@pytest.mark.anyio
async def test_close_cancels_blocked_session_start() -> None:
    class BlockingInteraction(ControlledInteraction):
        def __init__(self) -> None:
            super().__init__()
            self.begin_started = asyncio.Event()
            self.begin_cancelled = asyncio.Event()

        async def begin_approval_session(self) -> None:
            self.begin_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.begin_cancelled.set()

    interaction = BlockingInteraction()
    coordinator = ApprovalCoordinator(interaction)
    request = asyncio.create_task(coordinator.request_outcome({"id": "first"}))
    await asyncio.wait_for(interaction.begin_started.wait(), timeout=1)

    await asyncio.wait_for(coordinator.close(), timeout=1)
    outcome = await request

    assert outcome.decision == "decline"
    assert outcome.reason == "closed"
    assert interaction.begin_cancelled.is_set()
    assert coordinator.snapshot.unresolved_count == 0


@pytest.mark.anyio
async def test_last_caller_cancellation_stops_empty_session_start() -> None:
    class BlockingInteraction(ControlledInteraction):
        def __init__(self) -> None:
            super().__init__()
            self.begin_started = asyncio.Event()
            self.begin_cancelled = asyncio.Event()

        async def begin_approval_session(self) -> None:
            self.begin_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.begin_cancelled.set()

    interaction = BlockingInteraction()
    coordinator = ApprovalCoordinator(interaction)
    request = asyncio.create_task(coordinator.request({"id": "first"}))
    await asyncio.wait_for(interaction.begin_started.wait(), timeout=1)

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    await asyncio.wait_for(interaction.begin_cancelled.wait(), timeout=1)
    assert coordinator.snapshot.unresolved_count == 0
    assert interaction.calls == []
    await coordinator.close()


@pytest.mark.anyio
async def test_restore_pending_reuses_fifo_worker_without_waiter() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    approval = {
        "id": "restored",
        "approval_id": "restored",
        "call_id": "call-restored",
        "turn_id": "turn-restored",
        "tool": "exec_command",
        "kind": "command",
        "command": "echo restored",
        "available_decisions": ["accept", "decline"],
    }

    assert await coordinator.restore_pending(approval)
    await interaction.wait_started("restored")
    assert coordinator.snapshot.current is not None
    assert coordinator.snapshot.current.key.call_id == "call-restored"

    assert await coordinator.resolve("restored", "accept", source="user")
    await asyncio.wait_for(interaction.ended.wait(), timeout=1)
    assert coordinator.snapshot.unresolved_count == 0
    assert interaction.calls == ["restored"]


@pytest.mark.anyio
async def test_restore_pending_deduplicates_matching_request() -> None:
    interaction = ControlledInteraction()
    coordinator = ApprovalCoordinator(interaction)
    approval = {
        "id": "restored",
        "tool": "exec_command",
        "command": "echo restored",
    }

    assert await coordinator.restore_pending(approval)
    assert not await coordinator.restore_pending(dict(approval))
    await interaction.wait_started("restored")
    interaction.finish("restored", "decline")

    await asyncio.wait_for(interaction.ended.wait(), timeout=1)
    assert interaction.calls == ["restored"]
