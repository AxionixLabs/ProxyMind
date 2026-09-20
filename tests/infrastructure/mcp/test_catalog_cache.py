# -*- coding: utf-8 -*-

from dataclasses import replace

import pytest
from mcp import types as mcp_types

from infrastructure.config.schema import (
    ConfigValidationError,
    validate_config,
)
from infrastructure.mcp.catalog_cache import (
    CatalogIdentity,
    CatalogSnapshot,
    ToolCatalogCache,
    configuration_identity,
)
from infrastructure.mcp.settings import normalize_mcp_servers


def catalog(description="first"):
    return CatalogSnapshot.capture({"mcp__s__read": mcp_types.Tool(
        name="read", description=description, inputSchema={"type": "object"},
        annotations=mcp_types.ToolAnnotations(readOnlyHint=True),
        meta={"server": "s", "approval_mode": "approve"},
    )})


def test_cache_identity_ttl_lru_and_memory_bounds(tmp_path):
    now = [0.0]
    cache = ToolCatalogCache(capacity=2, max_bytes=2000, ttl_sec=10, clock=lambda: now[0])
    first = CatalogIdentity(tmp_path, "s", "config", 4)
    item = catalog()
    cache.publish(first, item)
    for other in (replace(first, workspace=tmp_path / "other"), replace(first, configuration="different"), replace(first, generation=5), replace(first, config_key="other")):
        assert cache.get(other) is None
    second, third = replace(first, config_key="two"), replace(first, config_key="three")
    cache.publish(second, item)
    assert cache.get(first) is item
    cache.publish(third, item)
    assert cache.get(second) is None
    now[0] = 9
    assert cache.get(first) is item
    now[0] = 10
    assert cache.get(first) is None
    cache.publish(first, catalog("x" * 3000))
    assert cache.get(first) is None
    cache.publish(first, item)
    cache.publish(replace(first, generation=5), item)
    assert cache.get(first) is None
    cache.clear()
    assert cache.get(replace(first, generation=5)) is None


def test_cache_preview_is_independent_and_cannot_supply_approval_grants():
    snapshot = catalog()
    preview = snapshot.preview()
    tool = preview["mcp__s__read"]
    assert tool.annotations is None
    assert tool.meta["approval_mode"] == "prompt"
    assert tool.meta["approval_allow_session"] is False
    assert tool.meta["approval_allow_persistent"] is False
    tool.inputSchema["changed"] = True
    assert "changed" not in snapshot.preview()["mcp__s__read"].inputSchema
    assert catalog("different").revision != snapshot.revision


def test_effective_config_fingerprint_covers_environment_headers_and_policy():
    config = {"s": {"url": "https://example.test/mcp", "bearer_token_env_var": "TEST_TOKEN"}}
    first = normalize_mcp_servers(config, environment={"TEST_TOKEN": "first"})[0]
    second = normalize_mcp_servers(config, environment={"TEST_TOKEN": "second"})[0]
    assert configuration_identity(first) != configuration_identity(second)
    assert "first" not in configuration_identity(first)
    assert configuration_identity(first) != configuration_identity({**first, "tool_filter": {"enabled_tools": []}})
    assert configuration_identity(first) != configuration_identity({**first, "tool_filter": {"disabled_tools": ["read"]}})


@pytest.mark.parametrize("value", [-1, True, "1", float("inf"), float("nan")])
def test_optional_startup_budget_rejects_invalid_config(value):
    with pytest.raises(ConfigValidationError):
        validate_config({"mcp_servers": {"s": {"url": "https://example.test/mcp", "optional_startup_wait_sec": value}}})


@pytest.mark.parametrize("value", [0, 0.1, 2])
def test_optional_startup_budget_accepts_disabled_and_finite_budget(value):
    validate_config({"mcp_servers": {"s": {"url": "https://example.test/mcp", "optional_startup_wait_sec": value}}})
