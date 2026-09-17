import asyncio
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionTarget,
    SessionDeletionConflict,
)
from agent.ports.persistence import RunPersistenceConflict
from agent.protocol import RunEvent
from agent.stores.agents.graph import AgentGraphCheckpoint
from agent.stores.effects.journal import LocalEffectJournal
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from tests.agent.stores.sessions.deletion_fixture import (
    approval,
    command,
    effect,
    rows,
    seeded,
    store,
    target,
)


def test_deletes_complete_target_set_and_preserves_control(tmp_path):
    plan, control = seeded(tmp_path)
    before = rows(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    backend.delete(plan)
    assert rows(tmp_path) == {table: count // 3 for table, count in before.items()}
    assert backend.pending() == ()
    backend.delete(plan)
    # A second request and already missing files remain a safe no-op for this exact scope.
    backend.delete(replace(plan, request_id="delete_repeat_acceptance"))
    assert rows(tmp_path) == {table: count // 3 for table, count in before.items()}
    assert backend.history.find_session(control.sid) is not None
    transcripts = ConversationTranscriptStore(tmp_path / "sessions")
    assert transcripts.reader(transcripts.existing_path_for_session(control.sid)).read()
    for owner in plan.targets:
        assert backend.history.find_session(owner.sid) is None
        assert backend.graphs.load(owner.sid) is None
        assert transcripts.existing_path_for_session(owner.sid) == ""
        assert transcripts.path_for_session(owner.sid) == ""


def test_late_store_and_transcript_writes_are_rejected(tmp_path):
    plan, _ = seeded(tmp_path)
    owner = plan.targets[0]
    transcripts = ConversationTranscriptStore(tmp_path / "sessions")
    path = transcripts.existing_path_for_session(owner.sid)
    backend = store(tmp_path, owner)
    backend.delete(plan)
    before = rows(tmp_path)
    for write in (
        lambda: backend.history.touch_session(cid=owner.cid, sid=owner.sid),
        lambda: backend.history.get_or_create_fork_request(cid=owner.cid, sid=owner.sid, request_id="fork_late"),
        lambda: backend.graphs.save(AgentGraphCheckpoint(owner.sid, 2, time.time_ns() // 1_000_000)),
        lambda: asyncio.run(backend.approvals.record_requested(approval(owner))),
        lambda: asyncio.run(backend.effects.begin(effect(owner, suffix="_late"))),
        lambda: asyncio.run(backend.effects.save_tool_result(owner.cid, owner.sid, "late", {}, {})),
    ):
        with pytest.raises((sqlite3.IntegrityError, SessionDeletionConflict)):
            write()
    request = command(owner, suffix="_late")
    event = RunEvent.create(sequence=1, session_id=request.session_id, run_id=request.run_id,
                            kind="run_queued", payload={"status": "queued"}, causation_id=request.command_id)
    with pytest.raises(RunPersistenceConflict):
        asyncio.run(backend.runs.append_event(request, event))
    writer = transcripts.writer(path, session_id=owner.sid)
    writer.open()
    writer.append("late", payload={"text": "must not reappear"})
    writer.close()
    assert not Path(path).exists()
    assert rows(tmp_path) == before


def test_open_writers_hold_shared_locks_and_block_deletion(tmp_path):
    plan, _ = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    transcripts = ConversationTranscriptStore(tmp_path / "sessions")
    path = transcripts.existing_path_for_session(plan.targets[0].sid)
    first = transcripts.writer(path, session_id=plan.targets[0].sid)
    second = transcripts.writer(path, session_id=plan.targets[0].sid)
    before = rows(tmp_path)
    try:
        first.open()
        second.open()
        second.append("shared-lock")
        assert transcripts.reader(path).read()[-1].event == "shared-lock"
        with pytest.raises(OSError):
            backend.delete(plan)
        first.close()
        with pytest.raises(OSError):
            backend.delete(plan)
        assert rows(tmp_path) == before
    finally:
        first.close()
        second.close()
    backend.delete(backend.pending()[0])
    assert not Path(path).exists()


def test_file_failure_keeps_original_scope_after_partial_cleanup(tmp_path):
    plan, _ = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    before = rows(tmp_path)
    with patch.object(Path, "unlink", side_effect=PermissionError("occupied transcript")):
        with pytest.raises(PermissionError):
            backend.delete(plan)
    assert len(backend.pending()) == 1
    partial = rows(tmp_path)
    assert partial["local_effects"] == 1
    assert partial["conversation_session_cursors"] == 3
    reopened = store(tmp_path, plan.targets[0])
    reopened.delete(reopened.pending()[0])
    assert reopened.pending() == ()
    assert rows(tmp_path) == {table: count // 3 for table, count in before.items()}


def test_sqlite_failure_rolls_back_one_store_and_resumes_other_stores(tmp_path):
    plan, _ = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    with sqlite3.connect(tmp_path / "approvals.db") as connection:
        connection.executescript("CREATE TRIGGER fail_delete BEFORE DELETE ON approval_facts "
                                 "BEGIN SELECT RAISE(ABORT, 'injected database failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match="injected database failure"):
        backend.delete(plan)
    assert rows(tmp_path)["approval_facts"] == 3
    assert rows(tmp_path)["local_effects"] == 1
    assert len(backend.pending()) == 1
    with sqlite3.connect(tmp_path / "approvals.db") as connection:
        connection.execute("DROP TRIGGER fail_delete")
    backend.delete(backend.pending()[0])
    assert rows(tmp_path)["approval_facts"] == 1
    assert backend.pending() == ()


def test_rejects_changed_scope_and_invalid_targets(tmp_path):
    plan, control = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    backend.delete(plan)
    with pytest.raises(SessionDeletionConflict):
        backend.delete(replace(plan, targets=(*plan.targets, control)))
    with pytest.raises(ValueError, match="duplicate"):
        backend.delete(replace(plan, targets=(plan.targets[0], plan.targets[0])))
    with pytest.raises(ValueError, match="coordinates"):
        backend.delete(replace(plan, targets=(LocalDeletionTarget("bad", "../../escape", ()),)))
    assert backend.history.find_session(control.sid) is not None


def test_missing_target_is_idempotent_and_cannot_be_created_later(tmp_path):
    owner = target()
    backend = store(tmp_path, owner)
    plan = LocalDeletionPlan("delete_missing_session", (owner,))
    backend.delete(plan)
    backend.delete(plan)
    assert set(rows(tmp_path).values()) == {0}
    assert ConversationTranscriptStore(tmp_path / "sessions").path_for_session(owner.sid) == ""


def test_path_escape_hard_links_and_sidecar_links_are_rejected(tmp_path):
    owner = target()
    transcripts = ConversationTranscriptStore(tmp_path / "sessions")
    outside = tmp_path / "control.txt"
    outside.write_text("must survive", encoding="utf-8")
    with pytest.raises(ValueError):
        transcripts.lock_sessions(("../control.txt",))
    path = Path(transcripts.path_for_session(owner.sid))
    path.unlink()
    path.hardlink_to(outside)
    with pytest.raises(ValueError, match="unshared"):
        transcripts.lock_sessions((owner.sid,))
    assert outside.read_text(encoding="utf-8") == "must survive"
    path.unlink()
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.unlink()
    lock_path.hardlink_to(outside)
    with pytest.raises(ValueError, match="unshared"):
        with transcripts.lock_sessions((owner.sid,)):
            pytest.fail("unsafe lock path was accepted")
    assert outside.read_text(encoding="utf-8") == "must survive"


def test_legacy_effect_migration_uses_saved_receipt_and_blocks_unowned_rows(tmp_path):
    owner, unknown = target(), target()
    path = tmp_path / "effects.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE local_effects (effect_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
        replay TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT, error TEXT NOT NULL DEFAULT '',
        created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL);
        PRAGMA user_version=2;
        """)
        for identity, payload in (
            (effect(owner).effect_id, json.dumps({"reconciliation_result_payload": {"cid": owner.cid, "sid": owner.sid}})),
            (effect(unknown).effect_id, None),
        ):
            connection.execute("INSERT INTO local_effects VALUES (?, ?, 'manual', 'committed', ?, '', 1, 1)",
                               (identity, "a" * 64, payload))
    journal = LocalEffectJournal(path, cid=owner.cid, sid=owner.sid)
    assert asyncio.run(journal.inspect(effect(owner))).action == "reuse"
    with pytest.raises(SessionDeletionConflict, match="unresolved"):
        journal.delete_sessions((owner,))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM local_effects").fetchone() == (2,)
        assert connection.execute("SELECT cid, sid FROM local_effects WHERE effect_id = ?",
                                  (effect(owner).effect_id,)).fetchone() == (owner.cid, owner.sid)


def test_effect_identity_cannot_cross_session_boundaries(tmp_path):
    first, second = target(), target()
    journal = LocalEffectJournal(tmp_path / "effects.db", cid=first.cid, sid=first.sid)
    asyncio.run(journal.begin(effect(first)))
    other = LocalEffectJournal(tmp_path / "effects.db", cid=second.cid, sid=second.sid)
    for operation in (other.inspect(effect(first)), other.begin(effect(first)), other.commit(effect(first), {})):
        with pytest.raises(SessionDeletionConflict, match="ownership"):
            asyncio.run(operation)


def test_retry_rejects_changed_database_and_transcript_locations(tmp_path):
    plan, _ = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    with patch.object(Path, "unlink", side_effect=PermissionError("occupied")):
        with pytest.raises(PermissionError):
            backend.delete(plan)
    reopened = store(tmp_path, plan.targets[0])
    reopened.runs.db_path = tmp_path / "other-runtime.db"
    with pytest.raises(SessionDeletionConflict, match="paths changed"):
        reopened.pending()
    with pytest.raises(SessionDeletionConflict, match="scope conflicts"):
        reopened.delete(plan)
    reopened = store(tmp_path, plan.targets[0])
    reopened.transcripts = ConversationTranscriptStore(tmp_path / "other-sessions")
    with pytest.raises(SessionDeletionConflict, match="scope conflicts"):
        reopened.delete(plan)
    assert not (tmp_path / "other-runtime.db").exists()
    assert not (tmp_path / "other-sessions").exists()
    backend.delete(backend.pending()[0])
    assert backend.pending() == ()


def test_graph_rejects_omitted_child_without_removing_relationships(tmp_path):
    plan, control = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    with pytest.raises(SessionDeletionConflict, match="undeleted child"):
        backend.graphs.delete_sessions((plan.targets[0],))
    checkpoint = backend.graphs.load(plan.targets[0].sid)
    assert checkpoint is not None
    assert checkpoint.records[0].thread.sid == plan.targets[1].sid
    assert checkpoint.mailbox.events[0].message == "isolated child mailbox"
    assert backend.graphs.load(control.sid) is not None
    backend.delete(plan)
    assert backend.graphs.load(plan.targets[0].sid) is None


def test_run_mapping_cannot_delete_an_unrelated_remote_session(tmp_path):
    plan, control = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    incorrect = replace(plan.targets[0], local_session_ids=(command(control).session_id,))
    before = rows(tmp_path)
    with pytest.raises(SessionDeletionConflict, match="undeleted remote sessions"):
        backend.runs.delete_sessions((incorrect,))
    assert rows(tmp_path) == before
    backend.delete(plan)
    assert asyncio.run(backend.runs.load_events(command(control).run_id))


def test_corrupt_recovery_plan_is_rejected_without_deleting_anything(tmp_path):
    plan, _ = seeded(tmp_path)
    backend = store(tmp_path, plan.targets[0])
    assert backend.pending() == ()
    before = rows(tmp_path)
    with sqlite3.connect(tmp_path / "history.db") as connection:
        connection.execute("INSERT INTO session_deletions (request_id, plan, layout) VALUES (?, ?, ?)",
                           (plan.request_id, '{"request_id": "bad", "targets": [{}]}', '{}'))
    with pytest.raises(ValueError):
        backend.pending()
    assert rows(tmp_path) == before


def test_queued_run_binding_is_deleted_before_remote_request_exists(tmp_path):
    owner = target()
    backend = store(tmp_path, owner)
    request = command(owner, local_session_id="custom-mcp-session")
    event = RunEvent.create(sequence=1, session_id=request.session_id, run_id=request.run_id,
                            kind="run_queued", payload={"status": "queued"}, causation_id=request.command_id)
    asyncio.run(backend.runs.append_event(request, event))
    backend.delete(LocalDeletionPlan("delete_queued_run", (owner,)))
    assert asyncio.run(backend.runs.load_events(request.run_id)) == ()
    late = command(owner, suffix="_late", local_session_id="another-custom-session")
    late_event = RunEvent.create(sequence=1, session_id=late.session_id, run_id=late.run_id,
                                 kind="run_queued", payload={"status": "queued"}, causation_id=late.command_id)
    with pytest.raises(RunPersistenceConflict, match="remote session has been deleted"):
        asyncio.run(backend.runs.append_event(late, late_event))
