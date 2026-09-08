# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from agent.ports import DurableQueuePersistenceConflict
from agent.protocol import (
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.stores import SQLiteDurableQueueStore


def _request(
    *,
    turn_id: str = "turn_queue_store_0001",
    message: str = "queued request",
) -> ModelStreamRequest:
    """创建包含权限和环境快照的冻结 Queue 请求。"""
    return ModelStreamRequest(
        cid="cid_queue_store",
        sid="sid_queue_store",
        turn_id=turn_id,
        pref_config={
            "primary": {
                "provider": "openai",
                "model": "gpt-test",
                "apikey": "secret",
            },
        },
        message=message,
        tools=({"name": "read_file", "inputSchema": {"type": "object"}},),
        attachments=({"filename": "note.txt", "content": "frozen"},),
        environment_snapshot={"snapshot_id": "environment-before-add"},
        metadata={"source": "tui:queue"},
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
        },
        timeout=90.0,
    )


def _command(
    request: ModelStreamRequest,
    *,
    run_id: str = "run_queue_store_0001",
) -> SubmitTurnCommand:
    """创建绑定同一远端 Turn 的本地 Run 命令。"""
    return SubmitTurnCommand.create(
        command_id=f"command_{run_id}",
        session_id="local_queue_session",
        run_id=run_id,
        idempotency_key=f"intent_{run_id}",
        message=request.message,
        attachments=request.attachment_values(),
        environment_snapshot=request.environment_snapshot_value(),
        pref_config=request.pref_config_value(),
        trace_context={
            "remote_turn": {
                "cid": request.cid,
                "sid": request.sid,
                "turn_id": request.turn_id,
            },
        },
    )


@pytest.mark.anyio
async def test_queue_store_persists_frozen_execution_before_remote_add(
    tmp_path: Path,
) -> None:
    """确保进程重启后仍能读取入队时完整配置、权限和环境。"""
    db_path = tmp_path / "durable_queue.db"
    request = _request()
    command = _command(request)
    first_store = SQLiteDurableQueueStore(db_path)

    created = await first_store.create(
        command,
        request,
        submission_id="submission_queue_store_0001",
        client_message_id="message_queue_store_0001",
        add_request_id="request_queue_add_store_0001",
    )

    assert created.status == "adding"
    assert created.queue_version == 0
    second_store = SQLiteDurableQueueStore(db_path)
    recovered = await second_store.find(created.submission_id)

    assert recovered is not None
    assert recovered.request == request
    assert recovered.command == command
    assert recovered.request.option_values()["permissions"] == {
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
        "approvals_reviewer": "user",
        "network_access": "restricted",
    }
    assert recovered.request.pref_config_value()["primary"]["apikey"] == "secret"


@pytest.mark.anyio
async def test_queue_store_reuses_identical_add_and_rejects_identity_collision(
    tmp_path: Path,
) -> None:
    """确保 exactly-once identity 只能映射到一项冻结意图。"""
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    request = _request()
    command = _command(request)
    values = {
        "submission_id": "submission_queue_store_0001",
        "client_message_id": "message_queue_store_0001",
        "add_request_id": "request_queue_add_store_0001",
    }

    first = await store.create(command, request, **values)
    repeated = await store.create(command, request, **values)

    assert repeated == first
    changed_request = _request(message="different request")
    changed_command = _command(changed_request)
    with pytest.raises(
        DurableQueuePersistenceConflict,
        match="identity conflicts",
    ):
        await store.create(changed_command, changed_request, **values)


