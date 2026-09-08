# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.adapters.protocol.review_request import build_review_stream_request
from agent.adapters.turns.review import ReviewCommandExecutor
from agent.application.turns.commands import TurnApplication
from agent.application.turns.run_result import RunResult
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.ports import (
    ModelCapabilityError,
    ProtocolCommandClient,
    RunPersistenceConflict,
)
from agent.protocol import (
    ModelStreamRequest,
    ReviewStreamRequest,
    RunEvent,
    SubmitReviewCommand,
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
            metadata={"cid": CID, "sid": SID},
        ),
    )
    return build_review_stream_request(wire_request)


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
