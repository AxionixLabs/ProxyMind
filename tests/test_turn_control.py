# -*- coding: utf-8 -*-

import json

import httpx
import pytest

from mind_nova.requests import turn_control
from mind_nova.turn_inputs import TurnInput


@pytest.mark.anyio
async def test_turn_status_returns_validated_authoritative_snapshot(
    monkeypatch,
) -> None:
    captured = {}
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_001",
            "run_id": "run_001",
            "status": "running",
            "terminal": False,
            "attempt": 2,
            "version": 3,
            "last_event_seq": 17,
            "created_at": 10.0,
            "updated_at": 12.5,
            "error": "",
        },
        request=httpx.Request("GET", "https://example.com/turn/status"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(
        turn_control.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(
        turn_control.Channel,
        "make_headers",
        lambda: {"authorization": "test"},
    )

    snapshot = await turn_control.get_turn_status(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        timeout=4.0,
    )

    assert snapshot.status == "running"
    assert snapshot.terminal is False
    assert snapshot.last_event_seq == 17
    assert captured == {
        "timeout": 4.0,
        "url": "https://example.com/turn/status",
        "params": {
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_001",
        },
        "headers": {"authorization": "test"},
    }


@pytest.mark.anyio
async def test_turn_status_preserves_http_status_code(monkeypatch) -> None:
    response = httpx.Response(
        404,
        request=httpx.Request("GET", "https://example.com/turn/status"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)

    with pytest.raises(turn_control.TurnStatusRequestError) as raised:
        await turn_control.get_turn_status(
            cid="cid_1",
            sid="sid_1",
            turn_id="turn_001",
        )

    assert raised.value.status_code == 404


@pytest.mark.anyio
async def test_turn_status_rejects_inconsistent_terminal_flag(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_001",
            "run_id": "run_001",
            "status": "failed",
            "terminal": False,
            "attempt": 1,
            "version": 1,
            "last_event_seq": 3,
            "created_at": 10.0,
            "updated_at": 12.5,
            "error": "provider failed",
        },
        request=httpx.Request("GET", "https://example.com/turn/status"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)

    with pytest.raises(
        turn_control.TurnStatusRequestError,
        match="invalid response",
    ):
        await turn_control.get_turn_status(
            cid="cid_1",
            sid="sid_1",
            turn_id="turn_001",
        )


@pytest.mark.anyio
async def test_steer_request_uses_session_query_and_stable_message_id(
    monkeypatch,
) -> None:
    captured = {}
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "request_id": "steer_request_1",
            "status": "accepted",
            "turn_id": "turn_001",
            "client_message_id": "message_1",
        },
        request=httpx.Request("POST", "https://example.com/turn/steer"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(
        turn_control.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(
        turn_control.Channel,
        "make_headers",
        lambda: {"authorization": "test"},
    )

    result = await turn_control.steer_turn(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        turn_input=TurnInput(
            client_message_id="message_1",
            text="change direction",
            attachments=({"kind": "image"},),
            extras={"source": "tui"},
        ),
        request_id="steer_request_1",
        timeout=4.0,
    )

    assert result.status == "accepted"
    assert result.request_id == "steer_request_1"
    assert captured["url"] == "https://example.com/turn/steer"
    assert captured["params"] == {"cid": "cid_1", "sid": "sid_1"}
    assert captured["headers"] == {"authorization": "test"}
    assert captured["timeout"] == 4.0
    assert json.loads(json.dumps(captured["json"])) == {
        "request_id": "steer_request_1",
        "turn_id": "turn_001",
        "client_message_id": "message_1",
        "input": {
            "text": "change direction",
            "attachments": [{"kind": "image"}],
            "extras": {"source": "tui"},
        },
    }


@pytest.mark.anyio
async def test_reconcile_request_validates_complete_classification(
    monkeypatch,
) -> None:
    captured = {}
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "turn_id": "turn_001",
            "turn_status": "settled",
            "committed_ids": ["message_1"],
            "pending_ids": [],
            "retry_ids": ["message_2"],
            "unknown_ids": [],
        },
        request=httpx.Request("POST", "https://example.com/turn/reconcile"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(
        turn_control.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(
        turn_control.Channel,
        "make_headers",
        lambda: {"authorization": "test"},
    )

    result = await turn_control.reconcile_turn_inputs(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        client_message_ids=["message_1", "message_2", "message_1"],
        timeout=2.0,
    )

    assert result.committed_ids == ("message_1",)
    assert result.retry_ids == ("message_2",)
    assert captured["url"] == "https://example.com/turn/reconcile"
    assert captured["params"] == {"cid": "cid_1", "sid": "sid_1"}
    assert captured["headers"] == {"authorization": "test"}
    assert captured["json"] == {
        "turn_id": "turn_001",
        "client_message_ids": ["message_1", "message_2"],
    }


@pytest.mark.anyio
async def test_interrupt_rejects_response_for_another_turn(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "request_id": "interrupt_request_1",
            "status": "accepted",
            "turn_id": "turn_other",
            "client_message_id": None,
        },
        request=httpx.Request("POST", "https://example.com/turn/interrupt"),
    )

    class ClientStub:
        def __init__(self, *, timeout) -> None:
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(turn_control.httpx, "AsyncClient", ClientStub)

    with pytest.raises(
        turn_control.TurnControlRequestError,
        match="does not match",
    ):
        await turn_control.interrupt_turn(
            cid="cid_1",
            sid="sid_1",
            turn_id="turn_001",
            request_id="interrupt_request_1",
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("cid", {"cid": "", "sid": "sid_1", "turn_id": "turn_001"}),
        ("sid", {"cid": "cid_1", "sid": "", "turn_id": "turn_001"}),
        ("turn_id", {"cid": "cid_1", "sid": "sid_1", "turn_id": ""}),
    ],
)
async def test_turn_control_rejects_incomplete_session_coordinates(
    field,
    values,
) -> None:
    with pytest.raises(
        turn_control.TurnControlRequestError,
        match=field,
    ):
        await turn_control.steer_turn(
            **values,
            turn_input=TurnInput(
                client_message_id="message_1",
                text="change direction",
            ),
        )


@pytest.mark.anyio
async def test_turn_control_rejects_invalid_turn_id() -> None:
    with pytest.raises(
        turn_control.TurnControlRequestError,
        match="8-128 ASCII",
    ):
        await turn_control.interrupt_turn(
            cid="cid_1",
            sid="sid_1",
            turn_id="short",
        )


@pytest.mark.anyio
async def test_turn_control_rejects_invalid_request_id() -> None:
    with pytest.raises(
        turn_control.TurnControlRequestError,
        match="8-160 ASCII",
    ):
        await turn_control.interrupt_turn(
            cid="cid_1",
            sid="sid_1",
            turn_id="turn_001",
            request_id="invalid request id",
        )