@pytest.mark.anyio
async def test_queue_store_preserves_unknown_start_request_until_resolved(
    tmp_path: Path,
) -> None:
    """确保 response lost 后复用同一 start ID，明确拒绝后才能换新 ID。"""
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    request = _request()
    created = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_store_0001",
        client_message_id="message_queue_store_0001",
        add_request_id="request_queue_add_store_0001",
    )
    queued = await store.mark_queued(
        created.submission_id,
        add_request_id=created.add_request_id,
        queue_version=4,
    )

    starting = await store.begin_start(
        queued.submission_id,
        request_id="request_queue_start_store_0001",
    )
    repeated = await store.begin_start(
        queued.submission_id,
        request_id="request_queue_start_store_0001",
    )

    assert starting.status == "starting"
    assert repeated == starting
    with pytest.raises(
        DurableQueuePersistenceConflict,
        match="already in progress",
    ):
        await store.begin_start(
            queued.submission_id,
            request_id="request_queue_start_store_0002",
        )

    reset = await store.reset_start(
        queued.submission_id,
        request_id="request_queue_start_store_0001",
    )
    next_attempt = await store.begin_start(
        queued.submission_id,
        request_id="request_queue_start_store_0002",
    )

    assert reset.status == "queued"
    assert reset.start_request_id is None
    assert next_attempt.status == "starting"
    assert next_attempt.start_request_id == "request_queue_start_store_0002"


@pytest.mark.anyio
async def test_queue_store_started_transition_is_monotonic_and_idempotent(
    tmp_path: Path,
) -> None:
    """确保 Queue 到 Turn 的本地绑定不能倒退或切换身份。"""
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    request = _request()
    created = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_store_0001",
        client_message_id="message_queue_store_0001",
        add_request_id="request_queue_add_store_0001",
    )
    queued = await store.mark_queued(
        created.submission_id,
        add_request_id=created.add_request_id,
        queue_version=5,
    )
    starting = await store.begin_start(
        queued.submission_id,
        request_id="request_queue_start_store_0001",
    )
    started = await store.mark_started(
        starting.submission_id,
        request_id="request_queue_start_store_0001",
        turn_id=request.turn_id,
        queue_version=6,
    )
    repeated = await store.mark_started(
        starting.submission_id,
        request_id="request_queue_start_store_0001",
        turn_id=request.turn_id,
        queue_version=6,
    )

    assert started.status == "started"
    assert repeated == started
    with pytest.raises(
        DurableQueuePersistenceConflict,
        match="cannot return to queued",
    ):
        await store.mark_queued(
            started.submission_id,
            add_request_id=created.add_request_id,
            queue_version=7,
        )
    with pytest.raises(
        DurableQueuePersistenceConflict,
        match="version cannot move backwards",
    ):
        await store.mark_started(
            started.submission_id,
            request_id="request_queue_start_store_0001",
            turn_id=request.turn_id,
            queue_version=3,
        )


@pytest.mark.anyio
async def test_queue_store_settles_started_turn_once(tmp_path: Path) -> None:
    """确保只有已经启动的 Queue Turn 能关闭本地观察恢复入口。"""
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    request = _request()
    created = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_store_0001",
        client_message_id="message_queue_store_0001",
        add_request_id="request_queue_add_store_0001",
    )
    queued = await store.mark_queued(
        created.submission_id,
        add_request_id=created.add_request_id,
        queue_version=1,
    )
    starting = await store.begin_start(
        queued.submission_id,
        request_id="request_queue_start_store_0001",
    )
    started = await store.mark_started(
        starting.submission_id,
        request_id="request_queue_start_store_0001",
        turn_id=request.turn_id,
        queue_version=2,
    )

    settled = await store.mark_settled(started.submission_id)
    repeated = await store.mark_settled(started.submission_id)

    assert settled.status == "settled"
    assert repeated == settled

    unstarted = await store.create(
        _command(_request(turn_id="turn_queue_store_0002")),
        _request(turn_id="turn_queue_store_0002"),
        submission_id="submission_queue_store_0002",
        client_message_id="message_queue_store_0002",
        add_request_id="request_queue_add_store_0002",
    )
    with pytest.raises(
        DurableQueuePersistenceConflict,
        match="can settle",
    ):
        await store.mark_settled(unstarted.submission_id)


@pytest.mark.anyio
async def test_queue_store_rejects_command_request_coordinate_mismatch(
    tmp_path: Path,
) -> None:
    """确保本地恢复命令不能观察另一项远端 Turn。"""
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    request = _request()
    other = _request(turn_id="turn_queue_store_0002")

    with pytest.raises(ValueError, match="coordinates do not match"):
        await store.create(
            _command(request),
            other,
            submission_id="submission_queue_store_0001",
            client_message_id="message_queue_store_0001",
            add_request_id="request_queue_add_store_0001",
        )
