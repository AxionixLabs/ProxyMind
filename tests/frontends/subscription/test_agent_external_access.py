# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from frontends.subscription.external_access import publish_external_access
from frontends.subscription.opening import normalize_open_payload


def test_agent_open_payload_preserves_server_web_url() -> None:
    web_url = "https://example.test/mind"
    client = SimpleNamespace(
        unwrap_data=lambda payload: payload["data"],
    )

    normalized = normalize_open_payload(client, {
        "data": {
            "session_id": "session-1",
            "ws_token": "ws-token-1",
            "credential": {"token": "credential-1"},
            "web_url": web_url,
        },
    })

    assert normalized[-1] == web_url


@pytest.mark.anyio
async def test_agent_access_sync_bypasses_proxy_environment(monkeypatch) -> None:
    response = SimpleNamespace(raise_for_status=Mock())
    put = AsyncMock(return_value=response)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.put = put
    factory = Mock(return_value=client)
    monkeypatch.setattr(
        "frontends.subscription.external_access.httpx.AsyncClient",
        factory,
    )
    runtime = SimpleNamespace(
        session_id="session-1",
        credential="credential-1",
        web_url="https://example.test/mind",
    )

    await publish_external_access(
        runtime,
        configuration_service_url=lambda: "http://127.0.0.1:37300",
    )

    assert factory.call_args.kwargs["trust_env"] is False
    put.assert_awaited_once_with(
        "http://127.0.0.1:37300/api/agent",
        headers={"Content-Type": "application/json"},
        json={
            "session_id": "session-1",
            "credential": "credential-1",
            "web_url": runtime.web_url,
            "state": "connecting",
        },
    )
    response.raise_for_status.assert_called_once_with()


@pytest.mark.anyio
async def test_key_is_published_even_when_web_address_is_unavailable(monkeypatch):
    response = SimpleNamespace(raise_for_status=Mock())
    http = MagicMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    http.put = AsyncMock(return_value=response)
    monkeypatch.setattr("frontends.subscription.external_access.httpx.AsyncClient",Mock(return_value=http))
    runtime = SimpleNamespace(session_id="session",credential="device-key",web_url=None)
    await publish_external_access(runtime,configuration_service_url=lambda:"http://127.0.0.1:37300",state="online")
    body = http.put.await_args.kwargs["json"]
    assert body["credential"] == "device-key" and body["web_url"] is None and body["state"] == "online"


def test_local_connection_page_serves_state_and_icon_without_caching_keys():
    from fastapi.testclient import TestClient
    from infrastructure.services.configuration_host.app import create_app
    with TestClient(create_app(SimpleNamespace())) as client:
        assert client.get("/api/agent").json()["data"]["state"] == "idle"
        response = client.put("/api/agent",json={"credential":"device-key","web_url":"https://example.test/mind","state":"online"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = client.get("/api/agent").json()["data"]
        assert body["has_credential"] and body["updated_at_ms"] > 0
        assert client.put("/api/agent",json={"web_url":"javascript:alert(1)"}).status_code == 422
        page = client.get("/agent")
        assert "/static/favicon.svg" in page.text and "/static/agent.js" in page.text
        assert client.get("/static/favicon.svg").status_code == 200
