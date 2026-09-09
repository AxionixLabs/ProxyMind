# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.adapters.turns.review import ReviewCommandExecutor
from agent.application.turns.commands import TurnApplication
from agent.application.turns.run_result import RunResult
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.ports import (
    ModelCapabilityError,
    ProtocolCommandClient,
    ProtocolCommandError,
    RunPersistenceConflict,
)
from agent.protocol import (
    ModelStreamRequest,
    ReviewStreamRequest,
    RunEvent,
    SubmitReviewCommand,
    TurnStatusSnapshot,
    parse_run_command,
)
from agent.stores import SQLiteRunStore
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewRequest,
    ReviewCustomTarget,
    ReviewExecutionOptions,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_01"
REQUEST_ID = "review_request_01"


def _tools() -> tuple[dict, ...]:
    """构造最小严格只读工具目录。"""
    return ({
        "name": "exec_command",
        "description": "Run a command in the review sandbox.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },)


def _request(
    *,
    patch: str = "diff --git a/a.py b/a.py\n",
) -> ReviewStreamRequest:
    """构造经过 wire 契约校验的本地 Review 请求。"""
    wire_request = MindReviewRequest(
        request_id=REQUEST_ID,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Review the current changes."),
        workspace=ClientReviewWorkspace.create(patch=patch),
        execution=ReviewExecutionOptions(
            llm_conf={"primary": {"model": "test-model"}},
            tools=_tools(),
            metadata={"cid": CID, "sid": SID},
        ),
    )
    return ReviewStreamRequest.from_dict(wire_request.request_payload())


def _command(
    *,
    command_id: str = "command-review",
    idempotency_key: str = "intent-review",
    patch: str = "diff --git a/a.py b/a.py\n",
) -> SubmitReviewCommand:
    """构造具有稳定本地身份的 Review Run 命令。"""
    return SubmitReviewCommand.create(
        command_id=command_id,
        session_id="session-review",
        run_id="run-review",
        idempotency_key=idempotency_key,
        request=_request(patch=patch),
        environment_snapshot={
            "snapshot_id": "envsnap-review",
            "workspace": {"root": "D:/workspace"},
        },
    )


def _event(
    command: SubmitReviewCommand,
    sequence: int,
    kind: str,
    status: str,
) -> RunEvent:
    """构造用于直接存储测试的连续 Run 事件。"""
    return RunEvent.create(
        sequence=sequence,
        session_id=command.session_id,
        run_id=command.run_id,
        kind=kind,
        payload={"status": status},
        causation_id=command.command_id,
    )


async def _append_started(
    store: SQLiteRunStore,
    command: SubmitReviewCommand,
) -> None:
    """提交一项可以冻结远端请求的 running Run。"""
    await store.append_event(
        command,
        _event(command, 1, "run_queued", "queued"),
    )
    await store.append_event(
        command,
        _event(command, 2, "run_started", "running"),
    )


def test_review_command_freezes_round_trips_and_keeps_fingerprint() -> None:
    """确保 Review 命令的请求与环境可精确恢复。"""
    environment = {
        "snapshot_id": "envsnap-review",
        "workspace": {"root": "D:/workspace"},
    }
    command = SubmitReviewCommand.create(
        command_id="command-review",
        session_id="session-review",
        run_id="run-review",
        request=_request(),
        environment_snapshot=environment,
    )
    environment["workspace"]["root"] = "D:/changed"

    restored = parse_run_command(command.to_dict())

    assert restored == command
    assert restored.fingerprint() == command.fingerprint()
    assert command.environment_snapshot_value() == {
        "snapshot_id": "envsnap-review",
        "workspace": {"root": "D:/workspace"},
    }
    assert restored.request.to_dict() == command.request.to_dict()


@pytest.mark.anyio
async def test_review_executor_persists_request_before_remote_operation(
    tmp_path: Path,
) -> None:
    """确保 Review 远端操作只在冻结请求入账后开始。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    application: TurnApplication[RunResult] = TurnApplication(
        store,
        runtime_factory=SessionRuntimeOwner,
    )
    command = _command()

    async def operation(request, environment_snapshot) -> RunResult:
        persisted = await store.load_remote_request(command.run_id)
        assert persisted is not None
        assert persisted.request == request
        assert persisted.revision == 1
        assert environment_snapshot == command.environment_snapshot_value()
        return RunResult(status="completed")

    executor = ReviewCommandExecutor(
        operation,
        request_recorder=application,
    )
    result = await application.submit(command, executor)
    await application.close()

    assert result.projection.status == "completed"
    assert [event.kind for event in result.events] == [
        "run_queued",
        "run_started",
        "run_completed",
    ]


@pytest.mark.anyio
async def test_review_unknown_submission_survives_restart_for_recovery(
    tmp_path: Path,
) -> None:
    """确保未知提交不会被记为失败或重新创建。"""
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    application: TurnApplication[RunResult] = TurnApplication(
        store,
        runtime_factory=SessionRuntimeOwner,
    )
    command = _command()

    async def operation(_request, _environment_snapshot) -> RunResult:
        raise ModelCapabilityError(
            "review_submission_unknown",
            "connection lost",
            retryable=True,
            details={"submission_unknown": True},
        )

    with pytest.raises(ModelCapabilityError):
        await application.submit(
            command,
            ReviewCommandExecutor(
                operation,
                request_recorder=application,
            ),
        )
    await application.close()

    restarted = SQLiteRunStore(db_path)
    recoveries = await restarted.recover_session(command.session_id)
    persisted = await restarted.load_remote_request(command.run_id)

    assert len(recoveries) == 1
    assert recoveries[0].status.value == "reconciliation_required"
    assert recoveries[0].command == command
    assert persisted is not None
    assert persisted.request == command.request
    assert persisted.revision == 1


@pytest.mark.anyio
async def test_review_worker_restart_attaches_frozen_remote_turn(
    tmp_path: Path,
) -> None:
    """确保 Worker 重启后只按权威水位观察原 Review Turn。"""
    db_path = tmp_path / "runtime.db"
    command = _command()
    first_store = SQLiteRunStore(db_path)
    first_application: TurnApplication[RunResult] = TurnApplication(
        first_store,
        runtime_factory=SessionRuntimeOwner,
    )

    async def unknown_operation(_request, _environment_snapshot) -> RunResult:
        raise ModelCapabilityError(
            "review_submission_unknown",
            "worker stopped after remote submission",
            retryable=True,
            details={"submission_unknown": True},
        )

    with pytest.raises(ModelCapabilityError):
        await first_application.submit(
            command,
            ReviewCommandExecutor(
                unknown_operation,
                request_recorder=first_application,
            ),
        )
    await first_application.close()

    restarted_store = SQLiteRunStore(db_path)
    restarted: TurnApplication[RunResult] = TurnApplication(
        restarted_store,
        runtime_factory=SessionRuntimeOwner,
    )
    protocol_client = AsyncMock(spec=ProtocolCommandClient)
    protocol_client.get_turn_status.return_value = TurnStatusSnapshot(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        run_id="remote-review-run",
        status="running",
        terminal=None,
        attempt=1,
        version=2,
        last_event_seq=9,
        created_at=1.0,
        updated_at=2.0,
    )

    recovery = await restarted.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )

    assert recovery.redispatch_reviews == ()
    assert recovery.restore_commands == ()
    assert recovery.resolved_run_ids == ()
    assert len(recovery.observe_turns) == 1
    observed = recovery.observe_turns[0]
    assert observed.snapshot.command == command
    assert observed.request == command.request
    assert observed.replay_target_seq == 9
    protocol_client.get_turn_status.assert_awaited_once_with(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
    )
    persisted = await restarted_store.load_remote_request(command.run_id)
    assert persisted is not None
    assert persisted.request == command.request
    await restarted.close()


@pytest.mark.anyio
async def test_review_client_exit_preserves_frozen_recovery_after_restart(
    tmp_path: Path,
) -> None:
    """确保远端登记后的客户端退出不会丢失 Review 恢复身份。"""
    db_path = tmp_path / "runtime.db"
    store = SQLiteRunStore(db_path)
    application: TurnApplication[RunResult] = TurnApplication(
        store,
        runtime_factory=SessionRuntimeOwner,
    )
    command = _command()
    remote_started = asyncio.Event()

    async def operation(_request, _environment_snapshot) -> RunResult:
        remote_started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    running = asyncio.create_task(application.submit(
        command,
        ReviewCommandExecutor(
            operation,
            request_recorder=application,
        ),
    ))
    await remote_started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await application.close()

    restarted = SQLiteRunStore(db_path)
    recoveries = await restarted.recover_session(command.session_id)
    persisted = await restarted.load_remote_request(command.run_id)

    assert len(recoveries) == 1
    assert recoveries[0].status.value == "reconciliation_required"
    assert recoveries[0].command == command
    assert persisted is not None
    assert persisted.request == command.request


@pytest.mark.anyio
async def test_missing_remote_review_requeues_the_exact_frozen_command(
    tmp_path: Path,
) -> None:
    """确保权威 404 把未知 Review 恢复为可持久重派的原命令。"""
    db_path = tmp_path / "runtime.db"
    command = _command()
    first_store = SQLiteRunStore(db_path)
    first_application: TurnApplication[RunResult] = TurnApplication(
        first_store,
        runtime_factory=SessionRuntimeOwner,
    )

    async def unknown_operation(_request, _environment_snapshot) -> RunResult:
        raise ModelCapabilityError(
            "review_submission_unknown",
            "response lost",
            retryable=True,
            details={"submission_unknown": True},
        )

    with pytest.raises(ModelCapabilityError):
        await first_application.submit(
            command,
            ReviewCommandExecutor(
                unknown_operation,
                request_recorder=first_application,
            ),
        )
    await first_application.close()

    restarted_store = SQLiteRunStore(db_path)
    restarted: TurnApplication[RunResult] = TurnApplication(
        restarted_store,
        runtime_factory=SessionRuntimeOwner,
    )
    protocol_client = AsyncMock(spec=ProtocolCommandClient)
    protocol_client.get_turn_status.side_effect = ProtocolCommandError(
        "turn_status_request_failed",
        "remote turn does not exist",
        details={"status_code": 404},
    )

    recovery = await restarted.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )

    assert recovery.redispatch_reviews == (command,)
    assert recovery.restore_commands == ()
    assert recovery.resolved_run_ids == ()
    assert len(recovery.pending) == 1
    assert recovery.pending[0].status.value == "queued"
    assert recovery.pending[0].recovery_action.value == "redispatch"
    events = await restarted_store.load_events(command.run_id)
    assert [event.kind for event in events] == [
        "run_queued",
        "run_started",
        "run_reconciliation_required",
        "run_redispatch_queued",
    ]
    assert events[-1].payload["recovery"] == {
        "resolution": "not_executed",
        "authority": "remote_turn_status_404",
    }

    async def complete_operation(request, environment_snapshot) -> RunResult:
        assert request == command.request
        assert environment_snapshot == command.environment_snapshot_value()
        return RunResult(status="completed", assistant_text="No findings.")

    result = await restarted.submit(
        command,
        ReviewCommandExecutor(
            complete_operation,
            request_recorder=restarted,
        ),
    )
    assert result.projection.status == "completed"
    assert await restarted.recover_session(command.session_id) == ()
    assert (await restarted_store.load_remote_request(command.run_id)) is not None
    await restarted.close()


@pytest.mark.anyio
async def test_review_requeue_is_serialized_by_the_session_owner(
    tmp_path: Path,
) -> None:
    """确保恢复重派只能由 Session 单写者提交一次。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command()
    await _append_started(store, command)
    await store.append_event(
        command,
        _event(
            command,
            3,
            "run_reconciliation_required",
            "reconciliation_required",
        ),
    )
    snapshot = (await store.recover_session(command.session_id))[0]
    owner: SessionRuntimeOwner[RunResult] = SessionRuntimeOwner(store)

    await owner.requeue_recovery(snapshot, authority="remote_turn_status_404")

    with pytest.raises(
        RunPersistenceConflict,
        match="recovery snapshot cannot be requeued",
    ):
        await owner.requeue_recovery(
            snapshot,
            authority="remote_turn_status_404",
        )

    events = await store.load_events(command.run_id)
    assert [event.kind for event in events] == [
        "run_queued",
        "run_started",
        "run_reconciliation_required",
        "run_redispatch_queued",
    ]
    await owner.close()


@pytest.mark.anyio
async def test_review_store_reuses_exact_request_and_rejects_conflict(
    tmp_path: Path,
) -> None:
    """确保重复冻结幂等且变更过的快照被拒绝。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command()
    await _append_started(store, command)

    first = await store.save_remote_request(command, command.request)
    duplicate = await store.save_remote_request(command, command.request)

    assert duplicate == first
    with pytest.raises(
        RunPersistenceConflict,
        match="does not match frozen command",
    ):
        await store.save_remote_request(
            command,
            _request(patch="diff --git a/b.py b/b.py\n"),
        )
    with pytest.raises(TypeError, match="requires a review request"):
        await store.save_remote_request(
            command,
            ModelStreamRequest(
                cid=CID,
                sid=SID,
                turn_id=TURN_ID,
                pref_config={},
                message="review",
                tools=(),
            ),
        )


@pytest.mark.anyio
async def test_review_session_deduplicates_repeated_command() -> None:
    """确保相同幂等意图在 Session 内只执行一次。"""
    first = _command(command_id="command-review-first")
    duplicate = _command(command_id="command-review-retry")
    calls: list[str] = []

    async def execute(_command) -> RunResult:
        calls.append("execute")
        await asyncio.sleep(0)
        return RunResult(status="completed")

    application: TurnApplication[RunResult] = TurnApplication(
        runtime_factory=SessionRuntimeOwner,
    )
    first_result, duplicate_result = await asyncio.gather(
        application.submit(first, execute),
        application.submit(duplicate, execute),
    )
    await application.close()

    assert calls == ["execute"]
    assert duplicate_result.value is first_result.value


@pytest.mark.anyio
async def test_queued_review_recovery_preserves_exact_command_for_redispatch(
    tmp_path: Path,
) -> None:
    """确保网络前退出不会把冻结 Review 降级成普通输入草稿。"""
    store = SQLiteRunStore(tmp_path / "runtime.db")
    command = _command()
    await store.append_event(
        command,
        _event(command, 1, "run_queued", "queued"),
    )
    application: TurnApplication[RunResult] = TurnApplication(
        store,
        runtime_factory=SessionRuntimeOwner,
    )
    protocol_client = AsyncMock(spec=ProtocolCommandClient)

    recovery = await application.reconcile_remote_session(
        command.session_id,
        protocol_client,
    )
    await application.close()

    assert recovery.redispatch_reviews == (command,)
    assert recovery.restore_commands == ()
    assert recovery.resolved_run_ids == ()
    assert recovery.pending[0].command == command
    protocol_client.get_turn_status.assert_not_awaited()
