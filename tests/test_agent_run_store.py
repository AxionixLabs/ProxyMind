import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.application import (
    RunPersistenceConflict,
    RunRecoveryRequired,
    open_turn_application,
)
from agent.domain import RecoveryAction
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand,
)
from agent.stores import SQLiteRunStore
from mind_app.paths import (
    agent_graph_db_path,
    agent_runtime_db_path,
    effect_journal_db_path,
    mind_history_db_path,
)
from mind_app.runtime.support.session_identity import derive_local_session_id


@dataclass(frozen=True, slots=True)
class _Result:
    status: str = "completed"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "assistant_text": "final answer",
            "tool_results": [{"call_id": "call-1", "ok": True}],
            "approval_decisions": [{"approval_id": "approval-1", "decision": "accept"}],
            "evidence_references": [{"path": "report.json", "sha256": "a" * 64}],
            "usage": {},
            "error": None,
            "exit_code": 0,
        }


def _command(
    *,
    session_id: str = "session-durable",
    run_id: str = "run-durable",
    command_id: str = "command-durable",
) -> SubmitTurnCommand:
    return SubmitTurnCommand.create(
        session_id=session_id,
        run_id=run_id,
        command_id=command_id,
        idempotency_key=f"intent-{run_id}",
        message="inspect",
    )


def _event(
    command: SubmitTurnCommand,
    sequence: int,
    kind: str,
    status: str,
    *,
    payload: dict[str, object] | None = None,
) -> RunEvent:
    return RunEvent.create(
        sequence=sequence,
        session_id=command.session_id,
        run_id=command.run_id,
        kind=kind,
        payload=payload or {"status": status},
        causation_id=command.command_id,
    )


async def _append_started(
    store: SQLiteRunStore,
    command: SubmitTurnCommand,
) -> None:
    await store.append_event(command, _event(command, 1, "run_queued", "queued"))
    await store.append_event(command, _event(command, 2, "run_started", "running"))


@pytest.mark.anyio
async def test_run_store_commits_event_snapshot_outbox_and_final_facts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    application = open_turn_application(db_path)
    command = _command()

    async def execute(_request: SubmitTurnCommand) -> _Result:
        return _Result()

    result = await application.submit(command, execute)
    facts = await application.facts(command.run_id)
    events = await application.events(command.run_id)
    await application.close()

    assert result.projection.status == "completed"
    assert [event.sequence for event in events] == [1, 2, 3]
    assert {fact.kind for fact in facts} == {
        "approval_decision",
        "assistant_message",
        "evidence_reference",
        "final_result",
        "tool_result",
    }
    assert next(
        fact for fact in facts if fact.kind == "assistant_message"
    ).payload == {"text": "final answer"}

    with sqlite3.connect(db_path) as connection:
        snapshot = connection.execute(
            "SELECT status, sequence, snapshot_version, effect_status FROM run_snapshots"
        ).fetchone()
        outbox = connection.execute(
            "SELECT status, attempt_count, fingerprint, replay FROM run_outbox"
        ).fetchone()
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert snapshot == ("completed", 3, 1, "committed")
    assert outbox is not None
    assert outbox[0:2] == ("committed", 1)
    assert len(outbox[2]) == 64
    assert outbox[3] == "manual"
    assert schema_version == 1


@pytest.mark.anyio
async def test_queued_run_is_safely_redispatched_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    original = _command()
    await store.append_event(
        original,
        _event(original, 1, "run_queued", "queued"),
    )

    retry = _command(command_id="command-retry")
    application = open_turn_application(db_path)
    calls: list[str] = []

    async def execute(request: SubmitTurnCommand) -> _Result:
        calls.append(request.command_id)
        return _Result()

    result = await application.submit(retry, execute)
    await application.close()

    assert calls == [original.command_id]
    assert result.events[0].kind == "run_queued"
    assert [event.sequence for event in result.events] == [1, 2, 3]


