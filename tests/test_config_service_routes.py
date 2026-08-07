# -*- coding: utf-8 -*-

from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.provider_config import (
    SUPPORTED_PROVIDER_OPTIONS,
    SUPPORTED_ROUTE_NAMES,
    default_route_for_provider,
)
from server.app import create_app
from server.page import render_page
from server.storage import save_pref


def test_config_service_exposes_only_active_pages(tmp_path) -> None:
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    app = create_app(session)

    paths = {route.path for route in app.routes}

    assert {"/", "/pref", "/agent"} <= paths
    assert "/code" not in paths


def test_anthropic_provider_is_available_in_config_and_page() -> None:
    providers = {
        option["value"]: option["label"]
        for option in SUPPORTED_PROVIDER_OPTIONS
    }
    page = render_page("pref.html").body.decode("utf-8")

    assert providers["anthropic"] == "Anthropic"
    assert "messages" in SUPPORTED_ROUTE_NAMES
    assert default_route_for_provider("anthropic") == "messages"
    assert '<option value="anthropic">Anthropic</option>' in page
    assert '<option value="messages">Messages API</option>' in page


def test_anthropic_pref_defaults_to_messages_route(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(store)

    pref = save_pref(session, {
        "primary": {
            "provider": "anthropic",
            "model": "claude-test",
            "apikey": "sk-ant-test",
            "enabled": True,
        },
    })

    assert pref["primary"]["route"] == "messages"
    assert store.read_raw()["model_providers"]["anthropic"]["route"] == (
        "messages"
    )
