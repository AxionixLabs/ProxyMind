# -*- coding: utf-8 -*-

import pytest

from agent.domain.mcp_authorization import (
    McpAuthorizationStatus,
    authorization_failed,
    authorization_from_credentials,
)
from agent.domain.mcp_oauth import (
    McpOAuthCredentialView,
    McpOAuthTarget,
)
from frontends.terminal.mcp_authorization import authorization_recovery


@pytest.mark.parametrize("state,expected", [
    ("missing", "unknown"), ("stored", "oauth"), ("expired", "oauth"),
    ("registered", "not_logged_in"), ("refresh_uncertain", "reauthorization_required"),
    ("reauthorization_required", "reauthorization_required"), ("unavailable", "unavailable"),
])
def test_local_credentials_never_claim_remote_acceptance(state, expected):
    view = McpOAuthCredentialView(McpOAuthTarget("server", "https://example.test/mcp"), state, generation=0)
    status = authorization_from_credentials(view, McpAuthorizationStatus())
    assert status.state == expected and status.verification == "unverified"


def test_replacement_logout_and_stale_reads_clear_only_obsolete_verification():
    target = McpOAuthTarget("server", "https://example.test/mcp")
    accepted = McpAuthorizationStatus("oauth", "stored", "accepted", generation=4)
    assert authorization_from_credentials(McpOAuthCredentialView(target, "stored", generation=4), accepted) == accepted
    assert authorization_from_credentials(McpOAuthCredentialView(target, "missing", generation=3), accepted) == accepted
    for state, expected in (("stored", "oauth"), ("missing", "not_logged_in")):
        updated = authorization_from_credentials(McpOAuthCredentialView(target, state, generation=5), accepted)
        assert updated.state == expected and updated.verification == "unverified"
    unavailable = authorization_failed(accepted, "storage_unavailable")
    recovered = authorization_from_credentials(McpOAuthCredentialView(target, "stored", generation=4), unavailable)
    assert recovered.state == "oauth" and recovered.error is None
    expired = McpAuthorizationStatus("reauthorization_required", "expired", generation=4, error="reauthorization_required")
    assert authorization_from_credentials(McpOAuthCredentialView(target, "expired", generation=4), expired) == expired


@pytest.mark.parametrize("state", ["header", "bearer"])
def test_explicit_rejection_does_not_offer_oauth_login(state):
    status = authorization_failed(McpAuthorizationStatus(state), "login_required")
    assert status.state == state and status.verification == "rejected"
    hint = authorization_recovery(status, "special name")
    assert hint is not None and '"special name"' in hint and "configured" in hint
    assert "mcp login" not in hint


def test_recovery_names_are_literal_sanitized_registration_names():
    hint = authorization_recovery(McpAuthorizationStatus("not_logged_in"), " odd; $(name)\x1b[31m ")
    assert hint is not None and "exact server name" in hint and "\x1b" not in hint
    assert "Run mind mcp login" not in hint
