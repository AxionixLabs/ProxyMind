import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.ports import (
    ProtocolCommandError,
    RunPersistence,
    RunPersistenceConflict,
    RunRecoveryRequired,
    RunSnapshot,
)
from agent.composition import open_turn_application
from agent.domain import RecoveryAction
from agent.harness.sessions.loop import SessionLoop
from agent.protocol import (
    ModelStreamRequest,
    RunEvent,
    SubmitTurnCommand,
    TurnCompletedSnapshot,
    TurnStatusSnapshot,
)
from agent.stores import SQLiteRunStore
from infrastructure.config.runtime_paths import (
    agent_graph_db_path,
    agent_runtime_db_path,
    effect_journal_db_path,
    conversation_history_db_path,
)
from agent.application.config.session_identity import derive_local_session_id


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
    environment_snapshot: dict[str, object] | None = None,
    trace_context: dict[str, object] | None = None,
) -> SubmitTurnCommand:
    return SubmitTurnCommand.create(
        session_id=session_id,
        run_id=run_id,
        command_id=command_id,
        idempotency_key=f"intent-{run_id}",
        message="inspect",
        environment_snapshot=environment_snapshot,
        trace_context=trace_context,
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


def _model_request(
    *,
    turn_id: str = "turn_remote",
    message: str = "inspect",
) -> ModelStreamRequest:
    """创建可在进程重启后重新观察的完整冻结请求。"""
    return ModelStreamRequest(
        cid="cid_test",
        sid="sid_test",
        turn_id=turn_id,
        pref_config={"primary": {"model": "gpt-test"}},
        message=message,
        tools=({"name": "read_file", "type": "function"},),
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
        },
    )


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
    assert schema_version == 2


@pytest.mark.anyio
async def test_run_store_persists_latest_frozen_remote_request(
    tmp_path: Path,
) -> None:
    """确保恢复快照幂等保存首个请求并推进到最新 continuation。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    first = await store.save_remote_request(command, _model_request())
    duplicate = await store.save_remote_request(command, _model_request())
    continuation = await store.save_remote_request(
        command,
        _model_request(turn_id="turn_continuation"),
    )
    loaded = await store.load_remote_request(command.run_id)

    assert first.revision == 1
    assert duplicate.revision == 1
    assert continuation.revision == 2
    assert loaded == continuation
    assert continuation.request.turn_id == "turn_continuation"


@pytest.mark.anyio
async def test_run_store_upgrades_v1_database_with_remote_request_table(
    tmp_path: Path,
) -> None:
    """确保已有用户的 runtime.db 可原位增加冻结请求恢复事实。"""
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE run_remote_requests")
        connection.execute("PRAGMA user_version=1")

    snapshot = await store.save_remote_request(command, _model_request())

    assert snapshot.revision == 1
    with sqlite3.connect(db_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='run_remote_requests'"
        ).fetchone()
    assert version == 2
    assert table == ("run_remote_requests",)


@pytest.mark.anyio
async def test_run_store_rejects_conflicting_frozen_remote_request(
    tmp_path: Path,
) -> None:
    """确保同一远端 Turn 不能被另一份请求语义覆盖。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    await store.save_remote_request(command, _model_request())

    with pytest.raises(
        RunPersistenceConflict,
        match="conflicts with persisted snapshot",
    ):
        await store.save_remote_request(
            command,
            _model_request(message="changed after freeze"),
        )


