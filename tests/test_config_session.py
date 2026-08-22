# -*- coding: utf-8 -*-

import json
import os

import pytest

from mind_core.config import (
    ConfigValidationError,
    config_override,
    normalize_config,
    parse_config_override,
)
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore, ConfigStoreError
from mind_core.config_layers import PROJECT_CONFIG_DIR
from mind_core.hook_discovery import HOOKS_FILE_NAME
from mind_core.hooks import HOOK_EVENT_CONFIG_SPECS


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


def test_unknown_config_field_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="config.typo"):
        normalize_config({"typo": True})


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
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == project_root.resolve()
    assert resolution.project_trust.level == trust_level
    assert resolution.project_trust.trusted is project_enabled
    assert ("project" in resolution.config["mcp_servers"]) is project_enabled
    assert bool(resolution.hooks) is project_enabled
    project_layers = [
        layer for layer in resolution.layers if layer.scope == "project"
    ]
    assert len(project_layers) == 1
    assert project_layers[0].enabled is project_enabled
    assert bool(project_layers[0].disabled_reason) is not project_enabled


@pytest.mark.parametrize(
    (
        "root_level",
        "nested_level",
        "loaded_names",
        "expected_depth",
    ),
    (
        ("trusted", None, ("root", "nested"), 4),
        ("trusted", "untrusted", ("root",), 2),
        ("untrusted", "trusted", ("nested",), 4),
    ),
)
def test_project_config_layers_apply_directory_trust_decisions(
    tmp_path,
    root_level,
    nested_level,
    loaded_names,
    expected_depth,
) -> None:
    project_root = tmp_path / "project"
    nested_root = project_root / "packages" / "sample"
    nested_root.mkdir(parents=True)
    (project_root / ".git").mkdir()
    config_paths = {}

    for name, directory, depth in (
        ("root", project_root, 2),
        ("nested", nested_root, 4),
    ):
        config_path = directory / PROJECT_CONFIG_DIR / "config.toml"
        config_path.parent.mkdir()
        config_path.write_text(
            "[agents]\n"
            f"max_depth = {depth}\n"
            f"[mcp_servers.{name}]\n"
            f'command = "{name}-server"\n'
            "[[hooks.PreToolUse]]\n"
            f'hooks = [{{ type = "command", command = "{name}-hook" }}]\n',
            encoding="utf-8",
        )
        config_paths[name] = config_path

    updates = {
        ("projects", str(project_root)): {"trust_level": root_level},
    }
    if nested_level is not None:
        updates[("projects", str(nested_root))] = {
            "trust_level": nested_level,
        }
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update(updates)

    resolution = ConfigSession(store, workspace=nested_root).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == (nested_level or root_level)
    assert resolution.project_trust.trust_root == (
        nested_root.resolve() if nested_level is not None else project_root.resolve()
    )
    assert resolution.config["agents"]["max_depth"] == expected_depth
    assert tuple(
        name
        for name in ("root", "nested")
        if name in resolution.config["mcp_servers"]
    ) == loaded_names
    assert tuple(
        definition.handler.command for definition in resolution.hooks
    ) == tuple(f"{name}-hook" for name in loaded_names)
    assert tuple(
        layer.path
        for layer in resolution.layers
        if layer.scope == "project" and layer.enabled
    ) == tuple(config_paths[name] for name in loaded_names)


