# -*- coding: utf-8 -*-

import pytest

from mind_core.config import (
    ConfigValidationError,
    normalize_config,
    parse_config_override,
)
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.config_layers import PROJECT_CONFIG_DIR


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


def test_unknown_config_field_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="config.typo"):
        normalize_config({"typo": True})


def test_cli_override_does_not_modify_user_document(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.ensure()
    original = store.path.read_text(encoding="utf-8")
    session = ConfigSession(
        store,
        (parse_config_override('model="temporary-model"'),),
    )

    config = session.load()

    assert config["model"]["primary"]["model"] == "temporary-model"
    assert store.path.read_text(encoding="utf-8") == original


def test_user_and_profile_hooks_keep_source_identity(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "matcher": "shell_command",
            "command": "check-user",
        }],
    })
    (tmp_path / "review.config.toml").write_text(
        "[[hooks.PostToolUse]]\n"
        'matcher = "shell_command"\n'
        'command = "audit-profile"\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, profile="review").resolve()

    assert [hook.event for hook in resolution.hooks] == [
        "PreToolUse",
        "PostToolUse",
    ]
    assert [hook.source_scope for hook in resolution.hooks] == [
        "user",
        "profile",
    ]
    assert all(len(hook.content_hash) == 64 for hook in resolution.hooks)
    assert set(resolution.config["hooks"]) == {"PreToolUse", "PostToolUse"}


def test_cli_hook_override_replaces_runtime_definitions(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{"command": "check-user"}],
    })
    override = parse_config_override(
        "hooks={ PreToolUse = [{ command = \"check-cli\" }] }"
    )

    resolution = ConfigSession(store, (override,)).resolve()

    assert [definition.command for definition in resolution.hooks] == ["check-cli"]
    assert [definition.source_scope for definition in resolution.hooks] == ["cli"]


def test_trusted_project_hooks_keep_project_source_identity(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'matcher = "shell_command"\n'
        'command = "check-project"\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trusted
    assert len(resolution.hooks) == 1
    assert resolution.hooks[0].source_scope == "project"
    assert resolution.hooks[0].source_path == str(project_config.resolve())


def test_workspace_override_selects_project_hook_source(tmp_path) -> None:
    projects = [tmp_path / "first", tmp_path / "second"]
    for index, project_root in enumerate(projects):
        (project_root / ".git").mkdir(parents=True)
        project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
        project_config.parent.mkdir()
        project_config.write_text(
            "[[hooks.PreToolUse]]\n"
            f'command = "check-{index}"\n',
            encoding="utf-8",
        )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"}
        for project_root in projects
    })
    session = ConfigSession(store, workspace=projects[0])

    assert session.resolve().hooks[0].command == "check-0"

    assert session.resolve(workspace=projects[1]).hooks[0].command == "check-1"
    assert session.resolve().hooks[0].command == "check-0"


def test_invalid_hook_matcher_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="matcher is invalid"):
        normalize_config({
            "hooks": {
                "PreToolUse": [{
                    "command": "check",
                    "matcher": "[",
                }],
            },
        })


def test_post_tool_hook_cannot_fail_closed() -> None:
    with pytest.raises(ConfigValidationError, match="continue for this event"):
        normalize_config({
            "hooks": {
                "PostToolUse": [{
                    "command": "audit",
                    "on_error": "block",
                }],
            },
        })
