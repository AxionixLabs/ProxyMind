# -*- coding: utf-8 -*-

"""验证 Hook 配置来源、解析、覆盖和事件契约。"""


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


def _command_handler(command, *, timeout=None):
    handler = {"type": "command", "command": command}
    if timeout is not None:
        handler["timeout"] = timeout
    return handler


def _write_hook_file(
    directory,
    command,
    *,
    event="PreToolUse",
):
    path = directory / HOOKS_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "description": "Lifecycle checks",
            "hooks": {
                event: [{
                    "hooks": [{
                        "type": "command",
                        "command": command,
                    }],
                }],
            },
        }),
        encoding="utf-8",
    )
    return path


def _linked_worktree(
    tmp_path,
    *,
    relative_pointer: bool,
):
    repository_root = tmp_path / "repository"
    git_dir = repository_root / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    pointer = (
        os.path.relpath(git_dir, worktree_root)
        if relative_pointer
        else str(git_dir)
    )
    (worktree_root / ".git").write_text(
        f"gitdir: {pointer}\n",
        encoding="utf-8",
    )
    return repository_root, worktree_root


def test_cli_override_does_not_modify_user_document(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.ensure()
    original = store.path.read_text(encoding="utf-8")
    session = ConfigSession(
        store,
        (
            parse_config_override('model_provider="openai-main"'),
            parse_config_override(
                'model_providers.openai-main.model="temporary-model"'
            ),
        ),
    )

    config = session.load()

    assert config["model"]["primary"]["model"] == "temporary-model"
    assert store.path.read_text(encoding="utf-8") == original


def test_user_hooks_json_keeps_file_source_identity(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    hook_file = _write_hook_file(tmp_path, "check-json")

    resolution = ConfigSession(store).resolve()

    assert [hook.handler.command for hook in resolution.hooks] == [
        "check-json",
    ]
    assert resolution.hooks[0].source_scope == "user"
    assert resolution.hooks[0].source_path == str(hook_file.resolve())
    assert resolution.hooks[0].key.startswith(f"{hook_file.resolve()}|command:")


def test_hook_layer_loads_json_before_inline_and_warns(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "hooks": [_command_handler("check-inline")],
        }],
    })
    hook_file = _write_hook_file(tmp_path, "check-json")

    resolution = ConfigSession(store).resolve()

    assert [hook.handler.command for hook in resolution.hooks] == [
        "check-json",
        "check-inline",
    ]
    assert [hook.source_path for hook in resolution.hooks] == [
        str(hook_file.resolve()),
        str(store.path.resolve()),
    ]
    warning = "\n".join(resolution.hook_warnings)
    assert "loading hooks from both" in warning
    assert str(hook_file.resolve()) in warning
    assert str(store.path.resolve()) in warning


def test_profile_does_not_reload_user_hooks_json(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    hook_file = _write_hook_file(tmp_path, "check-json")
    (tmp_path / "review.config.toml").write_text(
        "[[hooks.PostToolUse]]\n"
        'hooks = [{ type = "command", command = "audit-profile" }]\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, profile="review").resolve()

    assert [hook.handler.command for hook in resolution.hooks] == [
        "check-json",
        "audit-profile",
    ]
    assert [hook.source_scope for hook in resolution.hooks] == [
        "user",
        "profile",
    ]
    assert sum(
        hook.source_path == str(hook_file.resolve())
        for hook in resolution.hooks
    ) == 1


def test_invalid_hooks_json_warns_and_preserves_inline_hooks(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "hooks": [_command_handler("check-inline")],
        }],
    })
    hook_file = tmp_path / HOOKS_FILE_NAME
    hook_file.write_text("{broken", encoding="utf-8")

    resolution = ConfigSession(store).resolve()

    assert [hook.handler.command for hook in resolution.hooks] == [
        "check-inline",
    ]
    assert len(resolution.hook_warnings) == 1
    assert f"failed to parse hooks config {hook_file}" in (
        resolution.hook_warnings[0]
    )
    assert resolution.startup_warnings == ()


def test_hooks_json_schema_failure_rejects_entire_file(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    hook_file = tmp_path / HOOKS_FILE_NAME
    hook_file.write_text(
        json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "hooks": [{"type": "command"}],
                }],
                "PostToolUse": [{
                    "hooks": [_command_handler("valid-post")],
                }],
            },
        }),
        encoding="utf-8",
    )

    resolution = ConfigSession(store).resolve()

    assert resolution.hooks == ()
    assert len(resolution.hook_warnings) == 1
    assert (
        "hooks.PreToolUse[0].hooks[0].command must be a string"
        in resolution.hook_warnings[0]
    )
    assert resolution.startup_warnings == ()


