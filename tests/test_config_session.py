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
from mind_core.hooks import HOOK_EVENT_CONFIG_SPECS


def _command_handler(command, *, timeout=None):
    handler = {"type": "command", "command": command}
    if timeout is not None:
        handler["timeout"] = timeout
    return handler


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


@pytest.mark.parametrize(
    ("trust_level", "project_enabled"),
    [
        (None, False),
        ("untrusted", False),
        ("trusted", True),
    ],
)
def test_project_trust_state_controls_project_automation(
    tmp_path,
    trust_level,
    project_enabled,
) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        "[mcp_servers.project]\n"
        'command = "project-server"\n'
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "project-hook" }]\n',
        encoding="utf-8",
    )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.ensure()
    if trust_level is not None:
        store.update({
            ("projects", str(project_root)): {"trust_level": trust_level},
        })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.root == project_root.resolve()
    assert resolution.project_trust.level == trust_level
    assert resolution.project_trust.trusted is project_enabled
    assert ("project" in resolution.config["mcp_servers"]) is project_enabled
    assert bool(resolution.hooks) is project_enabled
    assert any(
        layer.scope == "project" for layer in resolution.layers
    ) is project_enabled


@pytest.mark.parametrize(
    ("markers", "expected_root_name"),
    [
        (["pyproject.toml"], "project"),
        ([], "src"),
    ],
)
def test_project_root_markers_define_the_trust_target(
    tmp_path,
    markers,
    expected_root_name,
) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("", encoding="utf-8")

    expected_root = project_root if expected_root_name == "project" else workspace
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("project_root_markers",): markers,
        ("projects", str(expected_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.root == expected_root.resolve()
    assert resolution.project_trust.level == "trusted"


def test_profile_cannot_mark_a_project_as_trusted(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text('model = "project-model"\n', encoding="utf-8")

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.ensure()
    ConfigStore(store.path.parent / "review.config.toml").update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(
        store,
        profile="review",
        workspace=workspace,
    ).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level is None
    assert not resolution.project_trust.trusted
    assert resolution.config["model"]["primary"]["model"] != "project-model"
    assert all(layer.scope != "project" for layer in resolution.layers)


def test_invalid_project_config_prevents_persisting_trust(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        '[tui.keymap.global]\nopen_transcript = "f12"\n',
        encoding="utf-8",
    )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    session.resolve()
    original = store.path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="cannot override tui"):
        session.update_user({
            ("projects", str(project_root), "trust_level"): "trusted",
        })

    assert store.path.read_text(encoding="utf-8") == original
    assert session.resolve().project_trust is not None
    assert session.resolve().project_trust.level is None


def test_config_session_persists_project_trust_decisions(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text('model = "project-model"\n', encoding="utf-8")

    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)

    trusted_config = session.update_user({
        ("projects", str(project_root), "trust_level"): "trusted",
    })

    trusted = session.resolve().project_trust
    assert trusted is not None
    assert trusted.level == "trusted"
    assert trusted_config["model"]["primary"]["model"] == "project-model"
    assert store.read_raw()["projects"][str(project_root)] == {
        "trust_level": "trusted",
    }

    untrusted_config = session.update_user({
        ("projects", str(project_root), "trust_level"): "untrusted",
    })

    untrusted = session.resolve().project_trust
    assert untrusted is not None
    assert untrusted.level == "untrusted"
    assert untrusted_config["model"]["primary"]["model"] != "project-model"


def test_project_trust_update_rejects_invalid_state(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    session = ConfigSession(ConfigStore(tmp_path / "home" / "config.toml"))

    with pytest.raises(ConfigValidationError, match="trusted or untrusted"):
        session.update_user({
            ("projects", str(project_root), "trust_level"): "unknown",
        })

    with pytest.raises(ConfigValidationError, match="unknown config key"):
        session.update_user({
            ("projects", str(project_root), "enabled"): True,
        })


def test_project_trust_matches_a_resolved_path_alias(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    alias = tmp_path / "project-alias"
    try:
        alias.symlink_to(project_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink is unavailable: {error}")

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(alias)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.root == project_root.resolve()
    assert resolution.project_trust.level == "trusted"


def test_project_config_cannot_override_tui_keymap(tmp_path) -> None:
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
        "[tui.keymap.global]\n"
        'open_transcript = "f12"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot override tui"):
        ConfigSession(store, workspace=workspace).resolve()


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
            "hooks": [_command_handler("check-user")],
        }],
    })
    (tmp_path / "review.config.toml").write_text(
        "[[hooks.PostToolUse]]\n"
        'matcher = "shell_command"\n'
        'hooks = [{ type = "command", command = "audit-profile" }]\n',
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
    assert all(
        hook.content_hash.startswith("sha256:")
        and len(hook.content_hash) == 71
        for hook in resolution.hooks
    )
    assert set(resolution.config["hooks"]) == {"PreToolUse", "PostToolUse"}


def test_user_hook_state_is_resolved_and_preserved_in_config(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    trusted_hash = "sha256:" + "a" * 64
    store.update({
        ("hooks", "state", "user:key"): {
            "enabled": False,
            "trusted_hash": trusted_hash,
        },
    })

    resolution = ConfigSession(store).resolve()

    assert resolution.hook_states == {
        "user:key": {
            "enabled": False,
            "trusted_hash": trusted_hash,
        },
    }
    assert resolution.config["hooks"]["state"] == resolution.hook_states
    assert store.read_raw()["hooks"]["state"] == resolution.hook_states


def test_profile_and_project_cannot_authorize_hook_state(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()
    trusted_hash = "sha256:" + "b" * 64

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
        ("hooks", "state", "user-hook"): {
            "trusted_hash": trusted_hash,
        },
    })
    (store.path.parent / "review.config.toml").write_text(
        "[hooks.state.profile-hook]\n"
        "enabled = false\n",
        encoding="utf-8",
    )
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        "[hooks.state.project-hook]\n"
        "enabled = false\n",
        encoding="utf-8",
    )

    resolution = ConfigSession(
        store,
        profile="review",
        workspace=workspace,
    ).resolve()

    assert resolution.hook_states == {
        "user-hook": {"trusted_hash": trusted_hash},
    }
    assert resolution.config["hooks"]["state"] == resolution.hook_states


def test_cli_hook_state_override_is_temporary(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    trusted_hash = "sha256:" + "c" * 64
    store.update({
        ("hooks", "state", "sample"): {
            "enabled": True,
            "trusted_hash": trusted_hash,
        },
    })
    session = ConfigSession(
        store,
        (parse_config_override("hooks.state.sample.enabled=false"),),
    )

    resolution = session.resolve()

    assert resolution.hook_states["sample"] == {
        "enabled": False,
        "trusted_hash": trusted_hash,
    }
    assert store.read_raw()["hooks"]["state"]["sample"]["enabled"] is True


def test_cli_hook_override_replaces_runtime_definitions(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "hooks": [_command_handler("check-user")],
        }],
    })
    override = parse_config_override(
        "hooks={ PreToolUse = [{ hooks = [{ type = \"command\", "
        "command = \"check-cli\" }] }] }"
    )

    resolution = ConfigSession(store, (override,)).resolve()

    assert [definition.handler.command for definition in resolution.hooks] == [
        "check-cli",
    ]
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
        'hooks = [{ type = "command", command = "check-project" }]\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.root == project_root.resolve()
    assert resolution.project_trust.level == "trusted"
    assert resolution.project_trust.trusted
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
            'hooks = [{ type = "command", '
            f'command = "check-{index}" }}]\n',
            encoding="utf-8",
        )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"}
        for project_root in projects
    })
    session = ConfigSession(store, workspace=projects[0])

    assert session.resolve().hooks[0].handler.command == "check-0"

    assert session.resolve(workspace=projects[1]).hooks[0].handler.command == (
        "check-1"
    )
    assert session.resolve().hooks[0].handler.command == "check-0"