@pytest.mark.anyio
async def test_queued_run_is_safely_redispatched_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    original_environment = {
        "snapshot_id": "envsnap_before_restart",
        "workspace": {"root": str(tmp_path)},
    }
    original = _command(environment_snapshot=original_environment)
    await store.append_event(
        original,
        _event(original, 1, "run_queued", "queued"),
    )

    original_environment["workspace"]["root"] = "changed-after-queue"
    retry = _command(
        command_id="command-retry",
        environment_snapshot={
            "snapshot_id": "envsnap_before_restart",
            "workspace": {"root": str(tmp_path)},
        },
    )
    application = open_turn_application(db_path)
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def execute(request: SubmitTurnCommand) -> _Result:
        calls.append((
            request.command_id,
            request.environment_snapshot_value(),
        ))
        return _Result()

    result = await application.submit(retry, execute)
    await application.close()

    assert calls == [(
        original.command_id,
        {
            "snapshot_id": "envsnap_before_restart",
            "workspace": {"root": str(tmp_path)},
        },
    )]
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
async def test_closing_remote_run_preserves_recovery_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    application = open_turn_application(db_path)
    started = asyncio.Event()
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_test",
        },
    })

    async def execute(_request: SubmitTurnCommand) -> _Result:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    submission = asyncio.create_task(application.submit(command, execute))
    await started.wait()
    await application.close(cancel_running=True)

    with pytest.raises(asyncio.CancelledError):
        await submission

    reopened = open_turn_application(db_path)
    recoveries = await reopened.recover_session(command.session_id)
    events = await reopened.events(command.run_id)
    await reopened.close()

    assert [event.kind for event in events] == [
        "run_queued",
        "run_started",
        "run_reconciliation_required",
    ]
    assert len(recoveries) == 1
    assert recoveries[0].recovery_action is RecoveryAction.RECONCILE


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


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_queued_run_waits_when_previous_run_requires_reconciliation(
    tmp_path: Path,
) -> None:
    application = open_turn_application(tmp_path / "runtime.db")
    first = _command(run_id="run-first", command_id="command-first")
    second = _command(run_id="run-second", command_id="command-second")
    third = _command(run_id="run-third", command_id="command-third")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    queued_calls: list[str] = []

    async def execute_first(_request: SubmitTurnCommand) -> _Result:
        first_started.set()
        await release_first.wait()
        raise TimeoutError("turn outcome is unknown")

    async def execute_queued(request: SubmitTurnCommand) -> _Result:
        queued_calls.append(request.run_id)
        return _Result()

    first_task = asyncio.create_task(application.submit(first, execute_first))
    await first_started.wait()
    second_task = asyncio.create_task(application.submit(second, execute_queued))
    third_task = asyncio.create_task(application.submit(third, execute_queued))
    try:
        async with asyncio.timeout(1.0):
            while not (
                await application.events(second.run_id)
                and await application.events(third.run_id)
            ):
                await asyncio.sleep(0)

        release_first.set()
        with pytest.raises(TimeoutError, match="outcome is unknown"):
            await first_task
        with pytest.raises(RunRecoveryRequired):
            await second_task
        with pytest.raises(RunRecoveryRequired):
            await third_task

        assert queued_calls == []

        await application.resolve_recovery(
            first.run_id,
            request_id="resolve-first",
            resolution="failed",
            error="remote turn failed",
        )
        recoveries = await application.recover_session(first.session_id)
        assert [item.command.run_id for item in recoveries] == [
            second.run_id,
            third.run_id,
        ]

        with pytest.raises(RunRecoveryRequired):
            await application.submit(third, execute_queued)
        assert queued_calls == []

        second_result = await application.submit(second, execute_queued)
        third_result = await application.submit(third, execute_queued)
        assert second_result.projection.status == "completed"
        assert third_result.projection.status == "completed"
        assert queued_calls == [second.run_id, third.run_id]
    finally:
        release_first.set()
        await application.close(cancel_running=True)


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_recovery_read_failure_keeps_persisted_queue_closed(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    persistence = AsyncMock(spec=RunPersistence)
    persistence.append_event.side_effect = store.append_event
    persistence.find_run.side_effect = store.find_run
    persistence.load_events.side_effect = store.load_events
    persistence.load_facts.side_effect = store.load_facts
    persistence.resolve_recovery.side_effect = store.resolve_recovery
    recovery_failures: list[bool] = [False]

    async def recover_session(session_id: str) -> tuple[RunSnapshot, ...]:
        should_fail = bool(recovery_failures and recovery_failures.pop(0))
        if should_fail:
            raise RuntimeError("recovery read failed")
        return await store.recover_session(session_id)

    persistence.recover_session.side_effect = recover_session
    first = _command(run_id="run-first", command_id="command-first")
    second = _command(run_id="run-second", command_id="command-second")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_calls: list[str] = []

    async def execute_first(_request: SubmitTurnCommand) -> _Result:
        first_started.set()
        await release_first.wait()
        return _Result()

    async def execute_second(request: SubmitTurnCommand) -> _Result:
        second_calls.append(request.run_id)
        return _Result()

    session = SessionLoop[_Result](
        first.session_id,
        persistence=persistence,
    )
    first_task = asyncio.create_task(session.execute(first, execute_first))
    await first_started.wait()
    second_task = asyncio.create_task(session.execute(second, execute_second))
    try:
        async with asyncio.timeout(1.0):
            while not await store.load_events(second.run_id):
                await asyncio.sleep(0)

        recovery_failures.append(True)
        release_first.set()
        with pytest.raises(RuntimeError, match="recovery read failed"):
            await first_task
        with pytest.raises(
            RunPersistenceConflict,
            match="recovery snapshot is unavailable",
        ):
            await second_task
        assert second_calls == []

        recoveries = await session.refresh_recoveries()
        assert [item.command.run_id for item in recoveries] == [second.run_id]

        result = await session.execute(second, execute_second)
        assert result.value.status == "completed"
        assert second_calls == [second.run_id]
    finally:
        release_first.set()
        await session.close(cancel_running=True)


@pytest.mark.anyio
async def test_remote_terminal_clears_persisted_and_cached_recovery_gate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    application = open_turn_application(db_path)
    blocked = _command(run_id="run-blocked", command_id="command-blocked")

    async def execute(_request: SubmitTurnCommand) -> _Result:
        return _Result()

    with pytest.raises(RunRecoveryRequired):
        await application.submit(blocked, execute)

    protocol_client = AsyncMock()
    protocol_client.get_turn_status.return_value = TurnStatusSnapshot(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_remote",
        run_id="run_remote",
        status="interrupted",
        terminal=TurnCompletedSnapshot(
            turn_id="turn_remote",
            status="interrupted",
            error=None,
            last_event_seq=8,
            completed_at=2.0,
        ),
        attempt=1,
        version=3,
        last_event_seq=8,
        created_at=1.0,
        updated_at=2.0,
    )

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )
    completed = await application.submit(blocked, execute)
    await application.close()

    assert recovery.pending == ()
    assert recovery.restore_commands == ()
    assert recovery.resolved_run_ids == (command.run_id,)
    assert completed.projection.status == "completed"
    protocol_client.get_turn_status.assert_awaited_once_with(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_remote",
    )