def test_untrusted_nested_project_config_does_not_block_parent(tmp_path) -> None:
    project_root = tmp_path / "project"
    nested_root = project_root / "packages" / "sample"
    nested_root.mkdir(parents=True)
    (project_root / ".git").mkdir()
    root_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    root_config.parent.mkdir()
    root_config.write_text(
        "[agents]\nmax_depth = 2\n",
        encoding="utf-8",
    )
    nested_config = nested_root / PROJECT_CONFIG_DIR / "config.toml"
    nested_config.parent.mkdir()
    nested_config.write_text(
        'unknown_project_field = true\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    session = ConfigSession(store, workspace=nested_root)

    with pytest.raises(ConfigValidationError, match="unknown_project_field"):
        session.resolve()

    session.update_user({
        ("projects", str(nested_root), "trust_level"): "untrusted",
    })

    resolution = session.resolve()

    assert resolution.config["agents"]["max_depth"] == 2
    assert store.read_raw()["projects"][str(nested_root)] == {
        "trust_level": "untrusted",
    }
    assert [
        layer.path
        for layer in resolution.layers
        if layer.scope == "project" and layer.enabled
    ] == [root_config]


@pytest.mark.parametrize("relative_pointer", (False, True))
def test_repository_trust_enables_linked_worktree_automation(
    tmp_path,
    relative_pointer,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=relative_pointer,
    )
    workspace = worktree_root / "src"
    workspace.mkdir()
    worktree_config = worktree_root / PROJECT_CONFIG_DIR / "config.toml"
    worktree_config.parent.mkdir()
    worktree_config.write_text(
        "[agents]\n"
        "max_depth = 3\n"
        "[mcp_servers.project]\n"
        'command = "project-server"\n'
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "worktree-hook" }]\n',
        encoding="utf-8",
    )
    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text(
        "[agents]\n"
        "max_depth = 8\n"
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "repository-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == worktree_root.resolve()
    assert resolution.project_trust.trust_root == repository_root.resolve()
    assert resolution.project_trust.level == "trusted"
    assert resolution.config["agents"]["max_depth"] == 3
    assert resolution.config["mcp_servers"]["project"]["command"] == (
        "project-server"
    )
    assert len(resolution.hooks) == 1
    assert resolution.hooks[0].handler.command == "repository-hook"
    assert resolution.hooks[0].source_path == str(repository_config.resolve())

    main_resolution = ConfigSession(
        store,
        workspace=repository_root,
    ).resolve()
    assert resolution.hooks[0].key == main_resolution.hooks[0].key


def test_linked_worktree_hook_is_removed_when_root_source_is_missing(
    tmp_path,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    worktree_config = worktree_root / PROJECT_CONFIG_DIR / "config.toml"
    worktree_config.parent.mkdir()
    worktree_config.write_text(
        "[agents]\n"
        "max_depth = 3\n"
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "worktree-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_root).resolve()

    assert resolution.config["agents"]["max_depth"] == 3
    assert resolution.hooks == ()
    assert "PreToolUse" not in resolution.config["hooks"]


def test_linked_worktree_uses_root_hooks_without_local_config_file(
    tmp_path,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    worktree_config_dir = worktree_root / PROJECT_CONFIG_DIR
    worktree_config_dir.mkdir()
    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "repository-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_root).resolve()

    project_layers = tuple(
        layer for layer in resolution.layers
        if layer.scope == "project"
    )
    assert len(project_layers) == 1
    assert project_layers[0].path == worktree_config_dir / "config.toml"
    assert not project_layers[0].path.exists()
    assert [hook.handler.command for hook in resolution.hooks] == [
        "repository-hook",
    ]
    assert resolution.hooks[0].source_path == str(repository_config.resolve())


def test_linked_worktree_requires_local_project_config_directory(
    tmp_path,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "repository-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_root).resolve()

    assert resolution.hooks == ()
    assert [layer.scope for layer in resolution.layers] == ["user"]


def test_linked_worktree_maps_nested_project_hook_layers(tmp_path) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    worktree_child = worktree_root / "child"
    worktree_child.mkdir()

    for directory, depth, command in (
        (worktree_root, 2, "worktree-root"),
        (worktree_child, 4, "worktree-child"),
    ):
        config_path = directory / PROJECT_CONFIG_DIR / "config.toml"
        config_path.parent.mkdir()
        config_path.write_text(
            f"[agents]\nmax_depth = {depth}\n"
            "[[hooks.PreToolUse]]\n"
            f'hooks = [{{ type = "command", command = "{command}" }}]\n',
            encoding="utf-8",
        )

    repository_configs = (
        repository_root / PROJECT_CONFIG_DIR / "config.toml",
        repository_root / "child" / PROJECT_CONFIG_DIR / "config.toml",
    )
    for config_path, command in zip(
        repository_configs,
        ("repository-root", "repository-child"),
        strict=True,
    ):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "[[hooks.PreToolUse]]\n"
            f'hooks = [{{ type = "command", command = "{command}" }}]\n',
            encoding="utf-8",
        )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_child).resolve()

    assert resolution.config["agents"]["max_depth"] == 4
    assert [hook.handler.command for hook in resolution.hooks] == [
        "repository-root",
        "repository-child",
    ]
    assert [hook.source_path for hook in resolution.hooks] == [
        str(config_path.resolve())
        for config_path in repository_configs
    ]


def test_invalid_trusted_root_hook_config_fails_linked_worktree_load(
    tmp_path,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    (worktree_root / PROJECT_CONFIG_DIR).mkdir()
    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text("[hooks\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    with pytest.raises(ConfigStoreError, match="config is invalid"):
        ConfigSession(store, workspace=worktree_root).resolve()


def test_untrusted_worktree_does_not_parse_invalid_root_hook_config(
    tmp_path,
) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    (worktree_root / PROJECT_CONFIG_DIR).mkdir()
    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text("[hooks\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
        ("projects", str(worktree_root)): {"trust_level": "untrusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_root).resolve()

    project_layers = tuple(
        layer for layer in resolution.layers
        if layer.scope == "project"
    )
    assert len(project_layers) == 1
    assert not project_layers[0].enabled
    assert resolution.hooks == ()


def test_linked_worktree_decision_precedes_repository_decision(tmp_path) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
        ("projects", str(worktree_root)): {"trust_level": "untrusted"},
    })

    resolution = ConfigSession(store, workspace=worktree_root).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == worktree_root.resolve()
    assert resolution.project_trust.trust_root == worktree_root.resolve()
    assert resolution.project_trust.level == "untrusted"
    assert not resolution.project_trust.trusted


def test_linked_worktree_keeps_custom_project_config_root(tmp_path) -> None:
    repository_root, worktree_root = _linked_worktree(
        tmp_path,
        relative_pointer=False,
    )
    project_root = worktree_root / "packages" / "sample"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("", encoding="utf-8")
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        "[agents]\n"
        "max_depth = 4\n"
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "worktree-hook" }]\n',
        encoding="utf-8",
    )
    repository_config = (
        repository_root
        / "packages"
        / "sample"
        / PROJECT_CONFIG_DIR
        / "config.toml"
    )
    repository_config.parent.mkdir(parents=True)
    repository_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "repository-hook" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("project_root_markers",): ["pyproject.toml"],
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == repository_root.resolve()
    assert resolution.project_trust.level == "trusted"
    assert resolution.config["agents"]["max_depth"] == 4
    assert any(layer.path == project_config for layer in resolution.layers)
    assert [hook.handler.command for hook in resolution.hooks] == [
        "repository-hook",
    ]
    assert resolution.hooks[0].source_path == str(repository_config.resolve())


@pytest.mark.parametrize(
    "pointer_case",
    (
        "empty",
        "missing-prefix",
        "non-worktree",
        "wrong-common-dir",
        "multiline",
    ),
)
def test_invalid_worktree_pointer_falls_back_to_project_root(
    tmp_path,
    pointer_case,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    valid_git_dir = tmp_path / "repository" / ".git" / "worktrees" / "feature"
    pointer = {
        "empty": "gitdir: \n",
        "missing-prefix": str(valid_git_dir),
        "non-worktree": f"gitdir: {tmp_path / 'metadata' / 'feature'}\n",
        "wrong-common-dir": (
            f"gitdir: {tmp_path / 'repository' / 'metadata' / 'worktrees' / 'feature'}\n"
        ),
        "multiline": f"gitdir: {valid_git_dir}\nextra\n",
    }[pointer_case]
    (project_root / ".git").write_text(pointer, encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=project_root).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == project_root.resolve()
    assert resolution.project_trust.level == "trusted"


def test_invalid_worktree_pointer_does_not_override_custom_project_root(
    tmp_path,
) -> None:
    checkout_root = tmp_path / "checkout"
    project_root = checkout_root / "packages" / "sample"
    project_root.mkdir(parents=True)
    (checkout_root / ".git").write_text(
        f"gitdir: {tmp_path / 'metadata' / 'feature'}\n",
        encoding="utf-8",
    )
    (project_root / "pyproject.toml").write_text("", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("project_root_markers",): ["pyproject.toml"],
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=project_root).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == project_root.resolve()
    assert resolution.project_trust.level == "trusted"


@pytest.mark.parametrize(
    (
        "markers",
        "expected_project_root_name",
        "expected_trust_root_name",
        "expected_level",
    ),
    [
        (["pyproject.toml"], "project", "src", None),
        ([], "src", "src", "trusted"),
    ],
)
def test_project_root_markers_do_not_expand_the_active_trust_target(
    tmp_path,
    markers,
    expected_project_root_name,
    expected_trust_root_name,
    expected_level,
) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (project_root / "pyproject.toml").write_text("", encoding="utf-8")

    expected_project_root = (
        project_root
        if expected_project_root_name == "project"
        else workspace
    )
    expected_trust_root = (
        project_root
        if expected_trust_root_name == "project"
        else workspace
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("project_root_markers",): markers,
        ("projects", str(expected_project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == expected_project_root.resolve()
    assert resolution.project_trust.trust_root == expected_trust_root.resolve()
    assert resolution.project_trust.level == expected_level


def test_profile_project_trust_participates_in_the_active_snapshot(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text('[agents]\nmax_depth = 2\n', encoding="utf-8")

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
    assert resolution.project_trust.level == "trusted"
    assert resolution.project_trust.trusted
    assert resolution.config["agents"]["max_depth"] == 2
    assert any(
        layer.scope == "project" and layer.enabled
        for layer in resolution.layers
    )


def test_profile_project_decision_overrides_the_user_registry(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[agents]\nmax_depth = 3\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    ConfigStore(store.path.parent / "review.config.toml").update({
        ("projects", str(project_root)): {"trust_level": "untrusted"},
    })

    resolution = ConfigSession(
        store,
        profile="review",
        workspace=project_root,
    ).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "untrusted"
    assert resolution.config["agents"]["max_depth"] == 1
    assert all(
        layer.scope != "project" or not layer.enabled
        for layer in resolution.layers
    )


def test_cli_project_trust_is_ephemeral_and_enables_project_config(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[agents]\nmax_depth = 3\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.ensure()
    original = store.path.read_text(encoding="utf-8")
    override = config_override(
        ("projects", str(project_root), "trust_level"),
        "trusted",
    )

    resolution = ConfigSession(
        store,
        (override,),
        workspace=project_root,
    ).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "trusted"
    assert resolution.config["agents"]["max_depth"] == 3
    assert store.path.read_text(encoding="utf-8") == original


def test_cli_project_decision_overrides_persistent_trust(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[agents]\nmax_depth = 3\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    override = config_override(
        ("projects", str(project_root), "trust_level"),
        "untrusted",
    )

    resolution = ConfigSession(
        store,
        (override,),
        workspace=project_root,
    ).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "untrusted"
    assert resolution.config["agents"]["max_depth"] == 1
    assert store.read_raw()["projects"][str(project_root)] == {
        "trust_level": "trusted",
    }


def test_shadowed_project_trust_update_does_not_modify_user_config(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.ensure()
    original = store.path.read_text(encoding="utf-8")
    session = ConfigSession(
        store,
        (config_override(("projects",), {}),),
        workspace=project_root,
    )
    resolution = session.resolve()

    assert resolution.project_trust is not None
    with pytest.raises(ValueError, match="shadowed"):
        session.set_project_trust(resolution.project_trust, "trusted")

    assert store.path.read_text(encoding="utf-8") == original


def test_cli_project_root_markers_apply_before_project_discovery(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("", encoding="utf-8")
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[agents]\nmax_depth = 3\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    override = config_override(
        ("project_root_markers",),
        ["pyproject.toml"],
    )

    resolution = ConfigSession(
        store,
        (override,),
        workspace=workspace,
    ).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.config["agents"]["max_depth"] == 3


def test_invalid_project_config_prevents_persisting_trust(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        'unknown_project_field = true\n',
        encoding="utf-8",
    )

    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)
    session.resolve()
    original = store.path.read_text(encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="unknown_project_field"):
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
    project_config.write_text('[agents]\nmax_depth = 2\n', encoding="utf-8")

    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=workspace)

    trusted_config = session.update_user({
        ("projects", str(project_root), "trust_level"): "trusted",
    })

    trusted = session.resolve().project_trust
    assert trusted is not None
    assert trusted.level == "trusted"
    assert trusted_config["agents"]["max_depth"] == 2
    assert store.read_raw()["projects"][str(project_root)] == {
        "trust_level": "trusted",
    }

    untrusted_config = session.update_user({
        ("projects", str(project_root), "trust_level"): "untrusted",
    })

    untrusted = session.resolve().project_trust
    assert untrusted is not None
    assert untrusted.level == "untrusted"
    assert untrusted_config["agents"]["max_depth"] != 2


def test_user_config_is_not_reloaded_as_home_project_config(tmp_path) -> None:
    workspace = tmp_path / "home"
    (workspace / ".git").mkdir(parents=True)
    store = ConfigStore(workspace / PROJECT_CONFIG_DIR / "config.toml")
    session = ConfigSession(store, workspace=workspace)

    initial = session.resolve()
    assert initial.project_trust is not None
    assert initial.project_trust.project_root == workspace.resolve()
    assert initial.project_trust.trust_root == workspace.resolve()
    assert initial.project_trust.level is None

    session.update_user({
        ("projects", str(workspace), "trust_level"): "trusted",
    })
    resolution = session.resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "trusted"
    assert [layer.scope for layer in resolution.layers] == ["user"]
    assert resolution.layers[0].path == store.path


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


def test_project_trust_does_not_follow_a_configured_path_alias(tmp_path) -> None:
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
    assert resolution.project_trust.project_root == project_root.resolve()
    assert resolution.project_trust.trust_root == project_root.resolve()
    assert resolution.project_trust.level is None
    assert not resolution.project_trust.trusted


def test_exact_project_trust_key_precedes_equivalent_path_alias(tmp_path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    alias = tmp_path / "aaa-project-alias"
    try:
        alias.symlink_to(project_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink is unavailable: {error}")

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(alias)): {"trust_level": "untrusted"},
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.project_trust is not None
    assert resolution.project_trust.level == "trusted"
    assert resolution.project_trust.registry_key == str(project_root)


def test_trusted_project_config_ignores_ui_but_applies_exec_policy(
    tmp_path,
) -> None:
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
        'sandbox_mode = "read-only"\n'
        "[tui.keymap.global]\n"
        'open_transcript = "f12"\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.config["tui"]["keymap"]["global"] == {}
    assert resolution.config["sandbox_mode"] == "read-only"
    assert "tui" in resolution.startup_warnings[0]


def test_project_config_ignores_user_only_fields_with_startup_warning(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "src"
    workspace.mkdir(parents=True)
    (project_root / ".git").mkdir()

    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("service", "domain"): "https://user.example",
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        'model_provider = "project-provider"\n'
        'project_root_markers = ["unsafe.marker"]\n'
        '[service]\n'
        'domain = "https://project.example"\n'
        '[agents]\n'
        'max_depth = 3\n',
        encoding="utf-8",
    )

    resolution = ConfigSession(store, workspace=workspace).resolve()

    assert resolution.config["service"]["domain"] == "https://user.example"
    assert resolution.config["agents"]["max_depth"] == 3
    assert len(resolution.startup_warnings) == 1
    warning = resolution.startup_warnings[0]
    assert str(project_config) in warning
    assert "model_provider, project_root_markers, service" in warning


def test_project_config_sanitizes_restricted_fields_before_validation(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        'model_providers = "invalid-but-ignored"\n'
        'service = "invalid-but-ignored"\n'
        '[agents]\n'
        'max_depth = 2\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=project_root).resolve()

    assert resolution.config["agents"]["max_depth"] == 2
    assert "model_providers, service" in resolution.startup_warnings[0]


def test_untrusted_malformed_project_config_is_disabled_without_parsing(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("[broken\n", encoding="utf-8")
    store = ConfigStore(tmp_path / "home" / "config.toml")
    session = ConfigSession(store, workspace=project_root)

    resolution = session.resolve()

    project_layer = next(
        layer for layer in resolution.layers if layer.scope == "project"
    )
    assert not project_layer.enabled
    assert project_layer.disabled_reason

    original = store.path.read_text(encoding="utf-8")
    assert resolution.project_trust is not None
    with pytest.raises(ValueError, match="config is invalid"):
        session.set_project_trust(resolution.project_trust, "trusted")
    assert store.path.read_text(encoding="utf-8") == original


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
        ("prompt", None),
        ("command", "check-inline"),
    ]
    warnings = "\n".join(resolution.hook_warnings)
    assert "skipping prompt hook" not in warnings
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
        ("prompt", None),
        ("agent", None),
        ("command", "valid"),
    ]
    assert resolution.hooks[-1].key.endswith(":PreToolUse:0:5")
    warnings = "\n".join(resolution.hook_warnings)
    assert "skipping prompt hook" not in warnings
    assert "skipping agent hook" not in warnings
    assert "unsupported handler type 'custom'" in warnings
    assert "skipping empty hook command" in warnings
    assert "skipping async hook" in warnings
    assert "ignoring unknown fields" in warnings
