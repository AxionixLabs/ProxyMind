# -*- coding: utf-8 -*-

import json
import sys
from dataclasses import (
    asdict,
    replace,
)
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

import pytest
from mcp.types import CallToolResult

from agent.domain.mcp_oauth import (
    McpOAuthClientInfo,
    McpOAuthCredentialRecord,
    McpOAuthCredentialSnapshot,
    McpOAuthErrorCode,
    McpOAuthTarget,
    McpOAuthToken,
)
from agent.ports.mcp_runtime import McpServiceSnapshot
from agent.domain.mcp_authorization import McpAuthorizationStatus
from infrastructure.config.store import ConfigStore
from tests.manual.mcp_oauth_sentry import (
    LiveCheckError,
    LiveReport,
    credential_evidence,
    main,
    refresh_committed,
    run_live,
)


def snapshot() -> McpOAuthCredentialSnapshot:
    return McpOAuthCredentialSnapshot(
        McpOAuthTarget("acceptance", "https://service.example/mcp"),
        "https://issuer.example", "https://service.example/mcp", "https://issuer.example/token",
        McpOAuthClientInfo("private-client-identity", ("http://127.0.0.1:12345/callback",), "dynamic"),
        1, McpOAuthToken("private-access", "private-refresh", 1000, ("read",)),
    )


@pytest.mark.parametrize("case", [
    "valid", "not_expired", "old_token", "expired_replacement", "missing_expiry",
    "missing_refresh", "login_instead_of_refresh", "changed_client", "missing_record",
])
def test_refresh_proof_requires_expiry_and_committed_rotation(case: str) -> None:
    old = snapshot()
    new = replace(old, generation=3, token=McpOAuthToken("new-access", "new-refresh", 2000, ("read",)))
    connected_at = 1001
    if case == "not_expired":
        connected_at = 999
    elif case == "old_token":
        new = replace(new, token=McpOAuthToken("private-access", "new-refresh", 2000, ("read",)))
    elif case == "expired_replacement":
        new = replace(new, token=McpOAuthToken("new-access", "new-refresh", 1002, ("read",)))
    elif case == "missing_expiry":
        new = replace(new, token=McpOAuthToken("new-access", "new-refresh", None, ("read",)))
    elif case == "missing_refresh":
        old = replace(old, token=McpOAuthToken("private-access", None, 1000, ("read",)))
    elif case == "login_instead_of_refresh":
        new = replace(new, generation=2)
    elif case == "changed_client":
        new = replace(new, client=replace(new.client, client_id="other-client"))
    before = McpOAuthCredentialRecord(old.generation, old)
    after = McpOAuthCredentialRecord(new.generation, None if case == "missing_record" else new)
    assert refresh_committed(before, after, connected_at=connected_at, observed_at=1003) is (case == "valid")


def test_report_projection_does_not_include_identity_or_credential_material() -> None:
    saved = snapshot()
    evidence = credential_evidence(McpOAuthCredentialRecord(saved.generation, saved))
    serialized = json.dumps(asdict(evidence))
    assert evidence.present and evidence.has_refresh_token and evidence.expires_at == 1000
    assert "private-" not in serialized
    assert "example" not in serialized
    assert "callback" not in serialized


@pytest.mark.anyio
async def test_failed_readonly_call_still_closes_owner_and_captures_local_state(tmp_path: Path) -> None:
    saved = snapshot()
    record = McpOAuthCredentialRecord(saved.generation, saved)
    ConfigStore(tmp_path / "config/config.toml").update({
        ("mcp_servers", "acceptance"): {"url": saved.target.server_url},
    })
    store = Mock(read=AsyncMock(return_value=record))
    group = Mock(
        start=AsyncMock(), started=True, service_snapshots=(), owned_keys=frozenset(),
        tools={"mcp__acceptance__execute_sentry_tool": Mock()},
        call_tool=AsyncMock(return_value=CallToolResult(content=[], isError=True)),
        close=AsyncMock(),
    )
    report = LiveReport("time", "platform", "version", "sdk", "keyring", "call")
    with patch("tests.manual.mcp_oauth_sentry.SystemMcpCredentialStore", return_value=store), patch(
        "tests.manual.mcp_oauth_sentry.ExternalMcpGroup", return_value=group,
    ):
        with pytest.raises(LiveCheckError, match="readonly_call_failed"):
            await run_live(tmp_path / "config", tmp_path / "state", "acceptance", "call", 2, report)
    group.close.assert_awaited_once()
    assert not report.call_succeeded
    assert report.resources_closed
    assert report.after == credential_evidence(record)


def test_failed_run_preserves_sanitized_report_and_never_overwrites_it(tmp_path: Path) -> None:
    report_path = tmp_path / "state/reports/failure.json"
    arguments = [
        "mcp_oauth_sentry", "--config-root", str(tmp_path / "config"),
        "--state-root", str(tmp_path / "state"), "--report", str(report_path),
    ]
    with patch.object(sys, "argv", arguments), patch(
        "tests.manual.mcp_oauth_sentry.run_live",
        AsyncMock(side_effect=RuntimeError("private-access https://issuer.example/token")),
    ) as run:
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 1
        first = report_path.read_bytes()
        report = json.loads(first)
        assert report["status"] == "failed" and report["error_code"] == "RuntimeError"
        assert b"private-access" not in first and b"issuer.example" not in first
        with pytest.raises(FileExistsError):
            main()
        assert report_path.read_bytes() == first
        run.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("authorization_error", ["login_required", "network_error", None])
async def test_logged_out_probe_requires_runtime_login_required(
    tmp_path: Path, authorization_error: McpOAuthErrorCode | None,
) -> None:
    saved = snapshot()
    record = McpOAuthCredentialRecord(2, None)
    ConfigStore(tmp_path / "config/config.toml").update({
        ("mcp_servers", "acceptance"): {"url": saved.target.server_url},
    })
    store = Mock(read=AsyncMock(return_value=record))
    group = Mock(
        start=AsyncMock(), started=False, owned_keys=frozenset(), tools={},
        service_snapshots=(McpServiceSnapshot(
            config_key="acceptance", tool_prefix="mcp__acceptance__",
            config_enabled=True, state="failed", transport="streamable_http",
            authorization=McpAuthorizationStatus(error=authorization_error),
        ),),
        call_tool=AsyncMock(), close=AsyncMock(),
    )
    report = LiveReport("time", "platform", "version", "sdk", "keyring", "logged-out")
    with patch("tests.manual.mcp_oauth_sentry.SystemMcpCredentialStore", return_value=store), patch(
        "tests.manual.mcp_oauth_sentry.ExternalMcpGroup", return_value=group,
    ):
        if authorization_error == "login_required":
            await run_live(tmp_path / "config", tmp_path / "state", "acceptance", "logged-out", 2, report)
            assert report.logout_observed and report.status == "passed"
        else:
            with pytest.raises(LiveCheckError, match="logged_out_boundary_failed"):
                await run_live(tmp_path / "config", tmp_path / "state", "acceptance", "logged-out", 2, report)
    group.start.assert_awaited_once()
    group.call_tool.assert_not_awaited()
    group.close.assert_awaited_once()
    assert report.resources_closed and report.after == credential_evidence(record)
