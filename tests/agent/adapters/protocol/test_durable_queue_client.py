# -*- coding: utf-8 -*-

import json
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest

from agent.adapters.protocol import client as adapter_module
from agent.adapters.protocol.client import MindChatProtocolClient
from agent.ports import (
    DurableQueueClient,
    ProtocolCommandError,
)
from agent.protocol import (
    DurableQueueMutationReceipt,
    ModelStreamRequest,
)
from protocol.client import durable_queue
from protocol.schema.durable_queue import (
    DurableQueueResponseError,
    DurableQueueItem,
    QueueInputSnapshot,
    QueueMutationResponse,
)
class _ClientFactory:
    """按顺序返回 Queue HTTP 响应并记录实际请求。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, *, timeout):
        factory = self

        class ClientStub:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **kwargs):
                return self._respond("GET", url, kwargs)

            async def post(self, url, **kwargs):
                return self._respond("POST", url, kwargs)

            async def request(self, method, url, **kwargs):
                return self._respond(method, url, kwargs)

            @staticmethod
            def _respond(method, url, kwargs):
                factory.calls.append((timeout, method, url, kwargs))
                outcome = factory.outcomes.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        return ClientStub()


def _fixture(fixtures_root: Path) -> dict:
    """读取双方可复用的 Queue 契约 fixture。"""
    fixture_path = fixtures_root / "protocol" / "durable_queue.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _response(data: dict, *, status_code: int = 200) -> httpx.Response:
    """创建绑定请求上下文的 JSON 响应。"""
    return httpx.Response(
        status_code,
        json=data,
        request=httpx.Request("POST", "https://example.test/queue"),
    )


def _model_request() -> ModelStreamRequest:
    """构造可冻结到服务端队列的模型请求。"""
    return ModelStreamRequest(
        cid="cid_queue",
        sid="sid_queue",
        turn_id="turn_queue_0001",
        pref_config={
            "primary": {
                "kind": "openai",
                "model": "gpt-test",
                "apikey": "test-key",
            },
        },
        message="queued input",
        tools=(),
        metadata={"origin": "queue-test"},
        options={"streaming": True, "session_mode": "existing"},
    )


@pytest.mark.anyio
async def test_queue_add_uses_independent_endpoint_and_frozen_identity(
    monkeypatch,
    fixtures_root: Path,
) -> None:
    fixture = _fixture(fixtures_root)
    item = fixture["item"]
    factory = _ClientFactory([_response({
        "ok": True,
        "data": {
            "request_id": "request_queue_0001",
            "status": "accepted",
            "queue_version": 1,
            "item": item,
        },
    })])
    monkeypatch.setattr(durable_queue.httpx, "AsyncClient", factory)
    monkeypatch.setattr(
        durable_queue.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(
        durable_queue,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    request = _model_request()

    result = await durable_queue.add_queue_submission(
        cid=request.cid,
        sid=request.sid,
        turn_id=request.turn_id,
        client_message_id=item["client_message_id"],
        submission_id=item["submission_id"],
        pref_config=request.pref_config_value(),
        message=request.message,
        tools=request.tool_values(),
        attachments=request.attachment_values(),
        environment_snapshot=request.environment_snapshot_value(),
        metadata=request.metadata_value(),
        options=request.option_values(),
        request_id="request_queue_0001",
    )

    assert result.item.submission_id == item["submission_id"]
    assert len(factory.calls) == 1
    _timeout, method, url, values = factory.calls[0]
    assert (method, url) == ("POST", "https://example.test/queue")
    assert values["params"] == {"cid": "cid_queue", "sid": "sid_queue"}
    assert values["json"]["request_id"] == "request_queue_0001"
    assert values["json"]["submission_id"] == item["submission_id"]
    queued_request = values["json"]["request"]
    assert queued_request["turn_id"] == item["turn_id"]
    assert queued_request["metadata"] == {
        "origin": "queue-test",
        "cid": "cid_queue",
        "sid": "sid_queue",
    }
    assert queued_request["llm_conf"]["primary"]["provider"] == "openai"
    assert "/mind-chat" not in url


@pytest.mark.anyio
async def test_queue_commands_use_declared_methods_and_authoritative_results(
    monkeypatch,
    fixtures_root: Path,
) -> None:
    fixture = _fixture(fixtures_root)
    item = fixture["item"]
    deleted = {**item, "position": None, "status": "deleted", "deleted_at": 3.0}
    outcomes = [
        _response({"ok": True, "data": {
            "cid": "cid_queue",
            "sid": "sid_queue",
            "queue_version": 1,
            "items": [item],
        }}),
        _response({"ok": True, "data": {
            "request_id": "request_queue_update",
            "status": "accepted",
            "queue_version": 2,
            "item": item,
        }}),
        _response({"ok": True, "data": {
            "request_id": "request_queue_delete",
            "status": "accepted",
            "queue_version": 3,
            "item": deleted,
        }}),
        _response({"ok": True, "data": {
            "request_id": "request_queue_reorder",
            "status": "accepted",
            "queue_version": 4,
            "submission_ids": [item["submission_id"]],
        }}),
        _response({"ok": True, "data": {
            "request_id": "request_queue_start",
            "status": "started",
            "queue_version": 5,
            "submission_id": item["submission_id"],
            "turn_id": item["turn_id"],
        }}),
    ]
    factory = _ClientFactory(outcomes)
    monkeypatch.setattr(durable_queue.httpx, "AsyncClient", factory)
    monkeypatch.setattr(
        durable_queue.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    request = _model_request()

    snapshot = await durable_queue.list_queue_submissions(
        cid=request.cid,
        sid=request.sid,
    )
    updated = await durable_queue.update_queue_submission(
        cid=request.cid,
        sid=request.sid,
        submission_id=item["submission_id"],
        turn_id=request.turn_id,
        pref_config=request.pref_config_value(),
        message=request.message,
        tools=request.tool_values(),
        attachments=request.attachment_values(),
        environment_snapshot=request.environment_snapshot_value(),
        metadata=request.metadata_value(),
        options=request.option_values(),
        request_id="request_queue_update",
    )
    removed = await durable_queue.delete_queue_submission(
        cid=request.cid,
        sid=request.sid,
        submission_id=item["submission_id"],
        request_id="request_queue_delete",
    )
    reordered = await durable_queue.reorder_queue_submissions(
        cid=request.cid,
        sid=request.sid,
        submission_ids=(item["submission_id"],),
        request_id="request_queue_reorder",
    )
    started = await durable_queue.start_queue_submission(
        cid=request.cid,
        sid=request.sid,
        submission_id=item["submission_id"],
        request_id="request_queue_start",
    )

    assert snapshot.items[0].position == 1
    assert updated.queue_version == 2
    assert removed.item.status == "deleted"
    assert reordered.submission_ids == (item["submission_id"],)
    assert started.turn_id == item["turn_id"]
    assert [(method, url.removeprefix("https://example.test")) for (
        _timeout, method, url, _values
    ) in factory.calls] == [
        tuple(fixture["operations"]["list"]),
        ("PATCH", f"/queue/{item['submission_id']}"),
        ("DELETE", f"/queue/{item['submission_id']}"),
        tuple(fixture["operations"]["reorder"]),
        tuple(fixture["operations"]["start"]),
    ]


@pytest.mark.anyio
async def test_queue_error_preserves_conflict_code_and_retryability(
    monkeypatch,
) -> None:
    factory = _ClientFactory([_response(
        {"detail": {"code": "session_busy", "message": "session is active"}},
        status_code=409,
    )])
    monkeypatch.setattr(durable_queue.httpx, "AsyncClient", factory)

    with pytest.raises(durable_queue.DurableQueueRequestError) as raised:
        await durable_queue.start_queue_submission(
            cid="cid_queue",
            sid="sid_queue",
            submission_id="submission_queue_0001",
            request_id="request_queue_start",
        )

    assert raised.value.status_code == 409
    assert raised.value.code == "session_busy"
    assert raised.value.retryable is False


def test_queue_response_rejects_undeclared_fields(fixtures_root: Path) -> None:
    """客户端与服务端 extra=forbid 契约保持一致。"""
    fixture = _fixture(fixtures_root)
    body = {
        "ok": True,
        "data": {
            "cid": "cid_queue",
            "sid": "sid_queue",
            "queue_version": 1,
            "items": [{**fixture["item"], "unexpected": True}],
        },
    }

    with pytest.raises(DurableQueueResponseError, match="fields"):
        durable_queue.parse_queue_snapshot(
            body,
            expected_cid="cid_queue",
            expected_sid="sid_queue",
        )


@pytest.mark.anyio
async def test_protocol_adapter_returns_frozen_queue_projection(monkeypatch) -> None:
    wire_item = DurableQueueItem(
        queue_seq=1,
        cid="cid_queue",
        sid="sid_queue",
        submission_id="submission_queue_0001",
        client_message_id="message_queue_0001",
        turn_id="turn_queue_0001",
        position=1,
        status="queued",
        input=QueueInputSnapshot(
            text="queued input",
            attachments=(),
            extras={},
        ),
        created_at=1.0,
        updated_at=1.0,
        started_at=None,
        deleted_at=None,
    )
    monkeypatch.setattr(
        adapter_module,
        "_add_queue_submission",
        lambda **_values: _async_result(QueueMutationResponse(
            request_id="request_queue_0001",
            queue_version=1,
            item=wire_item,
        )),
    )
    client = MindChatProtocolClient()

    assert isinstance(client, DurableQueueClient)
    receipt = await client.add_queue_submission(
        _model_request(),
        submission_id=wire_item.submission_id,
        client_message_id=wire_item.client_message_id,
        request_id="request_queue_0001",
    )

    assert isinstance(receipt, DurableQueueMutationReceipt)
    assert isinstance(receipt.item.input.extras, MappingProxyType)
    with pytest.raises(TypeError):
        receipt.item.input.extras["mutated"] = True


@pytest.mark.anyio
async def test_protocol_adapter_preserves_queue_server_error(monkeypatch) -> None:
    async def fail(**_values):
        raise durable_queue.DurableQueueRequestError(
            "session is active",
            status_code=409,
            code="session_busy",
        )

    monkeypatch.setattr(adapter_module, "_start_queue_submission", fail)

    with pytest.raises(ProtocolCommandError) as raised:
        await MindChatProtocolClient().start_queue_submission(
            cid="cid_queue",
            sid="sid_queue",
            submission_id="submission_queue_0001",
            request_id="request_queue_start",
        )

    assert raised.value.code == "session_busy"
    assert raised.value.retryable is False
    assert raised.value.details == {
        "status_code": 409,
        "server_code": "session_busy",
    }


async def _async_result(value):
    """把固定值包装为测试使用的 awaitable。"""
    return value