def test_hooks_json_rejects_events_outside_hooks_object(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    hook_file = tmp_path / HOOKS_FILE_NAME
    hook_file.write_text(
        json.dumps({
            "SessionStart": [{
                "hooks": [_command_handler("prepare-session")],
            }],
        }),
        encoding="utf-8",
    )

    resolution = ConfigSession(store).resolve()

    assert resolution.hooks == ()
    assert len(resolution.hook_warnings) == 1
    assert "unknown field 'SessionStart'" in resolution.hook_warnings[0]


def test_empty_hooks_json_is_ignored_without_warning(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    (tmp_path / HOOKS_FILE_NAME).write_text(
        json.dumps({"description": "No lifecycle handlers"}),
        encoding="utf-8",
    )

    resolution = ConfigSession(store).resolve()

    assert resolution.hooks == ()
    assert resolution.hook_warnings == ()
    assert resolution.startup_warnings == ()


def test_dual_source_warning_uses_nonempty_event_sources(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [{
            "hooks": [_command_handler("check-inline")],
        }],
    })
    (tmp_path / HOOKS_FILE_NAME).write_text(
        json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "hooks": [{"type": "prompt"}],
                }],
            },
        }),
        encoding="utf-8",
    )

    resolution = ConfigSession(store).resolve()

    assert [
        (hook.handler.type, hook.handler.command)
        for hook in resolution.hooks
    ] == [
        ("command", "check-inline"),
    ]
    warnings = "\n".join(resolution.hook_warnings)
    assert "skipping unsupported prompt hook" in warnings
    assert "loading hooks from both" in warnings


def test_project_hooks_json_respects_directory_trust(tmp_path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".git").mkdir(parents=True)
    hook_file = _write_hook_file(
        project_root / PROJECT_CONFIG_DIR,
        "check-project-json",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=project_root)

    untrusted = session.resolve()
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    trusted = session.resolve()

    assert untrusted.hooks == ()
    assert [hook.handler.command for hook in trusted.hooks] == [
        "check-project-json",
    ]
    assert trusted.hooks[0].source_scope == "project"
    assert trusted.hooks[0].source_path == str(hook_file.resolve())


def test_untrusted_project_does_not_parse_invalid_hooks_json(tmp_path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".git").mkdir(parents=True)
    hook_file = project_root / PROJECT_CONFIG_DIR / HOOKS_FILE_NAME
    hook_file.parent.mkdir()
    hook_file.write_text("{broken", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=project_root)

    untrusted = session.resolve()
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    trusted = session.resolve()

    assert untrusted.hooks == ()
    assert untrusted.hook_warnings == ()
    assert len(trusted.hook_warnings) == 1
    assert f"failed to parse hooks config {hook_file}" in (
        trusted.hook_warnings[0]
    )


def test_linked_worktree_uses_root_hooks_json(tmp_path) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    _write_hook_file(
        worktree_root / PROJECT_CONFIG_DIR,
        "worktree-json",
    )
    repository_hook_file = _write_hook_file(
        repository_root / PROJECT_CONFIG_DIR,
        "repository-json",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    worktree = ConfigSession(store, workspace=worktree_root).resolve()
    main = ConfigSession(store, workspace=repository_root).resolve()

    assert [hook.handler.command for hook in worktree.hooks] == [
        "repository-json",
    ]
    assert worktree.hooks[0].source_path == str(repository_hook_file.resolve())
    assert worktree.hooks[0].key == main.hooks[0].key


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
    assert [hook.trust_policy for hook in resolution.hooks] == [
        "content_hash",
        "content_hash",
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
    _write_hook_file(tmp_path, "check-json")
    override = parse_config_override(
        "hooks={ PreToolUse = [{ hooks = [{ type = \"command\", "
        "command = \"check-cli\" }] }] }"
    )

    resolution = ConfigSession(store, (override,)).resolve()

    assert [definition.handler.command for definition in resolution.hooks] == [
        "check-cli",
    ]
    assert [definition.source_scope for definition in resolution.hooks] == ["cli"]
    assert [definition.trust_policy for definition in resolution.hooks] == [
        "content_hash",
    ]


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
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == project_root.resolve()
    assert resolution.project_trust.level == "trusted"
    assert resolution.project_trust.trusted
    assert len(resolution.hooks) == 1
    assert resolution.hooks[0].source_scope == "project"
    assert resolution.hooks[0].trust_policy == "content_hash"
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
        "Interrupt": (None, "notify", False),
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
        "server": None,
        "tool": None,
        "timeout": 2,
        "async": False,
        "additionalContextLimit": 2500,
        "input": {},
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


def test_discovery_retains_catalog_handlers_and_keeps_valid_hook(
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

    assert [
        (hook.handler.type, hook.handler.command)
        for hook in resolution.hooks
    ] == [
        ("command", "async-command"),
        ("command", "valid"),
    ]
    assert resolution.hooks[-1].key.endswith(":PreToolUse:0:5")
    warnings = "\n".join(resolution.hook_warnings)
    assert "skipping unsupported prompt hook" in warnings
    assert "skipping unsupported agent hook" in warnings
    assert "unsupported handler type 'custom'" in warnings
    assert "skipping empty hook command" in warnings
    assert "skipping async hook" not in warnings
    assert "ignoring unknown fields" in warnings
