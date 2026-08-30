# -*- coding: utf-8 -*-

from unittest.mock import (
    AsyncMock,
    call,
)

import httpx
import pytest

from protocol.transport import reliable


class _ClientFactory(object):
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, *, timeout):
        factory = self

        class ClientStub(object):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url, **kwargs):
                factory.calls.append((timeout, url, kwargs))
                outcome = factory.outcomes.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        return ClientStub()


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"ok": status_code < 400},
        request=httpx.Request("POST", "https://example.test/command"),
    )


@pytest.mark.anyio
async def test_reliable_post_reuses_payload_across_transient_failures(
    monkeypatch,
) -> None:
    payload = {"request_id": "request_1", "value": 1}
    factory = _ClientFactory([
        httpx.ConnectError("offline"),
        _response(503),
        _response(200),
    ])
    sleep = AsyncMock()
    monkeypatch.setattr(reliable.asyncio, "sleep", sleep)

    response = await reliable.post_json_reliably(
        "https://example.test/command",
        headers={"authorization": "test"},
        payload=payload,
        timeout=3.0,
        client_factory=factory,
        retry_delays=(0.0, 0.2, 0.5),
    )

    assert response.status_code == 200
    assert len(factory.calls) == 3
    assert all(call[2]["json"] is payload for call in factory.calls)
    sleep.assert_has_awaits([call(0.2), call(0.5)])


@pytest.mark.anyio
async def test_reliable_post_does_not_retry_permanent_client_error(
    monkeypatch,
) -> None:
    factory = _ClientFactory([_response(422), _response(200)])
    sleep = AsyncMock()
    monkeypatch.setattr(reliable.asyncio, "sleep", sleep)

    response = await reliable.post_json_reliably(
        "https://example.test/command",
        headers={},
        payload={"request_id": "request_1"},
        timeout=3.0,
        client_factory=factory,
        retry_delays=(0.0, 0.2),
    )

    assert response.status_code == 422
    assert len(factory.calls) == 1
    sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_malformed_sse_data_raises_transport_decode_error(
    monkeypatch,
) -> None:
    class ResponseStub(object):
        status_code = 200
        headers = {}
        request = httpx.Request("POST", "https://example.test/stream")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"type":"ping"}'
            yield "data: {broken"

    class ClientStub(object):
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return ResponseStub()

    monkeypatch.setattr(reliable.httpx, "AsyncClient", ClientStub)
    from protocol.transport import streaming as streaming_module
    monkeypatch.setattr(streaming_module.httpx, "AsyncClient", ClientStub)

    stream = streaming_module.streaming(
        "https://example.test/stream",
        {},
        {},
    )
    assert await anext(stream) == {"type": "ping"}
    with pytest.raises(streaming_module.StreamDecodeError):
        await anext(stream)
