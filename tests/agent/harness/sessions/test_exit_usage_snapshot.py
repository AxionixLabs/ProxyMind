import asyncio
import json
import subprocess
import sys
from dataclasses import (
    FrozenInstanceError,
    replace,
)
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionTarget,
    SessionDeletionRemoteError,
)
from agent.protocol.context_usage import (
    ContextUsageRecord,
    SessionTokenUsageRecord,
)
from tests.agent.harness.sessions.test_session_deletion_lifecycle import (
    _Remote,
    _Store,
)
from tests.composition.test_controller_runtime_cleanup import _root_session


@pytest.fixture
def record():
    return ContextUsageRecord(
        "cid_test_12345678", "sid_test_1_abcdef", "turn_one", 12, 1, 100_000, 20_000,
        SessionTokenUsageRecord(17_677, 17_663, 14_976, 0, 14, 4, 2, 0),
        "provider", "test-model", "responses",
    )


async def resumed(record):
    session, resources = _root_session()
    resources.shutdown_root.return_value = ()
    resources.context_recovery.load.return_value = record
    await session.bind(record.cid, record.sid)
    return session, resources


@pytest.mark.anyio
async def test_cold_resume_exit_freezes_full_fact_without_a_local_turn(record):
    session, resources = await resumed(record)
    assert session.turn_count == 0
    await session.end(reason="exit")
    assert session.context_usage.view.record is None
    snapshot = session.take_exit_snapshot()
    assert snapshot.record == record
    assert snapshot.disposition == "recoverable"
    assert not snapshot.remote_stop_confirmed
    with pytest.raises(FrozenInstanceError):
        snapshot.record.total_token_usage.input_tokens = 0
    assert session.take_exit_snapshot() is None
    await session.end(reason="exit")
    assert session.take_exit_snapshot() is None
    resources.context_recovery.load.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_only_remote_terminal_can_confirm_stop(record, interrupted):
    session, _ = await resumed(record)
    session.observe_remote_turn(record.cid, record.sid, "one", terminal=False)
    session.observe_remote_turn(record.cid, record.sid, "two", terminal=False)
    session.observe_remote_turn(record.cid, record.sid, "one", terminal=True)
    session.observe_remote_turn(record.cid, "other", "two", terminal=True)
    if not interrupted:
        session.observe_remote_turn(record.cid, record.sid, "two", terminal=True)
    await session.end(reason="exit")
    assert session.take_exit_snapshot().remote_stop_confirmed is (not interrupted)


@pytest.mark.anyio
async def test_unbound_and_never_confirmed_new_session_have_no_resume():
    session, resources = _root_session()
    resources.shutdown_root.return_value = ()
    await session.end(reason="exit")
    assert session.take_exit_snapshot() is None
    await session.reset()
    await session.begin_turn()
    await session.end(reason="exit")
    assert session.take_exit_snapshot() is None


@pytest.mark.anyio
async def test_archive_keeps_usage_and_does_not_become_normal_resume(record):
    session, _ = await resumed(record)
    await session.archive_current()
    assert session.context_usage.view.record is None
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.disposition == "archived"
    assert snapshot.record == record


@pytest.mark.anyio
async def test_failed_archive_rolls_back_and_cannot_publish_archived_snapshot(record):
    session, resources = await resumed(record)
    resources.lifecycle.end.side_effect = OSError("hook failure")
    with pytest.raises(OSError):
        await session.archive_current()
    assert session.context_usage.view.record == record
    assert session.take_exit_snapshot() is None
    resources.store.unarchive_session.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["deleted", "unknown", "rejected", "local_failed"])
async def test_delete_disposition_tracks_receipt_and_local_completion(record, outcome):
    session, _ = await resumed(record)
    store = _Store()
    remote = _Remote(error=(
        SessionDeletionRemoteError(outcome=outcome, code="test_failure")
        if outcome in {"unknown", "rejected"} else None
    ))
    store.fail_delete = outcome == "local_failed"
    session._session_deletion_store, session._session_deletion_remote = store, remote
    assert (await session.delete_current("delete_exit_usage")).status == outcome
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.record == record
    assert snapshot.disposition == {
        "deleted": "deleted", "unknown": "pending_delete",
        "rejected": "recoverable", "local_failed": "pending_delete",
    }[outcome]
    assert snapshot.deletion_request_id == (
        "delete_exit_usage" if outcome in {"unknown", "local_failed"} else None
    )
    assert snapshot.remote_stop_confirmed is (outcome in {"deleted", "local_failed"})


