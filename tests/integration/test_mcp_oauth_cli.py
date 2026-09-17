# -*- coding: utf-8 -*-

import json

import anyio
import httpx
import pytest
from pathlib import Path
from unittest.mock import patch

from agent.application.mcp.oauth import McpOAuthService
from agent.domain.mcp_oauth import McpOAuthTarget
from frontends.cli.entry import run
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.oauth_adapter import McpOAuthAdapter
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from tests.fakes.mcp_credentials import MemoryVault
from tests.infrastructure.mcp.oauth_browser import (
    OAuthBrowser,
    assert_callback_closed,
)
from tests.infrastructure.mcp.oauth_fixture import (
    OAuthService,
    RESOURCE_URL,
)


class LoginFixture:
    def __init__(self, root: Path) -> None:
        self.config_path = root / "config" / "config.toml"
        self.name = " sentry raw "
        ConfigStore(self.config_path).update({("mcp_servers", self.name): {"url": RESOURCE_URL}})
        self.vault = MemoryVault()
        self.store = SystemMcpCredentialStore(config_root=self.config_path.parent, state_root=root / "state", vault=self.vault, clock=lambda: 1000)
        self.remote = OAuthService()
        self.browser = OAuthBrowser(self.remote)
        self.application = McpOAuthService(self.store, McpOAuthAdapter(
            open_browser=self.browser.open, client_factory=self.remote.client, clock=lambda: 1000,
        ))

    def factory(self, config_root: Path) -> McpOAuthService:
        assert config_root == self.config_path.parent
        return self.application

    def run(self, *arguments: str) -> int:
        with patch("frontends.cli.mcp_registry.application_config_path", return_value=self.config_path):
            return run(arguments=["mcp", *arguments], mcp_oauth_factory=self.factory)


def test_production_cli_login_get_list_logout_and_secret_redaction(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = LoginFixture(tmp_path)
    original = fixture.config_path.read_bytes()
    assert fixture.run("login", fixture.name, "--scopes", "project:read") == 0
    output = capsys.readouterr().out
    assert "Successfully logged in" in output and "code_challenge=" in output
    assert fixture.remote.token_requests[0].grant_type == "authorization_code"
    anyio.run(assert_callback_closed, fixture.browser.callback_url)
    assert fixture.run("get", fixture.name, "--json") == 0
    output += (get_output := capsys.readouterr().out)
    assert json.loads(get_output)["authorization"] == {
        "state": "oauth", "credentials": "stored", "expires_at": 4600, "error": None,
        "verification": "unverified", "generation": 1,
    }
    before = len(fixture.remote.requests)
    assert fixture.run("list", "--json") == 0
    output += (list_output := capsys.readouterr().out)
    assert json.loads(list_output)["servers"][0]["authorization"]["state"] == "oauth"
    assert len(fixture.remote.requests) == before
    assert "access-1" not in output and "refresh-1" not in output and "code-1" not in output
    assert fixture.run("logout", fixture.name) == 0
    assert fixture.run("logout", fixture.name) == 0
    assert "Remote authorization was not revoked" in capsys.readouterr().out
    assert not fixture.vault.values
    assert fixture.config_path.read_bytes() == original


@pytest.mark.parametrize("failure", ["denied", "wrong_state", "wrong_issuer", "missing_code", "timeout", "registration", "token", "storage", "issuer", "cancel"])
def test_cli_failure_codes_preserve_previous_credentials_and_close_resources(tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str) -> None:
    fixture = LoginFixture(tmp_path)
    assert fixture.run("login", fixture.name) == 0
    capsys.readouterr()
    target = McpOAuthTarget(fixture.name, RESOURCE_URL)
    before = anyio.run(fixture.store.read, target)
    if failure == "registration":
        fixture.remote.registration_status = 400
    elif failure == "token":
        fixture.remote.token_status = 400
    elif failure == "storage":
        fixture.vault.fail_write = fixture.vault.writes + 1
    elif failure == "issuer":
        fixture.remote.metadata_issuer = "https://wrong.example/issuer"
    else:
        fixture.browser.mode = failure
    assert fixture.run("login", fixture.name, "--timeout-sec", "0.4") == (130 if failure == "cancel" else 1)
    output = capsys.readouterr()
    assert "Successfully logged in" not in output.out
    assert "access-" not in output.out + output.err
    assert "refresh-" not in output.out + output.err
    assert anyio.run(fixture.store.read, target) == before
    anyio.run(assert_callback_closed, fixture.browser.callback_url)


def test_cli_browser_failure_keeps_manual_authorization_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = LoginFixture(tmp_path)
    fixture.browser.result = False
    assert fixture.run("login", fixture.name) == 0
    output = capsys.readouterr().out
    assert "Open the URL above" in output and "Successfully logged in" in output


def test_cli_unknown_server_and_explicit_auth_do_not_create_credentials(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = LoginFixture(tmp_path)
    assert fixture.run("login", "sentry raw") == 1
    ConfigStore(fixture.config_path).update({("mcp_servers", fixture.name, "bearer_token_env_var"): "UNRESOLVED_TOKEN"})
    assert fixture.run("login", fixture.name) == 1
    assert not fixture.vault.values and not fixture.remote.requests
    assert fixture.run("get", fixture.name, "--json") == 0
    assert '"state": "bearer"' in capsys.readouterr().out


def test_cli_service_without_oauth_fails_without_browser_or_config_changes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = LoginFixture(tmp_path)
    original = fixture.config_path.read_bytes()

    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if str(request.url) == RESOURCE_URL else 404, json={})

    fixture.application = McpOAuthService(fixture.store, McpOAuthAdapter(
        open_browser=fixture.browser.open,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ))
    assert fixture.run("login", fixture.name) == 1
    output = capsys.readouterr()
    assert "OAuth" in output.out + output.err and "Successfully" not in output.out
    assert fixture.browser.authorization_url is None
    assert fixture.config_path.read_bytes() == original and not fixture.vault.values


def test_cli_local_credential_status_does_not_claim_remote_validity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixture = LoginFixture(tmp_path)
    assert fixture.run("get", fixture.name, "--json") == 0
    assert json.loads(capsys.readouterr().out)["authorization"]["credentials"] == "missing"
    fixture.remote.expires_in = 0
    assert fixture.run("login", fixture.name) == 0
    capsys.readouterr()
    assert fixture.run("get", fixture.name, "--json") == 0
    assert json.loads(capsys.readouterr().out)["authorization"]["credentials"] == "expired"
    fixture.vault.fail_read = True
    assert fixture.run("get", fixture.name, "--json") == 0
    status = json.loads(capsys.readouterr().out)["authorization"]
    assert status["state"] == "unavailable" and status["error"] == "storage_unavailable"