def test_invalid_hook_matcher_warns_and_skips_only_its_group(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [
            {
                "hooks": [_command_handler("invalid")],
                "matcher": "[",
            },
            {
                "hooks": [_command_handler("valid")],
                "matcher": "shell_command",
            },
        ],
    })

    resolution = ConfigSession(store).resolve()

    assert [hook.handler.command for hook in resolution.hooks] == ["valid"]
    assert "invalid matcher '['" in resolution.hook_warnings[0]


def test_session_start_hook_accepts_reason_matcher() -> None:
    config = normalize_config({
        "hooks": {
            "SessionStart": [{
                "hooks": [_command_handler("prepare-session")],
                "matcher": "initial|reset",
            }],
        },
    })

    assert config["hooks"]["SessionStart"][0]["matcher"] == "initial|reset"


def test_hook_event_contracts_are_explicit() -> None:
    expected = {
        "PreToolUse": ("tool_name", "gate", True),
        "PermissionRequest": ("tool_name", "permission", False),
        "PostToolUse": ("tool_name", "notify", True),
        "PreCompact": ("compact_trigger", "gate", False),
        "PostCompact": ("compact_trigger", "notify", False),
        "SessionStart": ("session_reason", "notify", True),
        "UserPromptSubmit": (None, "gate", True),
        "SubagentStart": ("agent_type", "notify", True),
        "SubagentStop": ("agent_type", "notify", False),
        "Stop": (None, "notify", False),
        "SessionEnd": ("session_reason", "notify", False),
    }

    assert {
        event: (
            spec.matcher_subject,
            spec.control_policy,
            spec.supports_additional_context,
        )
        for event, spec in HOOK_EVENT_CONFIG_SPECS.items()
    } == expected


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
def test_hook_event_without_match_subject_ignores_matcher(event) -> None:
    config = normalize_config({
        "hooks": {
            event: [{
                "hooks": [_command_handler("check")],
                "matcher": "value",
            }],
        },
    })

    assert config["hooks"][event][0]["matcher"] == ""


