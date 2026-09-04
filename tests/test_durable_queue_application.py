# -*- coding: utf-8 -*-

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.application.turns.durable_queue import DurableQueueApplication
from agent.ports import (
    DurableQueueClient,
    ProtocolCommandClient,
    ProtocolCommandError,
)
from agent.protocol import (
    DurableQueueInput,
    DurableQueueItem,
    DurableQueueMutationReceipt,
    DurableQueueSnapshot,
    DurableQueueStartReceipt,
    ModelStreamRequest,
    SubmitTurnCommand,
    TurnStatusSnapshot,
)
from agent.stores import SQLiteDurableQueueStore


def _request() -> ModelStreamRequest:
    """创建一项冻结的 Queue 模型请求。"""
    return ModelStreamRequest(
        cid="cid_queue_application",
        sid="sid_queue_application",
        turn_id="turn_queue_application_0001",
        pref_config={"primary": {"model": "gpt-test", "apikey": "secret"}},
        message="queued input",
        tools=(),
        attachments=({"filename": "input.txt", "content": "frozen"},),
        environment_snapshot={"snapshot_id": "env-before-queue"},
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
        },
    )


def _command(request: ModelStreamRequest) -> SubmitTurnCommand:
    """创建与 Queue 请求绑定的本地 Run 命令。"""
    return SubmitTurnCommand.create(
        command_id="command_queue_application_0001",
        session_id="local_queue_application",
        run_id="run_queue_application_0001",
        idempotency_key="intent_queue_application_0001",
        message=request.message,
        pref_config=request.pref_config_value(),
        attachments=request.attachment_values(),
        environment_snapshot=request.environment_snapshot_value(),
        trace_context={
            "remote_turn": {
                "cid": request.cid,
                "sid": request.sid,
                "turn_id": request.turn_id,
            },
        },
    )


def _item(
    request: ModelStreamRequest,
    *,
    submission_id: str = "submission_queue_application_0001",
    client_message_id: str = "message_queue_application_0001",
) -> DurableQueueItem:
    """创建与冻结意图一致的服务端 Queue item。"""
    return DurableQueueItem(
        queue_seq=1,
        cid=request.cid,
        sid=request.sid,
        submission_id=submission_id,
        client_message_id=client_message_id,
        turn_id=request.turn_id,
        position=1,
        status="queued",
        input=DurableQueueInput(
            text=request.message,
            attachments=request.attachments,
        ),
        created_at=1.0,
        updated_at=1.0,
    )


def _application(
    tmp_path: Path,
) -> tuple[DurableQueueApplication, AsyncMock, AsyncMock, SQLiteDurableQueueStore]:
    """组合使用真实本地账本和模拟远端端口的 Queue 用例。"""
    queue_client = AsyncMock(spec=DurableQueueClient)
    protocol_client = AsyncMock(spec=ProtocolCommandClient)
    store = SQLiteDurableQueueStore(tmp_path / "durable_queue.db")
    return (
        DurableQueueApplication(queue_client, protocol_client, store),
        queue_client,
        protocol_client,
        store,
    )


@pytest.mark.anyio
async def test_enqueue_persists_before_network_and_retries_same_identity(
    tmp_path: Path,
) -> None:
    """确保 add response lost 不会生成第二项输入或重新读取配置。"""
    application, queue_client, _protocol_client, store = _application(tmp_path)
    request = _request()
    command = _command(request)
    transport_error = ProtocolCommandError(
        "durable_queue_transport_failed",
        "connection lost",
        retryable=True,
    )
    queue_client.add_queue_submission.side_effect = transport_error

    with pytest.raises(ProtocolCommandError, match="connection lost"):
        await application.enqueue(
            command,
            request,
            submission_id="submission_queue_application_0001",
            client_message_id="message_queue_application_0001",
            request_id="request_queue_add_application_0001",
        )

    persisted = await store.find("submission_queue_application_0001")
    assert persisted is not None
    assert persisted.status == "adding"
    queue_client.add_queue_submission.side_effect = None
    queue_client.add_queue_submission.return_value = DurableQueueMutationReceipt(
        request_id=persisted.add_request_id,
        queue_version=3,
        item=_item(request),
    )

    result = await application.retry_add(persisted.submission_id)

    assert result.local.status == "queued"
    assert queue_client.add_queue_submission.await_count == 2
    for call in queue_client.add_queue_submission.await_args_list:
        assert call.args == (request,)
        assert call.kwargs == {
            "submission_id": persisted.submission_id,
            "client_message_id": persisted.client_message_id,
            "request_id": persisted.add_request_id,
        }


@pytest.mark.anyio
async def test_snapshot_confirms_response_lost_add_without_resubmission(
    tmp_path: Path,
) -> None:
    """确保远端快照可以确认已提交但回执丢失的 add。"""
    application, queue_client, _protocol_client, store = _application(tmp_path)
    request = _request()
    command = _command(request)
    local = await store.create(
        command,
        request,
        submission_id="submission_queue_application_0001",
        client_message_id="message_queue_application_0001",
        add_request_id="request_queue_add_application_0001",
    )
    queue_client.list_queue_submissions.return_value = DurableQueueSnapshot(
        cid=request.cid,
        sid=request.sid,
        queue_version=7,
        items=(_item(request),),
    )

    snapshot = await application.snapshot(cid=request.cid, sid=request.sid)

    assert snapshot.queue_version == 7
    assert snapshot.entries[0].executable
    assert snapshot.entries[0].local is not None
    assert snapshot.entries[0].local.status == "queued"
    assert snapshot.local_only == ()
    queue_client.add_queue_submission.assert_not_awaited()
    recovered = await store.find(local.submission_id)
    assert recovered is not None
    assert recovered.status == "queued"


