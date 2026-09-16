# -*- coding: utf-8 -*-

import pytest
from pathlib import Path

from pydantic import JsonValue

from agent.domain.mcp_oauth import McpOAuthError
from infrastructure.config.mcp_oauth import (
    McpOAuthServerSettings,
    validate_mcp_oauth_options,
)
from infrastructure.config.schema import (
    config_override,
    normalize_config,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore


@pytest.mark.parametrize("options", [
    {"client_secret": "secret"}, {"callback_port": True}, {"callback_port": 0},
    {"callback_port": 65536}, {"callback_port": None}, {"login_timeout_sec": True},
    {"login_timeout_sec": float("inf")}, {"login_timeout_sec": -1},
    {"scopes": None}, {"scopes": "read"}, {"scopes": ["two scopes"]},
    {"client_id": ""}, {"client_id": "public-without-port"},
    {"client_id": "public", "callback_port": 12608, "client_metadata_url": "https://client.test/client.json"},
    {"client_metadata_url": "http://client.test/client.json"},
    {"client_metadata_url": "https://client.test/"},
    {"client_metadata_url": "https://user:secret@client.test/client.json"},
])
def test_invalid_oauth_options_are_rejected_without_input_leaks(options: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError) as error:
        validate_mcp_oauth_options(options, effective=True)
    assert "secret" not in str(error.value)


def test_layered_client_identity_is_validated_after_merge(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({("mcp_servers", " raw "): {"url": "https://example.test/mcp", "oauth": {"callback_port": 12608}}})
    session = ConfigSession(store, (config_override(("mcp_servers", " raw ", "oauth", "client_id"), "public"),), workspace=tmp_path)
    server = session.resolve().config["mcp_servers"][" raw "]
    settings = McpOAuthServerSettings.model_validate(server)
    request = settings.login_request(" raw ", scopes=None, timeout_sec=None, environment={})
    assert request.target.config_key == " raw " and request.callback_port == 12608
    assert request.registration.kind == "registered"


def test_scope_and_timeout_overrides_preserve_omitted_versus_empty() -> None:
    settings = McpOAuthServerSettings.model_validate({"url": "https://example.test/mcp", "oauth": {"scopes": ["read", "read"], "login_timeout_sec": 30}})
    configured = settings.login_request("server", scopes=None, timeout_sec=None, environment={})
    explicit = settings.login_request("server", scopes=(), timeout_sec=10, environment={})
    assert configured.scopes == ("read",) and configured.timeout_sec == 30
    assert explicit.scopes == () and explicit.timeout_sec == 10
    assert McpOAuthServerSettings(url="https://example.test/mcp").login_request("server", scopes=None, timeout_sec=None, environment={}).scopes is None


@pytest.mark.parametrize("auth", [
    {"bearer_token_env_var": "MISSING_TOKEN"},
    {"http_headers": {"aUtHoRiZaTiOn": "secret"}},
    {"env_http_headers": {"Authorization": "MISSING_TOKEN"}},
])
def test_explicit_auth_is_a_conflict_even_without_resolved_environment(auth: dict[str, JsonValue]) -> None:
    settings = McpOAuthServerSettings.model_validate({"url": "https://example.test/mcp", **auth})
    assert not settings.applicable
    with pytest.raises(McpOAuthError) as error:
        settings.login_request("server", scopes=None, timeout_sec=None, environment={})
    assert error.value.code == "configuration_conflict"


@pytest.mark.parametrize("server", [{"command": "tool"}, {"url": "https://example.test/sse?key=value"}])
def test_unsupported_transport_has_no_oauth_target(server: dict[str, JsonValue]) -> None:
    settings = McpOAuthServerSettings.model_validate(server)
    assert not settings.applicable
    with pytest.raises(McpOAuthError) as error:
        settings.target("server")
    assert error.value.code == "unsupported_transport"


def test_normalize_config_accepts_oauth_and_rejects_unknown_nested_fields() -> None:
    normalized = normalize_config({"mcp_servers": {"server": {"url": "https://example.test/mcp", "oauth": {"scopes": []}}}})
    assert normalized["mcp_servers"]["server"]["oauth"]["scopes"] == []
    with pytest.raises(ValueError):
        normalize_config({"mcp_servers": {"server": {"url": "https://example.test/mcp", "oauth": {"typo": True}}}})