@pytest.mark.parametrize("field", ["on_error", "enabled", "handler"])
def test_unknown_matcher_group_fields_are_ignored(field) -> None:
    config = normalize_config({
        "hooks": {
            "PostToolUse": [{
                "hooks": [_command_handler("notify")],
                field: True,
            }],
        },
    })

    group = config["hooks"]["PostToolUse"][0]
    assert field not in group
    assert group["hooks"][0]["command"] == "notify"


def test_legacy_flat_hook_command_is_ignored() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{"command": "check"}],
        },
    })

    assert config["hooks"]["PreToolUse"] == [{"matcher": "", "hooks": []}]


def test_empty_matcher_group_is_allowed() -> None:
    config = normalize_config({"hooks": {"PreToolUse": [{}]}})

    assert config["hooks"]["PreToolUse"] == [{"matcher": "", "hooks": []}]


def test_hook_handler_ignores_unknown_fields() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{
                "hooks": [{
                    "type": "command",
                    "command": "check",
                    "extra": True,
                }],
            }],
        },
    })

    handler = config["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["command"] == "check"
    assert "extra" not in handler


def test_hook_handler_without_type_is_skipped() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{
                "hooks": [{"command": "check"}],
            }],
        },
    })

    assert config["hooks"]["PreToolUse"][0]["hooks"] == []


def test_hook_handler_timeout_is_normalized() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{
                "hooks": [_command_handler("check", timeout=2)],
            }],
        },
    })

    assert config["hooks"]["PreToolUse"][0]["hooks"][0] == {
        "type": "command",
        "command": "check",
        "commandWindows": None,
        "statusMessage": None,
        "timeout": 2,
        "async": False,
        "additionalContextLimit": 2500,
    }


def test_hook_handler_uses_event_timeout_defaults() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{"hooks": [_command_handler("check")]}],
            "SessionEnd": [{"hooks": [_command_handler("close")]}],
        },
    })

    assert config["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 600
    assert config["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 1


def test_session_end_hook_timeout_is_clamped_with_warning(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "SessionEnd"): [{
            "hooks": [_command_handler("close", timeout=4)],
        }],
    })

    resolution = ConfigSession(store).resolve()

    assert resolution.hooks[0].handler.timeout_sec == 3
    assert "clamping SessionEnd hook timeout to 3s" in (
        resolution.hook_warnings[0]
    )


def test_hook_handler_accepts_toml_windows_command_name() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{
                "hooks": [{
                    "type": "command",
                    "command": "check-posix",
                    "command_windows": "check-windows",
                }],
            }],
        },
    })

    handler = config["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["commandWindows"] == "check-windows"
    assert "command_windows" not in handler


def test_hook_matcher_group_expands_multiple_command_handlers() -> None:
    config = normalize_config({
        "hooks": {
            "PreToolUse": [{
                "matcher": "shell_command",
                "hooks": [
                    {
                        "type": "command",
                        "command": "check-posix",
                        "commandWindows": "check-windows",
                        "statusMessage": "Checking command",
                        "timeout": 7,
                        "async": False,
                        "additionalContextLimit": 800,
                    },
                    _command_handler("audit"),
                ],
            }],
        },
    })

    handlers = config["hooks"]["PreToolUse"][0]["hooks"]
    assert [handler["command"] for handler in handlers] == [
        "check-posix",
        "audit",
    ]
    assert handlers[0]["commandWindows"] == "check-windows"
    assert handlers[0]["statusMessage"] == "Checking command"
    assert handlers[0]["async"] is False
    assert handlers[0]["additionalContextLimit"] == 800


def test_discovery_skips_unsupported_handlers_and_keeps_valid_hook(
    tmp_path,
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "hooks": [
                {"type": "prompt"},
                {"type": "agent"},
                {"type": "custom", "command": "custom"},
                {"type": "command", "command": ""},
                {
                    "type": "command",
                    "command": "async-command",
                    "async": True,
                },
                {
                    "type": "command",
                    "command": "valid",
                    "extra": True,
                },
            ],
            "legacy": True,
        }],
    })

    resolution = ConfigSession(store).resolve()

    assert [hook.handler.command for hook in resolution.hooks] == ["valid"]
    assert resolution.hooks[0].key.endswith(":PreToolUse:0:5")
    warnings = "\n".join(resolution.hook_warnings)
    assert "skipping prompt hook" in warnings
    assert "skipping agent hook" in warnings
    assert "unsupported handler type 'custom'" in warnings
    assert "skipping empty hook command" in warnings
    assert "skipping async hook" in warnings
    assert "ignoring unknown fields" in warnings