@pytest.mark.anyio
async def test_running_run_requires_reconciliation_and_is_not_redispatched(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command()
    await _append_started(store, command)
    application = open_turn_application(db_path)
    calls: list[str] = []

    async def execute(_request: SubmitTurnCommand) -> _Result:
        calls.append("called")
        return _Result()

    recoveries = await application.recover_session(command.session_id)
    with pytest.raises(RunPersistenceConflict, match="cannot be redispatched"):
        await application.submit(command, execute)

    another = _command(run_id="run-another", command_id="command-another")
    with pytest.raises(RunRecoveryRequired) as caught:
        await application.submit(another, execute)
    await application.close(cancel_running=True)

    assert calls == []
    assert recoveries[0].status.value == "running"
    assert recoveries[0].recovery_action is RecoveryAction.RECONCILE
    assert caught.value.snapshots == recoveries


@pytest.mark.anyio
async def test_timeout_enters_reconciliation_instead_of_failed(tmp_path: Path) -> None:
    application = open_turn_application(tmp_path / "runtime.db")
    command = _command()

    async def execute(_request: SubmitTurnCommand) -> _Result:
        raise TimeoutError("delivery acknowledgement was not received")

    with pytest.raises(TimeoutError):
        await application.submit(command, execute)

    another = _command(run_id="run-after-timeout", command_id="command-after-timeout")
    with pytest.raises(RunRecoveryRequired):
        await application.submit(another, execute)

    events = await application.events(command.run_id)
    recoveries = await application.recover_session(command.session_id)
    await application.close()

    assert events[-1].kind == "run_reconciliation_required"
    assert recoveries[0].recovery_action is RecoveryAction.RECONCILE
    assert recoveries[0].effect_status == "reconciliation_required"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "status", "action"),
    (
        ("run_waiting_approval", "waiting_approval", RecoveryAction.WAIT_APPROVAL),
        ("run_waiting_effect", "waiting_effect", RecoveryAction.RECONCILE),
        ("run_paused", "paused", RecoveryAction.RESUME),
    ),
)
async def test_waiting_and_paused_states_survive_restart(
    tmp_path: Path,
    kind: str,
    status: str,
    action: RecoveryAction,
) -> None:
    store = SQLiteRunStore(tmp_path / f"{status}.db")
    command = _command(session_id=f"session-{status}", run_id=f"run-{status}")
    await _append_started(store, command)
    await store.append_event(command, _event(command, 3, kind, status))

    recovered = await store.recover_session(command.session_id)

    assert len(recovered) == 1
    assert recovered[0].status.value == status
    assert recovered[0].recovery_action is action
    assert recovered[0].sequence == 3


@pytest.mark.anyio
async def test_discontinuous_event_rolls_back_snapshot_and_outbox(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command()
    await store.append_event(command, _event(command, 1, "run_queued", "queued"))

    with pytest.raises(RunPersistenceConflict, match="not continuous"):
        await store.append_event(command, _event(command, 3, "run_started", "running"))

    recovered = await store.recover_session(command.session_id)
    events = await store.load_events(command.run_id)
    assert recovered[0].sequence == 1
    assert recovered[0].effect_status == "pending"
    assert [event.sequence for event in events] == [1]


@pytest.mark.anyio
async def test_run_identity_collision_is_reported_as_persistence_conflict(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    first = _command()
    conflicting = _command(run_id="run-conflicting")
    await store.append_event(first, _event(first, 1, "run_queued", "queued"))

    with pytest.raises(RunPersistenceConflict, match="identity or event conflicts"):
        await store.append_event(
            conflicting,
            _event(conflicting, 1, "run_queued", "queued"),
        )

    assert await store.load_events(conflicting.run_id) == ()


@pytest.mark.anyio
async def test_recovery_rejects_unknown_snapshot_version(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command()
    await store.append_event(
        command,
        _event(command, 1, "run_queued", "queued"),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE run_snapshots SET snapshot_version = 2 WHERE run_id = ?",
            (command.run_id,),
        )

    with pytest.raises(RuntimeError, match="snapshot version is unsupported"):
        await store.recover_session(command.session_id)


def test_runtime_identity_and_storage_are_isolated_from_online_and_graph_state() -> None:
    coordinates = {"cid": "cid-secret", "sid": "sid-secret"}

    first = derive_local_session_id("tui", coordinates)
    second = derive_local_session_id("tui", dict(coordinates))

    assert first == second
    assert first.startswith("tui_session_")
    assert coordinates["cid"] not in first
    assert coordinates["sid"] not in first
    assert {
        agent_runtime_db_path().name,
        agent_graph_db_path().name,
        effect_journal_db_path().name,
        mind_history_db_path().name,
    } == {"runtime.db", "agents.db", "effects.db", "history.db"}
