# -*- coding: utf-8 -*-

import json

import httpx
import pytest

from mind_nova.requests import turn_control
from mind_nova.turn_inputs import TurnInput


@pytest.mark.anyio
async def test_steer_request_uses_session_query_and_stable_message_id(
    monkeypatch,
) -> None:
    captured = {}
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "status": "accepted",
            "turn_id": "turn_1",
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
        turn_id="turn_1",
        turn_input=TurnInput(
            client_message_id="message_1",
            text="change direction",
            attachments=({"kind": "image"},),
            extras={"source": "tui"},
        ),
        timeout=4.0,
    )

    assert result.status == "accepted"
    assert captured["url"] == "https://example.com/turn/steer"
    assert captured["params"] == {"cid": "cid_1", "sid": "sid_1"}
    assert captured["headers"] == {"authorization": "test"}
    assert captured["timeout"] == 4.0
    assert json.loads(json.dumps(captured["json"])) == {
        "turn_id": "turn_1",
        "client_message_id": "message_1",
        "input": {
            "text": "change direction",
            "attachments": [{"kind": "image"}],
            "extras": {"source": "tui"},
        },
    }


@pytest.mark.anyio
async def test_follow_up_request_uses_the_same_stable_input_contract(
    monkeypatch,
) -> None:
    captured = {}
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "status": "accepted",
            "turn_id": "turn_1",
            "client_message_id": "message_1",
        },
        request=httpx.Request("POST", "https://example.com/turn/follow-up"),
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

    turn_input = TurnInput(client_message_id="message_1", text="next task")
    result = await turn_control.follow_up_turn(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
        turn_input=turn_input,
    )

    assert result.status == "accepted"
    assert captured["url"] == "https://example.com/turn/follow-up"
    assert captured["params"] == {"cid": "cid_1", "sid": "sid_1"}
    assert captured["json"] == {
        "turn_id": "turn_1",
        "client_message_id": "message_1",
        "input": {
            "text": "next task",
            "attachments": [],
            "extras": {},
        },
    }


@pytest.mark.anyio
async def test_interrupt_rejects_response_for_another_turn(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "status": "accepted",
            "turn_id": "turn_other",
            "client_message_id": "interrupt",
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
            turn_id="turn_1",
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("cid", {"cid": "", "sid": "sid_1", "turn_id": "turn_1"}),
        ("sid", {"cid": "cid_1", "sid": "", "turn_id": "turn_1"}),
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