@pytest.mark.anyio
async def test_unknown_start_reuses_request_id_until_receipt_arrives(
    tmp_path: Path,
) -> None:
    """确保 start response lost 后仅重试同一服务端命令。"""
    application, queue_client, _protocol_client, store = _application(tmp_path)
    request = _request()
    local = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_application_0001",
        client_message_id="message_queue_application_0001",
        add_request_id="request_queue_add_application_0001",
    )
    await store.mark_queued(
        local.submission_id,
        add_request_id=local.add_request_id,
        queue_version=2,
    )
    queue_client.start_queue_submission.side_effect = ProtocolCommandError(
        "durable_queue_transport_failed",
        "response lost",
        retryable=True,
    )

    with pytest.raises(ProtocolCommandError, match="response lost"):
        await application.start(
            cid=request.cid,
            sid=request.sid,
            submission_id=local.submission_id,
            request_id="request_queue_start_application_0001",
        )

    uncertain = await store.find(local.submission_id)
    assert uncertain is not None
    assert uncertain.status == "starting"
    queue_client.start_queue_submission.side_effect = None
    queue_client.start_queue_submission.return_value = DurableQueueStartReceipt(
        request_id="request_queue_start_application_0001",
        queue_version=3,
        submission_id=local.submission_id,
        turn_id=request.turn_id,
    )

    result = await application.start(
        cid=request.cid,
        sid=request.sid,
        submission_id=local.submission_id,
        request_id="request_queue_start_application_9999",
    )

    assert result.local.status == "started"
    assert [
        call.kwargs["request_id"]
        for call in queue_client.start_queue_submission.await_args_list
    ] == [
        "request_queue_start_application_0001",
        "request_queue_start_application_0001",
    ]


@pytest.mark.anyio
async def test_deterministic_busy_start_allows_new_explicit_attempt(
    tmp_path: Path,
) -> None:
    """确保确定 busy 不会把旧 request id 永久冻结到后续尝试。"""
    application, queue_client, _protocol_client, store = _application(tmp_path)
    request = _request()
    local = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_application_0001",
        client_message_id="message_queue_application_0001",
        add_request_id="request_queue_add_application_0001",
    )
    await store.mark_queued(
        local.submission_id,
        add_request_id=local.add_request_id,
        queue_version=1,
    )
    queue_client.start_queue_submission.side_effect = ProtocolCommandError(
        "session_busy",
        "session is busy",
        details={"status_code": 409},
    )

    with pytest.raises(ProtocolCommandError, match="session is busy"):
        await application.start(
            cid=request.cid,
            sid=request.sid,
            submission_id=local.submission_id,
            request_id="request_queue_start_application_0001",
        )

    reset = await store.find(local.submission_id)
    assert reset is not None
    assert reset.status == "queued"
    assert reset.start_request_id is None
    queue_client.start_queue_submission.side_effect = None
    queue_client.start_queue_submission.return_value = DurableQueueStartReceipt(
        request_id="request_queue_start_application_0002",
        queue_version=2,
        submission_id=local.submission_id,
        turn_id=request.turn_id,
    )

    result = await application.start(
        cid=request.cid,
        sid=request.sid,
        submission_id=local.submission_id,
        request_id="request_queue_start_application_0002",
    )

    assert result.local.start_request_id == "request_queue_start_application_0002"
    assert result.local.status == "started"


@pytest.mark.anyio
async def test_cold_reconcile_identifies_started_turn_without_restarting(
    tmp_path: Path,
) -> None:
    """确保 start 回执丢失后使用 Turn 状态确认，不再调用 queue.start。"""
    application, queue_client, protocol_client, store = _application(tmp_path)
    request = _request()
    local = await store.create(
        _command(request),
        request,
        submission_id="submission_queue_application_0001",
        client_message_id="message_queue_application_0001",
        add_request_id="request_queue_add_application_0001",
    )
    await store.mark_queued(
        local.submission_id,
        add_request_id=local.add_request_id,
        queue_version=4,
    )
    await store.begin_start(
        local.submission_id,
        request_id="request_queue_start_application_0001",
    )
    queue_client.list_queue_submissions.return_value = DurableQueueSnapshot(
        cid=request.cid,
        sid=request.sid,
        queue_version=5,
        items=(),
    )
    protocol_client.get_turn_status.return_value = TurnStatusSnapshot(
        cid=request.cid,
        sid=request.sid,
        turn_id=request.turn_id,
        run_id="remote_queue_run_0001",
        status="running",
        terminal=None,
        attempt=1,
        version=2,
        last_event_seq=12,
        created_at=1.0,
        updated_at=2.0,
    )

    reconciled = await application.reconcile(cid=request.cid, sid=request.sid)

    queue_client.start_queue_submission.assert_not_awaited()
    persisted = await store.find(local.submission_id)
    assert persisted is not None
    assert persisted.status == "started"
    assert persisted.start_request_id == "request_queue_start_application_0001"
    assert len(reconciled.local_only) == 1
    assert reconciled.local_only[0].status == "started"


@pytest.mark.anyio
async def test_remote_item_without_local_execution_snapshot_is_not_executable(
    tmp_path: Path,
) -> None:
    """确保丢失本地权限快照时仍可展示，但不会用当前配置猜测执行。"""
    application, queue_client, _protocol_client, _store = _application(tmp_path)
    request = _request()
    queue_client.list_queue_submissions.return_value = DurableQueueSnapshot(
        cid=request.cid,
        sid=request.sid,
        queue_version=1,
        items=(_item(request),),
    )

    snapshot = await application.snapshot(cid=request.cid, sid=request.sid)

    assert len(snapshot.entries) == 1
    assert not snapshot.entries[0].executable
    assert snapshot.entries[0].local is None
