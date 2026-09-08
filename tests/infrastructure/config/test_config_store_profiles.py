# -*- coding: utf-8 -*-

"""验证用户配置文档持久化与 profile 覆盖语义。"""


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


def test_invalid_update_does_not_replace_user_document(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(store)
    session.resolve()
    original = store.path.read_text(encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="exactly one"):
        session.update_user({
            ("mcp_servers", "broken", "enabled"): True,
        })

    assert store.path.read_text(encoding="utf-8") == original


def test_config_store_keeps_blank_lines_between_table_sections(
    tmp_path,
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.path.write_text(
        'model_provider = "main"\n'
        '[model_providers.main]\nname = "main"\n'
        '[mcp_servers.playwright]\ncommand = "npx"\n'
        '[service]\ndomain = ""\n'
        '[skills]\nenabled = []\n'
        '[projects]\n',
        encoding="utf-8",
    )

    store.update({
        ("model_providers", "aaa", "name"): "aaa",
        ("model_providers", "aaa", "base_url"): "",
        ("mcp_servers", "review", "command"): "review-server",
        ("projects", "workspace", "trust_level"): "trusted",
    })

    text = store.path.read_text(encoding="utf-8")
    assert 'model_provider = "main"\n\n[model_providers.main]' in text
    assert 'name = "main"\n\n[model_providers.aaa]' in text
    assert 'base_url = ""\n\n[mcp_servers.playwright]' in text
    assert 'command = "npx"\n\n[mcp_servers.review]' in text
    assert (
        'command = "review-server"\n\n'
        '[service]\n'
        'domain = ""\n\n'
        '[skills]\n'
        'enabled = []\n\n'
        '[projects]\n\n'
        '[projects.workspace]'
    ) in text
    assert text.endswith('trust_level = "trusted"\n')
    assert not text.endswith("\n\n")

    store.update({("model_providers", "aaa", "name"): "aaa"})
    assert store.path.read_text(encoding="utf-8") == text


def test_config_store_keeps_heading_comments_with_the_next_table(
    tmp_path,
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.path.write_text(
        '[model_providers.main]\nname = "main"\n'
        '# Playwright settings\n'
        '[mcp_servers.playwright]\ncommand = "npx"\n'
        '[projects]\n'
        '# Workspace settings\n'
        '[projects.workspace]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    store.update({("model_providers", "main", "name"): "main"})

    text = store.path.read_text(encoding="utf-8")
    assert (
        'name = "main"\n\n'
        '# Playwright settings\n'
        '[mcp_servers.playwright]'
    ) in text
    assert '# Playwright settings\n\n[mcp_servers.playwright]' not in text
    assert (
        '[projects]\n\n'
        '# Workspace settings\n'
        '[projects.workspace]'
    ) in text


def test_profile_can_switch_mcp_transport(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("mcp_servers", "browser"): {
            "command": "browser-server",
            "args": ["--stdio"],
        },
    })
    (tmp_path / "remote.config.toml").write_text(
        "[mcp_servers.browser]\n"
        'url = "https://example.test/mcp"\n',
        encoding="utf-8",
    )

    config = ConfigSession(store, profile="remote").load()

    assert config["mcp_servers"]["browser"] == {
        "url": "https://example.test/mcp",
    }


def test_profile_merges_keymap_actions_and_preserves_explicit_unbinding(
    tmp_path,
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("tui", "keymap", "global", "open_transcript"): "f10",
        ("tui", "keymap", "composer", "submit"): "f11",
        ("tui", "keymap", "composer", "queue"): "tab",
    })
    (tmp_path / "work.config.toml").write_text(
        "[tui.keymap.composer]\n"
        "queue = []\n"
        "[tui.keymap.editor]\n"
        'move_left = "f12"\n',
        encoding="utf-8",
    )

    config = ConfigSession(store, profile="work").load()

    assert config["tui"]["keymap"]["global"]["open_transcript"] == "f10"
    assert config["tui"]["keymap"]["composer"] == {
        "submit": "f11",
        "queue": [],
    }
    assert config["tui"]["keymap"]["editor"] == {"move_left": "f12"}


def test_permission_update_reports_profile_shadow_before_write(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.ensure()
    (tmp_path / "remote.config.toml").write_text(
        'sandbox_mode = "read-only"\n',
        encoding="utf-8",
    )
    session = ConfigSession(store, profile="remote")

    with pytest.raises(ConfigStoreError, match="shadowed"):
        session.update_user(
            {("sandbox_mode",): "workspace-write"},
            ensure_effective={("sandbox_mode",): "workspace-write"},
        )

    assert store.read_raw().get("sandbox_mode") != "workspace-write"


def test_permission_update_reports_cli_shadow_before_write(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.ensure()
    session = ConfigSession(
        store,
        (config_override(("sandbox_mode",), "read-only"),),
    )

    with pytest.raises(ConfigStoreError, match="shadowed"):
        session.update_user(
            {("sandbox_mode",): "workspace-write"},
            ensure_effective={("sandbox_mode",): "workspace-write"},
        )

    assert store.read_raw().get("sandbox_mode") != "workspace-write"
