# -*- coding: utf-8 -*-

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.harness.sessions.conversation import ConversationState
from agent.ports.session_deletion import (
    RemoteDeletionReceipt,
    SessionDeletionRemoteError,
    SessionDeletionResult,
)
from tests.composition.test_controller_runtime_cleanup import _root_session


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

    assert result == SessionDeletionResult("deleted", request_id="delete_test_123")
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
    assert store.prepared == []
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


if __name__ == '__main__':
    pass
