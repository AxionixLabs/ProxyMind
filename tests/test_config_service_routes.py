# -*- coding: utf-8 -*-

import httpx
import pytest

from metadata import const
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.config.preferences import config_to_preferences
from protocol.client.payload import request_llm_conf
from infrastructure.config.providers import (
    SUPPORTED_PROVIDER_OPTIONS,
    SUPPORTED_ROUTE_NAMES,
    default_route_for_kind,
)
from server.app import create_app
from server.page import render_page
from server.storage import (
    create_provider,
    load_pref,
    set_active_provider,
    update_provider,
)


def test_config_service_exposes_only_active_pages(tmp_path) -> None:
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    app = create_app(session)

    paths = {route.path for route in app.routes}

    assert {"/", "/pref", "/agent"} <= paths
    assert {
        "/api/pref/providers",
        "/api/pref/providers/{provider_id}",
        "/api/pref/active-provider",
    } <= paths
    assert "/code" not in paths


def test_anthropic_provider_is_available_in_config_and_page() -> None:
    providers = {
        option["value"]: option["label"]
        for option in SUPPORTED_PROVIDER_OPTIONS
    }
    page = render_page("pref.html").body.decode("utf-8")

    assert providers["anthropic"] == "Anthropic"
    assert "messages" in SUPPORTED_ROUTE_NAMES
    assert default_route_for_kind("anthropic") == "messages"
    assert 'id="provider-dialog"' in page
    assert 'class="provider-grid"' in page
    assert 'id="provider-search"' in page
    assert 'id="provider-kind-filter"' in page
    assert 'id="provider-status-filter"' in page
    assert 'id="confirm-dialog"' in page
    assert "provider-add-card" not in page
    assert "static/pref.css" not in page
    assert "static/pref.js" not in page
    assert "requestConfirmation" in page
    assert "if (!confirm(" not in page


@pytest.mark.anyio
async def test_configuration_version_uses_product_metadata(tmp_path) -> None:
    app = create_app(ConfigSession(ConfigStore(tmp_path / "config.toml")))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/version")

    assert response.status_code == 200
    assert response.json()["version"] == const.APP_VERSION


def test_provider_profiles_are_independent_and_secrets_are_redacted(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(store)

    create_provider(session, {
        "id": "claude-main",
        "name": "claude-main",
        "kind": "anthropic",
        "model": "claude-test",
        "api_key": "sk-ant-test",
    })
    pref = set_active_provider(session, "claude-main")

    claude = next(item for item in pref["providers"] if item["id"] == "claude-main")
    assert pref["active_provider"] == "claude-main"
    assert claude["route"] == "messages"
    assert claude["api_key_configured"] is True
    assert "api_key" not in claude
    assert store.read_raw()["model_providers"]["claude-main"]["route"] == (
        "messages"
    )
    request = request_llm_conf(config_to_preferences(session.load()))["primary"]
    assert request["provider"] == "anthropic"
    assert set(request) == {
        "provider",
        "route",
        "model",
        "apikey",
        "base_url",
        "reasoning_effort",
    }

    update_provider(session, "claude-main", {"model": "claude-next"})
    raw = store.read_raw()["model_providers"]
    assert raw["claude-main"]["model"] == "claude-next"
    assert raw["claude-main"]["api_key"] == "sk-ant-test"
    assert "openai-main" in raw


def test_incomplete_provider_cannot_be_activated(tmp_path) -> None:
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    create_provider(session, {
        "id": "local",
        "name": "local",
        "kind": "openai_compatible",
    })

    with pytest.raises(
        ValueError,
        match="provider is incomplete: local",
    ):
        set_active_provider(session, "local")

    assert load_pref(session)["active_provider"] == "openai-main"


@pytest.mark.anyio
async def test_provider_api_covers_secret_and_lifecycle_semantics(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    app = create_app(ConfigSession(store))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        created = await client.post("/api/pref/providers", json={
            "id": "local-main",
            "name": "Local Main",
            "kind": "openai_compatible",
            "model": "local-test",
            "api_key": "secret-test",
        })
        assert created.status_code == 200
        local = next(
            item for item in created.json()["data"]["providers"]
            if item["id"] == "local-main"
        )
        assert local["api_key_configured"] is True
        assert "api_key" not in local

        preserved = await client.patch(
            "/api/pref/providers/local-main",
            json={"model": "local-next", "api_key": ""},
        )
        assert preserved.status_code == 200
        assert store.read_raw()["model_providers"]["local-main"]["api_key"] == (
            "secret-test"
        )

        blocked = await client.delete("/api/pref/providers/openai-main")
        assert blocked.status_code == 400
        assert "select another provider" in blocked.json()["detail"]

        activated = await client.put(
            "/api/pref/active-provider",
            json={"provider": "local-main"},
        )
        assert activated.status_code == 200
        assert activated.json()["data"]["active_provider"] == "local-main"

        cleared = await client.patch(
            "/api/pref/providers/local-main",
            json={"clear_api_key": True},
        )
        assert cleared.status_code == 200
        local = next(
            item for item in cleared.json()["data"]["providers"]
            if item["id"] == "local-main"
        )
        assert local["api_key_configured"] is False

        deleted = await client.delete("/api/pref/providers/openai-main")
        assert deleted.status_code == 200
        assert [
            item["id"] for item in deleted.json()["data"]["providers"]
        ] == ["local-main"]


@pytest.mark.anyio
async def test_provider_api_rejects_invalid_kind_route_pair(tmp_path) -> None:
    app = create_app(ConfigSession(ConfigStore(tmp_path / "config.toml")))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post("/api/pref/providers", json={
            "id": "claude-main",
            "name": "Claude",
            "kind": "anthropic",
            "route": "responses",
            "model": "claude-test",
        })

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "route is not supported by anthropic: responses"
    )
