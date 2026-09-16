# -*- coding: utf-8 -*-

import pytest

from agent.domain.mcp_oauth import (
    McpOAuthTarget,
    normalize_oauth_url,
)


@pytest.mark.parametrize("url", [
    "https://user:secret@example/mcp", "https://example/mcp#", "https://example:/mcp",
    "https://example/m cp", "file:///secret", "https://example:0/mcp", "https://example\\evil/mcp",
])
def test_invalid_target_urls_do_not_silently_change_identity(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid MCP OAuth URL"):
        normalize_oauth_url(url)


def test_normalization_preserves_full_service_identity() -> None:
    assert normalize_oauth_url("HTTPS://EXAMPLE.COM:443/mcp/A?tenant=X") == "https://example.com:443/mcp/A?tenant=X"
    assert normalize_oauth_url("https://example.com?") == "https://example.com/?"
    assert normalize_oauth_url("http://[::1]:8888/mcp") == "http://[::1]:8888/mcp"
    assert McpOAuthTarget(" raw key ", "https://example.com/mcp").config_key == " raw key "


def test_target_requires_normalization_before_freezing() -> None:
    with pytest.raises(ValueError, match="normalized"):
        McpOAuthTarget("sentry", "HTTPS://EXAMPLE.COM/mcp")
