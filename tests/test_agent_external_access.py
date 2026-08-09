# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from mind_app.subscription.external_access import publish_external_access
from mind_app.subscription.opening import normalize_open_payload


def test_agent_open_payload_preserves_server_mind_call_example() -> None:
    example = {
        "method": "POST",
        "url": "https://example.test/mind",
        "headers": {"Authorization": "Bearer credential-1"},
        "body": {"message": "inspect workspace"},
    }
    client = SimpleNamespace(
        unwrap_data=lambda payload: payload["data"],
    )

    normalized = normalize_open_payload(client, {
        "data": {
            "session_id": "session-1",
            "ws_token": "ws-token-1",
            "credential": {"token": "credential-1"},
            "examples": {"mind_call": example},
        },
    })

    assert normalized[-1] == example


@pytest.mark.anyio
async def test_agent_example_sync_bypasses_proxy_environment(monkeypatch) -> None:
    response = SimpleNamespace(raise_for_status=Mock())
    put = AsyncMock(return_value=response)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.put = put
    factory = Mock(return_value=client)
    monkeypatch.setattr(
        "mind_app.subscription.external_access.httpx.AsyncClient",
        factory,
    )
    monkeypatch.setattr(
        "mind_app.subscription.external_access.config_service_base_url",
        lambda: "http://127.0.0.1:37300",
    )
    runtime = SimpleNamespace(
        session_id="session-1",
        credential="credential-1",
        mind_call_example={
            "method": "POST",
            "url": "https://example.test/mind",
            "headers": {"Content-Type": "application/json"},
            "body": {"message": "inspect workspace"},
        },
    )

    await publish_external_access(runtime)

    assert factory.call_args.kwargs["trust_env"] is False
    put.assert_awaited_once_with(
        "http://127.0.0.1:37300/api/agent",
        headers={"Content-Type": "application/json"},
        json={
            "session_id": "session-1",
            "credential": "credential-1",
            "mind_call": runtime.mind_call_example,
        },
    )
    response.raise_for_status.assert_called_once_with()