@pytest.mark.anyio
async def test_frozen_remote_turn_requires_attach_before_recovery_gate_opens(
    tmp_path: Path,
) -> None:
    """确保终态 status 不能绕过原 Turn replay 直接解除本地门禁。"""
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    await store.save_remote_request(command, _model_request())
    application = open_turn_application(db_path)
    protocol_client = AsyncMock()
    protocol_client.get_turn_status.return_value = TurnStatusSnapshot(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_remote",
        run_id="run_remote",
        status="completed",
        terminal=TurnCompletedSnapshot(
            turn_id="turn_remote",
            status="completed",
            error=None,
            last_event_seq=12,
            completed_at=2.0,
        ),
        attempt=1,
        version=3,
        last_event_seq=12,
        created_at=1.0,
        updated_at=2.0,
    )

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )

    assert len(recovery.pending) == 1
    assert recovery.resolved_run_ids == ()
    assert len(recovery.observe_turns) == 1
    observed = recovery.observe_turns[0]
    assert observed.snapshot.command == command
    assert observed.request == _model_request()
    assert observed.replay_target_seq == 12

    await application.resolve_observed_recovery(observed, _Result())
    assert await application.recover_session(command.session_id) == ()
    await application.close()


@pytest.mark.anyio
async def test_missing_remote_turn_restores_input_and_clears_recovery_gate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_missing",
        },
    })
    await _append_started(store, command)
    await store.save_remote_request(
        command,
        _model_request(turn_id="turn_missing"),
    )
    application = open_turn_application(db_path)
    protocol_client = AsyncMock()
    protocol_client.get_turn_status.side_effect = ProtocolCommandError(
        "turn_status_request_failed",
        "turn status request failed",
        details={"status_code": 404},
    )

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )
    remaining = await application.recover_session(command.session_id)
    await application.close()

    assert recovery.pending == ()
    assert recovery.restore_commands == (command,)
    assert recovery.resolved_run_ids == (command.run_id,)
    assert remaining == ()


@pytest.mark.anyio
async def test_missing_continuation_fails_without_redispatching_original_input(
    tmp_path: Path,
) -> None:
    """确保 Stop continuation 的 404 不会重复提交已经执行过的首轮输入。"""
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command(trace_context={
        "remote_turn": {
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_remote",
        },
    })
    await _append_started(store, command)
    await store.save_remote_request(command, _model_request())
    await store.save_remote_request(
        command,
        _model_request(turn_id="turn_missing_continuation"),
    )
    application = open_turn_application(db_path)
    protocol_client = AsyncMock()
    protocol_client.get_turn_status.side_effect = ProtocolCommandError(
        "turn_status_request_failed",
        "turn status request failed",
        details={"status_code": 404},
    )

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )

    assert recovery.pending == ()
    assert recovery.restore_commands == ()
    assert recovery.resolved_run_ids == (command.run_id,)
    await application.close()


@pytest.mark.anyio
async def test_queued_recovery_is_returned_for_editor_restore(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    command = _command()
    await store.append_event(
        command,
        _event(command, 1, "run_queued", "queued"),
    )
    application = open_turn_application(db_path)
    protocol_client = AsyncMock()

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )
    await application.close()

    assert recovery.pending == ()
    assert recovery.restore_commands == (command,)
    assert recovery.resolved_run_ids == (command.run_id,)
    protocol_client.get_turn_status.assert_not_awaited()


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
        conversation_history_db_path().name,
    } == {"runtime.db", "agents.db", "effects.db", "history.db"}
