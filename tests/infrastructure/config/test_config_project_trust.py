# -*- coding: utf-8 -*-

"""验证项目与 linked worktree 的信任决策和配置分层。"""


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
