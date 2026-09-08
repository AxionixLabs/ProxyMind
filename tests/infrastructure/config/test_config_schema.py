# -*- coding: utf-8 -*-

"""验证配置 schema、provider 引用与兼容性拒绝规则。"""


import json
import os
import pytest
from infrastructure.config.schema import (
    ConfigValidationError,
    config_override,
    normalize_config,
    parse_config_override,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import (
    ConfigStore,
    ConfigStoreError,
)
from infrastructure.config.layers import PROJECT_CONFIG_DIR
from infrastructure.hooks.discovery import HOOKS_FILE_NAME
from agent.domain.hooks import HOOK_EVENT_CONFIG_SPECS


def test_unknown_config_field_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="config.typo"):
        normalize_config({"typo": True})


def test_network_access_is_normalized_as_a_root_permission_field() -> None:
    assert normalize_config({})["network_access"] == "restricted"
    assert normalize_config({"network_access": "enabled"})[
        "network_access"
    ] == "enabled"

    with pytest.raises(ConfigValidationError, match="network_access"):
        normalize_config({"network_access": "open"})


def test_removed_approval_reviewer_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="approvals_reviewer"):
        normalize_config({"approvals_reviewer": "guardian_subagent"})


def test_default_config_uses_only_provider_profiles(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")

    raw = store.read_raw()

    assert raw["model_provider"] == "openai-main"
    assert raw["model_providers"]["openai-main"] == {
        "name": "openai-main",
        "kind": "openai",
        "model": "",
        "route": "responses",
        "reasoning_effort": "medium",
        "api_key": "",
        "base_url": "",
    }
    assert {"model", "model_reasoning_effort", "model_enabled"}.isdisjoint(raw)


@pytest.mark.parametrize(
    "legacy_field",
    ["model", "model_reasoning_effort", "model_enabled"],
)
def test_legacy_model_fields_are_rejected(legacy_field) -> None:
    with pytest.raises(ConfigValidationError, match=legacy_field):
        normalize_config({legacy_field: "unsupported"})


def test_active_provider_must_reference_a_profile() -> None:
    with pytest.raises(ConfigValidationError, match="unknown profile"):
        normalize_config({
            "model_provider": "missing",
            "model_providers": {},
        })


def test_provider_ids_and_routes_follow_the_new_schema() -> None:
    with pytest.raises(ConfigValidationError, match="provider id"):
        normalize_config({
            "model_provider": "invalid.profile",
            "model_providers": {"invalid.profile": {}},
        })

    config = normalize_config({
        "model_provider": "openai-main",
        "model_providers": {
            "openai-main": {
                "kind": "OpenAI",
                "route": "Responses",
            },
        },
    })
    assert config["model"]["primary"]["kind"] == "openai"
    assert config["model"]["primary"]["route"] == "responses"

    with pytest.raises(ConfigValidationError, match="route must be one of"):
        normalize_config({
            "model_provider": "openai-main",
            "model_providers": {
                "openai-main": {},
                "custom": {"route": "messages"},
            },
        })


def test_profile_overlay_can_select_and_partially_override_provider(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.ensure()
    store.update({
        ("model_providers", "claude-main", "name"): "Claude",
        ("model_providers", "claude-main", "kind"): "anthropic",
        ("model_providers", "claude-main", "model"): "claude-test",
        ("model_providers", "claude-main", "route"): "messages",
        ("model_providers", "claude-main", "reasoning_effort"): "high",
        ("model_providers", "claude-main", "api_key"): "",
        ("model_providers", "claude-main", "base_url"): "",
    })
    (tmp_path / "work.config.toml").write_text(
        'model_provider = "claude-main"\n'
        '[model_providers.claude-main]\n'
        'route = "messages"\n',
        encoding="utf-8",
    )

    config = ConfigSession(store, profile="work").load()

    assert config["model"]["primary"]["provider"] == "claude-main"
    assert config["model"]["primary"]["kind"] == "anthropic"
    assert config["model"]["primary"]["route"] == "messages"