@pytest.mark.anyio
async def test_cancelled_delete_keeps_intent_for_exit(record):
    session, _ = await resumed(record)
    store = _Store()
    remote = _Remote()
    entered = asyncio.Event()

    async def pending(_request):
        entered.set()
        await asyncio.Future()

    remote.delete = pending
    session._session_deletion_store, session._session_deletion_remote = store, remote
    deletion = asyncio.create_task(session.delete_current("delete_cancelled_exit"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    deletion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deletion
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.disposition == "pending_delete"
    assert snapshot.record == record


@pytest.mark.anyio
async def test_pending_delete_without_confirmed_remote_usage_still_has_recovery_identity():
    session, resources = _root_session()
    resources.shutdown_root.return_value = ()
    await session.begin_turn()
    session._session_deletion_store = _Store()
    session._session_deletion_remote = _Remote(error=SessionDeletionRemoteError(outcome="unknown", code="timeout"))
    await session.delete_current("delete_before_first_fact")
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.disposition == "pending_delete"
    assert snapshot.deletion_request_id == "delete_before_first_fact"
    assert snapshot.record is None


@pytest.mark.anyio
async def test_recovery_keeps_candidate_after_local_failure_and_flushes_latest_fact(record):
    session, _ = await resumed(record)
    store = _Store()
    store.fail_delete = True
    session._session_deletion_store, session._session_deletion_remote = store, _Remote()
    latest = replace(record, event_seq=20, total_token_usage=None)
    session.bind_session_runtime_close(AsyncMock(side_effect=lambda *_: session.record_context_usage(latest)))
    assert (await session.delete_current("delete_retry_exit")).status == "local_failed"
    assert session.context_usage.view.record is None
    store.fail_delete = False
    assert (await session.recover_delete("delete_retry_exit")).complete
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.disposition == "deleted"
    assert snapshot.record == latest


@pytest.mark.anyio
async def test_exit_keeps_fact_received_before_failed_runtime_cleanup(record):
    session, _ = await resumed(record)
    session._session_deletion_store, session._session_deletion_remote = _Store(), _Remote()
    latest = replace(record, event_seq=21, total_token_usage=None)

    async def fail_after_delivery(*_args):
        session.record_context_usage(latest)
        raise OSError("runtime close failed")

    session.bind_session_runtime_close(fail_after_delivery)
    assert (await session.delete_current("delete_runtime_failure")).status == "local_failed"
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot.disposition == "pending_delete"
    assert snapshot.record == latest


@pytest.mark.anyio
async def test_other_session_recovery_does_not_freeze_current_usage(record):
    session, _ = await resumed(record)
    other = LocalDeletionTarget("cid_other_12345678", "sid_other_1_abcdef", ())
    plan = LocalDeletionPlan("delete_other_exit", (other,), other)
    store = _Store()
    store.prepare(plan)
    session._session_deletion_store, session._session_deletion_remote = store, _Remote()
    assert (await session.recover_delete(plan.request_id)).complete
    assert session._exit_snapshot is None
    assert session.context_usage.view.record == record
    await session.end(reason="exit")
    assert session.take_exit_snapshot().disposition == "recoverable"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["reset", "bind", "begin_turn"])
async def test_switch_discards_prior_exit_candidate_and_cumulative(record, operation):
    session, resources = await resumed(record)
    await session.archive_current()
    resources.context_recovery.load.return_value = None
    if operation == "reset":
        await session.reset()
    else:
        await getattr(session, operation)("cid_other_12345678", "sid_other_1_abcdef")
    assert session._exit_snapshot is None
    assert session.context_usage.view.record is None
    await session.end(reason="exit")
    snapshot = session.take_exit_snapshot()
    assert snapshot is None or (snapshot.sid != record.sid and snapshot.record is None)


@pytest.mark.anyio
async def test_pending_replay_and_error_cleanup_do_not_publish_cached_usage(record):
    session, _ = await resumed(record)
    session.context_usage_recovery(record.cid, record.sid, pending=True)
    await session.end(reason="exit")
    assert session.take_exit_snapshot().record is None
    session, _ = await resumed(record)
    await session.archive_current()
    await session.end(reason="error")
    assert session.take_exit_snapshot() is None


def test_independent_client_processes_restore_and_delete_real_usage_storage(
    tmp_path: Path, repository_root: Path, fixtures_root: Path,
):
    def run(action):
        result = subprocess.run(
            [sys.executable, "-m", "tests.agent.harness.sessions.exit_usage_process", str(tmp_path), action,
             "--fixtures", str(fixtures_root)],
            cwd=repository_root,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert run("seed")["seeded"]
    resumed = run("resume")
    assert resumed["turn_count"] == 0
    assert resumed["snapshot"]["disposition"] == "recoverable"
    assert "mind resume " in resumed["rendered"]
    assert resumed["history_exists"] and resumed["cache_exists"] and resumed["transcript_exists"]
    deleted = run("delete")
    assert deleted["snapshot"]["record"] == resumed["snapshot"]["record"]
    assert deleted["snapshot"]["record"]["total_token_usage"]["cached_input_tokens"] == 14_976
    assert deleted["snapshot"]["disposition"] == "deleted"
    assert "resume" not in deleted["rendered"]
    assert not deleted["history_exists"] and not deleted["cache_exists"] and not deleted["transcript_exists"]
    assert run("verify_deleted") == {"deleted": True, "pending": 0}
