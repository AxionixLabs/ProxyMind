# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.harness.sessions.conversation import ConversationState
from agent.harness.agents.runtime import SubagentRuntime
from agent.application.turns.run_result import RunResult
from agent.ports.session_deletion import (
    LocalDeletionRecord,
    LocalDeletionPlan,
    LocalDeletionTarget,
    RemoteDeletionReceipt,
    SessionDeletionRemoteError,
    SessionDeletionResult,
    SessionDeletionConflict,
)
from tests.composition.test_controller_runtime_cleanup import _root_session
from tests.agent.harness.agents.test_subagent_runtime_service import (
    _Controller,
    _parent_turn,
)
from tests.agent.stores.sessions.deletion_fixture import store as deletion_store


class _Remote:
    def __init__(self, *, error: SessionDeletionRemoteError | None = None) -> None:
        self.error = error
        self.requests = []
        self.recovery_requests = []

    async def delete(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return RemoteDeletionReceipt(
            request_id=request.request_id,
            root=request.root,
            targets=request.targets,
        )

    async def recover(self, request):
        self.recovery_requests.append(request)
        return RemoteDeletionReceipt(
            request_id=request.request_id,
            root=request.root,
            targets=request.targets,
        )


class _Store:
    def __init__(self) -> None:
        self.prepared = []
        self.deleted = []
        self.pending_plans = []
        self.fail_delete = False

    def prepare(self, plan) -> None:
        self.prepared.append(plan)
        self.pending_plans = [plan]

    def delete(self, plan) -> None:
        if self.fail_delete:
            raise OSError("local cleanup failed")
        self.deleted.append(plan)
        self.pending_plans = []

    def pending(self):
        return tuple(self.pending_plans)

    def lookup(self, request_id):
        for plan in self.pending_plans + self.deleted:
            if plan.request_id == request_id:
                return LocalDeletionRecord(plan, plan in self.deleted)
        return None

    def for_session(self, cid, sid):
        for plan in self.pending_plans + self.deleted:
            if any((target.cid, target.sid) == (cid, sid) for target in plan.targets):
                return LocalDeletionRecord(plan, plan in self.deleted)
        return None

    def rejected(self, plan):
        self.pending_plans.remove(plan)


def _bound_session():
    session, resources = _root_session(
        ConversationState(
            cid="cid_test_12345678",
            sid="sid_test_1_abcdef",
            fork_source_available=True,
        )
    )
    resources.shutdown_root.return_value = ()
    return session, resources


@pytest.mark.anyio
async def test_delete_current_confirms_remote_before_local_cleanup() -> None:
    session, resources = _bound_session()
    remote = _Remote()
    store = _Store()
    session._session_deletion_remote = remote
    session._session_deletion_store = store

    result = await session.delete_current("delete_test_123")

    assert result == SessionDeletionResult("deleted", request_id="delete_test_123", remote_deleted=True)
    assert len(remote.requests) == 1
    assert len(store.prepared) == 1
    assert len(store.deleted) == 1
    resources.lifecycle.end.assert_awaited_once()
    assert resources.lifecycle.end.call_args.kwargs["reason"] == "deleted"
    with pytest.raises(ValueError, match="deleted session"):
        await session.begin_turn()


@pytest.mark.anyio
async def test_delete_rejection_does_not_report_success_or_clean_locally() -> None:
    session, _resources = _bound_session()
    remote = _Remote(error=SessionDeletionRemoteError(outcome="rejected", code="session_busy"))
    store = _Store()
    session._session_deletion_remote = remote
    session._session_deletion_store = store

    result = await session.delete_current("delete_test_123")

    assert result.status == "rejected"
    assert result.code == "session_busy"
    assert len(store.prepared) == 1
    assert store.pending_plans == []
    assert store.deleted == []


@pytest.mark.anyio
async def test_unknown_result_is_persisted_and_recovered_without_new_request() -> None:
    session, _resources = _bound_session()
    remote = _Remote(error=SessionDeletionRemoteError(outcome="unknown", code="transport_error"))
    store = _Store()
    session._session_deletion_remote = remote
    session._session_deletion_store = store

    first = await session.delete_current("delete_test_123")
    second = await session.recover_delete("delete_test_123")

    assert first.status == "unknown"
    assert second.status == "deleted"
    assert len(remote.requests) == 1
    assert len(remote.recovery_requests) == 1
    assert remote.recovery_requests[0].request_id == "delete_test_123"
    assert len(store.deleted) == 1


@pytest.mark.anyio
async def test_remote_success_local_failure_is_explicit_and_recoverable() -> None:
    session, _resources = _bound_session()
    remote = _Remote()
    store = _Store()
    store.fail_delete = True
    session._session_deletion_remote = remote
    session._session_deletion_store = store

    result = await session.delete_current("delete_test_123")

    assert result.status == "local_failed"
    assert result.code == "local_cleanup_failed"
    assert len(store.prepared) == 1
    assert store.deleted == []
    with pytest.raises(ValueError, match="deleted session"):
        await session.begin_turn()

    store.fail_delete = False
    recovered = await session.recover_delete("delete_test_123")

    assert recovered.status == "deleted"
    assert len(remote.recovery_requests) == 1
    assert len(store.deleted) == 1


@pytest.mark.anyio
async def test_unbound_session_has_typed_result_without_remote_request() -> None:
    session, _resources = _root_session()
    session._session_deletion_remote = _Remote()
    session._session_deletion_store = _Store()

    result = await session.delete_current("delete_test_123")

    assert result.status == "new_unbound"


@pytest.mark.anyio
async def test_non_current_root_is_not_deleted() -> None:
    session, _resources = _bound_session()

    result = await session.delete_session(
        "cid_test_12345678",
        "sid_test_1_aaaaaa",
        "delete_test_123",
    )

    assert result.status == "not_current"


@pytest.mark.anyio
async def test_intent_precedes_network_and_cancellation_blocks_reuse() -> None:
    session, _ = _bound_session()
    store = _Store()
    entered = asyncio.Event()

    async def transmit(request):
        assert store.pending()[0].request_id == request.request_id
        entered.set()
        await asyncio.Future()

    remote = _Remote()
    remote.delete = transmit
    session._session_deletion_remote = remote
    session._session_deletion_store = store
    task = asyncio.create_task(session.delete_current("delete_cancelled"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(SessionDeletionConflict, match="delete recover delete_cancelled"):
        await session.begin_turn()
    with pytest.raises(SessionDeletionConflict):
        await session.bind(session.cid, session.sid)
    assert len(store.pending()) == 1
    repeated = await session.delete_current("delete_new_identity")
    assert repeated.status == "unknown" and repeated.request_id == "delete_cancelled"
    assert remote.recovery_requests == []
    assert (await session.recover_delete(repeated.request_id)).complete
    assert remote.recovery_requests[0].request_id == "delete_cancelled"


@pytest.mark.anyio
async def test_prepare_failure_never_sends_request_or_claims_remote_success() -> None:
    session, _ = _bound_session()
    store, remote = _Store(), _Remote()
    def fail(_plan):
        raise OSError("disk full")
    store.prepare = fail
    session._session_deletion_store, session._session_deletion_remote = store, remote
    result = await session.delete_current("delete_diskfull")
    assert result.status == "local_failed" and not result.remote_deleted
    assert remote.requests == []
    assert not session.session_retired


@pytest.mark.anyio
async def test_parallel_confirmations_do_not_delete_new_session() -> None:
    session, _ = _bound_session()
    remote, store = _Remote(), _Store()
    session._session_deletion_remote, session._session_deletion_store = remote, store
    identity = session.cid, session.sid
    results = await asyncio.gather(
        session.delete_session(*identity, "delete_parallel_one"),
        session.delete_session(*identity, "delete_parallel_two"),
    )
    assert all(result.complete for result in results)
    assert len(remote.requests) == 1
    await session.reset()
    await session.begin_turn()
    assert not session.session_retired
    result = await session.delete_session(*identity, "delete_stale_menu")
    assert result.status == "not_current"
    assert len(remote.requests) == 1


@pytest.mark.anyio
async def test_recovery_closes_runtime_and_flush_before_cleanup() -> None:
    session, resources = _bound_session()
    remote = _Remote(error=SessionDeletionRemoteError(outcome="unknown", code="transport_error"))
    store = _Store()
    session._session_deletion_remote, session._session_deletion_store = remote, store
    await session.delete_current("delete_flush_failure")
    close = AsyncMock()
    session.bind_session_runtime_close(close)
    resources.event_close.side_effect = OSError("flush failed")
    result = await session.recover_delete("delete_flush_failure")
    assert result.status == "local_failed" and result.remote_deleted
    close.assert_awaited_once_with(session.cid, session.sid)
    assert store.deleted == [] and session.session_retired
    resources.event_close.side_effect = None
    assert (await session.recover_delete("delete_flush_failure")).complete
    resources.lifecycle.end.assert_awaited()
    assert resources.lifecycle.end.call_args.kwargs["reason"] == "deleted"
    requests_before = len(remote.recovery_requests)
    assert (await session.recover_delete("delete_flush_failure")).complete
    assert len(remote.recovery_requests) == requests_before
    lifecycle_calls = resources.lifecycle.end.await_count
    await session.end(reason="exit")
    assert resources.lifecycle.end.await_count == lifecycle_calls


@pytest.mark.anyio
async def test_recovery_preserves_original_root_and_unrelated_current_session() -> None:
    session, resources = _bound_session()
    current = session.cid, session.sid
    child = LocalDeletionTarget("cid_aaaa_12345678", "sid_aaaa_1_abcdef", ())
    root = LocalDeletionTarget("cid_zzzz_12345678", "sid_zzzz_1_abcdef", ())
    plan = LocalDeletionPlan("delete_original_root", (child, root), root)
    remote, store = _Remote(), _Store()
    store.prepare(plan)
    session._session_deletion_remote, session._session_deletion_store = remote, store
    close = AsyncMock()
    session.bind_session_runtime_close(close)
    assert (await session.recover_delete(plan.request_id)).complete
    assert remote.recovery_requests[0].root.cid == root.cid
    assert (session.cid, session.sid) == current
    assert not session.session_retired
    resources.lifecycle.end.assert_not_awaited()
    close.assert_awaited_once_with(root.cid, root.sid)
    await session.begin_turn()


@pytest.mark.anyio
async def test_javascript_close_failure_keeps_intent_for_retry() -> None:
    session, resources = _bound_session()
    store, remote = _Store(), _Remote()
    session._session_deletion_remote, session._session_deletion_store = remote, store
    resources.javascript_cleanup.side_effect = OSError("sidecar is still closing")
    result = await session.delete_current("delete_sidecar_close")
    assert result.status == "local_failed" and result.remote_deleted
    assert store.deleted == [] and store.pending()
    resources.lifecycle.end.assert_not_awaited()
    resources.javascript_cleanup.side_effect = None
    assert (await session.recover_delete(result.request_id)).complete
    assert len(remote.requests) == len(remote.recovery_requests) == 1


@pytest.mark.anyio
async def test_partial_receipt_preserves_full_scope_and_blocks_cleanup() -> None:
    session, _ = _bound_session()
    store, remote = _Store(), _Remote()
    session._session_deletion_remote, session._session_deletion_store = remote, store

    async def partial(request):
        remote.requests.append(request)
        return RemoteDeletionReceipt(request.request_id, request.root, ())

    remote.delete = partial
    result = await session.delete_current("delete_partial_receipt")
    assert result.status == "unknown"
    assert store.deleted == [] and store.pending()
    assert (await session.recover_delete(result.request_id)).complete
    assert len(remote.requests) == len(remote.recovery_requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("finish_before_delete", (True, False))
async def test_real_subagent_completion_and_shutdown_flush_before_deletion(tmp_path, finish_before_delete) -> None:
    parent = _parent_turn()
    target = LocalDeletionTarget(parent.cid, parent.sid, ())
    backend = deletion_store(tmp_path, target)
    controller = _Controller()
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete_during_shutdown(**_kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass
        return RunResult(status="completed")

    controller.stream_handler = complete_during_shutdown
    runtime = SubagentRuntime(controller, graph_store=backend.graphs)
    session, _ = _root_session(ConversationState(cid=parent.cid, sid=parent.sid, fork_source_available=True))
    session._subagent_shutdown = runtime.shutdown_root
    session._session_deletion_store = backend
    remote = _Remote()

    async def confirm_after_flush(request):
        checkpoint = backend.graphs.load(parent.sid)
        assert checkpoint is not None
        assert all(record.status == "closed" for record in checkpoint.records)
        remote.requests.append(request)
        return RemoteDeletionReceipt(request.request_id, request.root, request.targets)

    remote.delete = confirm_after_flush
    session._session_deletion_remote = remote
    try:
        child = await runtime.spawn(parent, "inspect", {}, agent_type="review", task_name="inspect", agent_id="agent_delete_race")
        await asyncio.wait_for(entered.wait(), timeout=2)
        if finish_before_delete:
            release.set()
            await runtime.wait(parent.sid, [child.agent_id], timeout_sec=1)
        result = await session.delete_current("delete_child_completion")
        assert result.complete and len(remote.requests) == 1
        assert {(target.cid, target.sid) for target in remote.requests[0].targets} == {
            (parent.cid, parent.sid), (child.thread.cid, child.thread.sid),
        }
    finally:
        await runtime.shutdown()
    assert backend.graphs.load(parent.sid) is None
    assert backend.pending() == ()


if __name__ == '__main__':
    pass
