# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from metadata import const

from mind_app.controller import Mind
from mind_app.runtime.hooks.catalog import HookCatalogStaleError
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.scope import HookExecutionContext
from infrastructure.config.layers import PROJECT_CONFIG_DIR
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.application import HOOK_EVENT_NAMES


def _hook(command, *, matcher=None):
    config = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        config["matcher"] = matcher
    return config


def _definitions(source_path: Path):
    project = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{
                    "type": "command",
                    "command": "check-project",
                }],
                "matcher": "shell_command",
            }],
        },
        source_scope="project",
        source_path=source_path,
    )[0]
    user = resolve_hook_definitions(
        {"PostToolUse": [_hook("audit-user")]},
        source_scope="user",
        source_path=source_path.parent / "user.toml",
    )[0]
    return project, user


def _controller(tmp_path: Path, config_session) -> Mind:
    controller = object.__new__(Mind)
    controller.history_workspace = str(tmp_path)
    controller.config_session = config_session
    controller.hook_registry = HookRegistry()
    return controller


def _user_controller(tmp_path: Path) -> tuple[Mind, ConfigStore]:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [_hook("check-user")],
    })
    return _controller(tmp_path, ConfigSession(store)), store


def test_catalog_summarizes_registered_events_and_hook_details(tmp_path) -> None:
    project, user = _definitions(tmp_path / "project.toml")

    catalog = HookRegistry().inspect(
        (project, user),
        hook_states={
            user.key: {"trusted_hash": user.content_hash},
        },
        warnings=("skipping invalid hook",),
        workspace=tmp_path,
    )

    assert catalog.installed_count == 2
    assert catalog.active_count == 1
    assert [
        (
            item.event,
            item.installed_count,
            item.active_count,
            item.description,
        )
        for item in catalog.events
    ] == [
        ("PreToolUse", 1, 0, "Before a tool executes"),
        ("PermissionRequest", 0, 0, "When permission is requested"),
        ("PostToolUse", 1, 1, "After a tool executes"),
        ("PreCompact", 0, 0, "Before context compaction"),
        ("PostCompact", 0, 0, "After context compaction"),
        ("SessionStart", 0, 0, "When a new session starts"),
        ("SessionEnd", 0, 0, "Right before a session ends"),
        ("UserPromptSubmit", 0, 0, "When the user submits a prompt"),
        ("SubagentStart", 0, 0, "When a subagent is created"),
        ("SubagentStop", 0, 0, "Right before a subagent ends its turn"),
        ("Stop", 0, 0, f"Right before {const.APP_DESC} ends its turn"),
    ]
    assert catalog.hooks[0].command == "check-project"
    assert catalog.hooks[0].matcher == "shell_command"
    assert catalog.hooks[0].trust_policy == "content_hash"
    assert catalog.hooks[0].trust_state == "untrusted"
    assert catalog.hooks[0].enabled
    assert catalog.hooks[1].trust_state == "trusted"
    assert catalog.hooks[1].active
    assert [hook.display_order for hook in catalog.hooks] == [0, 1]
    assert catalog.events[0].review_count == 1
    assert catalog.events[2].review_count == 0
    assert catalog.warnings == ("skipping invalid hook",)


def test_hook_event_names_use_the_stable_codex_order() -> None:
    assert HOOK_EVENT_NAMES == (
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    )


@pytest.mark.parametrize(
    ("trust_policy", "state", "enabled", "needs_review", "trusted", "toggleable", "active"),
    [
        ("managed", {}, True, False, True, False, True),
        ("content_hash", {}, True, True, False, False, False),
        ("content_hash", {"trusted_hash": "sha256:" + "0" * 64}, True, True, False, False, False),
        ("content_hash", {"trusted_hash": "PLACEHOLDER"}, True, False, True, True, True),
        ("content_hash", {"trusted_hash": "PLACEHOLDER", "enabled": False}, False, False, True, True, False),
    ],
)
def test_catalog_entry_exposes_unified_hook_state(
    tmp_path,
    trust_policy,
    state,
    enabled,
    needs_review,
    trusted,
    toggleable,
    active,
) -> None:
    definition = resolve_hook_definitions(
        {"PreToolUse": [_hook("state-hook")]},
        source_scope="user",
        source_path=tmp_path / "state.toml",
        trust_policy=trust_policy,
    )[0]
    if state.get("trusted_hash") == "PLACEHOLDER":
        state = {**state, "trusted_hash": definition.content_hash}

    entry = HookRegistry().inspect(
        (definition,),
        hook_states={definition.key: state},
        workspace=tmp_path,
    ).hooks[0]

    assert entry.stable_key == definition.key
    assert entry.enabled is enabled
    assert entry.needs_review is needs_review
    assert entry.trusted is trusted
    assert entry.toggleable is toggleable
    assert entry.computed_active is active
    assert entry.active is active


def test_controller_inspection_uses_resolved_workspace(tmp_path) -> None:
    definitions = _definitions(tmp_path / "project.toml")
    resolution = SimpleNamespace(
        hooks=definitions,
        hook_states={},
        hook_warnings=(),
    )
    config_session = SimpleNamespace(
        resolve=Mock(return_value=resolution),
    )
    controller = _controller(tmp_path, config_session)

    catalog = controller.inspect_hooks()

    assert catalog.workspace == str(tmp_path.resolve())
    config_session.resolve.assert_called_once_with(
        workspace=tmp_path.resolve(),
    )


def test_controller_rejects_stale_hash_then_persists_trust(tmp_path) -> None:
    controller, store = _user_controller(tmp_path)
    original = controller.inspect_hooks().hooks[0]
    store.update({
        ("hooks", "PreToolUse"): [_hook("changed-user")],
    })
    changed = controller.inspect_hooks().hooks[0]

    with pytest.raises(HookCatalogStaleError, match="content changed"):
        controller.trust_hook(
            changed.key,
            expected_content_hash=original.content_hash,
        )

    catalog = controller.trust_hook(
        changed.key,
        expected_content_hash=changed.content_hash,
    )

    assert catalog.active_count == 1
    assert catalog.hooks[0].trust_state == "trusted"
    state = store.read_raw()["hooks"]["state"][changed.key]
    assert state == {"trusted_hash": changed.content_hash}


def test_controller_validates_all_hashes_before_batch_trust(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({
        ("hooks", "PreToolUse"): [_hook("check-user")],
        ("hooks", "PostToolUse"): [_hook("audit-user")],
    })
    controller = _controller(tmp_path, ConfigSession(store))
    first, second = controller.inspect_hooks().hooks

    with pytest.raises(HookCatalogStaleError, match="content changed"):
        controller.trust_hooks((
            (first.key, first.content_hash),
            (second.key, "sha256:stale"),
        ))

    assert "state" not in store.read_raw()["hooks"]

    trusted = controller.trust_hooks((
        (first.key, first.content_hash),
        (second.key, second.content_hash),
    ))

    assert trusted.active_count == 2
    assert all(item.trust_state == "trusted" for item in trusted.hooks)


def test_controller_trusts_json_and_inline_hooks_independently(tmp_path) -> None:
    controller, store = _user_controller(tmp_path)
    hook_file = tmp_path / "hooks.json"
    hook_file.write_text(
        '{"hooks":{"PreToolUse":[{"hooks":['
        '{"type":"command","command":"check-json"}]}]}}',
        encoding="utf-8",
    )

    initial = controller.inspect_hooks()
    json_hook, inline_hook = initial.hooks
    updated = controller.trust_hook(
        json_hook.key,
        expected_content_hash=json_hook.content_hash,
    )

    assert [hook.command for hook in initial.hooks] == [
        "check-json",
        "check-user",
    ]
    assert json_hook.source_path == str(hook_file.resolve())
    assert json_hook.key != inline_hook.key
    assert updated.active_count == 1
    assert [hook.trust_state for hook in updated.hooks] == [
        "trusted",
        "untrusted",
    ]
    assert store.read_raw()["hooks"]["state"] == {
        json_hook.key: {"trusted_hash": json_hook.content_hash},
    }


def test_controller_preserves_disabled_state_when_retrusting_modified_hook(
    tmp_path,
) -> None:
    controller, store = _user_controller(tmp_path)
    original = controller.inspect_hooks().hooks[0]
    controller.trust_hook(
        original.key,
        expected_content_hash=original.content_hash,
    )
    disabled = controller.set_hook_enabled(
        original.key,
        expected_content_hash=original.content_hash,
        enabled=False,
    ).hooks[0]

    assert disabled.trust_state == "trusted"
    assert not disabled.enabled
    assert not disabled.active

    store.update({
        ("hooks", "PreToolUse"): [_hook("changed-user")],
    })
    modified = controller.inspect_hooks().hooks[0]

    assert modified.trust_state == "modified"
    assert not modified.enabled

    retrusted = controller.trust_hook(
        modified.key,
        expected_content_hash=modified.content_hash,
    ).hooks[0]

    assert retrusted.trust_state == "trusted"
    assert not retrusted.enabled
    assert not retrusted.active


def test_controller_rejects_managed_hook_state_changes(tmp_path) -> None:
    definition = resolve_hook_definitions(
        {"PreToolUse": [_hook("managed-check")]},
        source_scope="managed",
        source_path=tmp_path / "managed.toml",
        trust_policy="managed",
    )[0]
    config_session = SimpleNamespace(
        resolve=Mock(return_value=SimpleNamespace(
            hooks=(definition,),
            hook_states={},
        )),
        update_user=Mock(),
    )
    controller = _controller(tmp_path, config_session)

    with pytest.raises(ValueError, match="managed hook trust"):
        controller.trust_hook(
            definition.key,
            expected_content_hash=definition.content_hash,
        )
    with pytest.raises(ValueError, match="managed hook enabled"):
        controller.set_hook_enabled(
            definition.key,
            expected_content_hash=definition.content_hash,
            enabled=False,
        )

    config_session.update_user.assert_not_called()


def test_trusted_project_layer_does_not_bypass_hook_content_trust(
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    (project_root / ".git").mkdir(parents=True)
    project_config = project_root / PROJECT_CONFIG_DIR / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "project-check" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(project_root)): {"trust_level": "trusted"},
    })

    resolution = ConfigSession(store, workspace=project_root).resolve()
    status = HookRegistry().build(
        resolution.hooks,
        hook_states=resolution.hook_states,
    ).status()

    assert status.installed_count == 1
    assert status.active_count == 0
    assert status.hooks[0].source_scope == "project"
    assert status.hooks[0].trust_policy == "content_hash"
    assert status.hooks[0].trust_state == "untrusted"


def test_exec_hook_trust_bypass_runs_enabled_untrusted_hooks(tmp_path) -> None:
    definition = resolve_hook_definitions(
        {"SessionStart": [_hook("check-startup")]},
        source_scope="user",
        source_path=tmp_path / "config.toml",
    )[0]

    status = HookRegistry(bypass_hook_trust=True).build(
        (definition,),
        hook_states={},
    ).status()

    assert status.active_count == 1
    assert status.hooks[0].trust_state == "untrusted"
    assert status.hooks[0].enabled
    assert status.hooks[0].active

    disabled = HookRegistry(bypass_hook_trust=True).build(
        (definition,),
        hook_states={definition.key: {"enabled": False}},
    ).status()

    assert disabled.active_count == 0
    assert not disabled.hooks[0].enabled
    assert not disabled.hooks[0].active


def test_exec_mcp_hook_warning_respects_trust_and_enabled_state(tmp_path) -> None:
    definition = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [{
            "type": "mcp_tool",
            "server": "files",
            "tool": "read",
        }]}]},
        source_scope="user",
        source_path=tmp_path / "config.toml",
    )[0]

    assert HookRegistry().startup_warnings((definition,)) == ()
    assert HookRegistry(bypass_hook_trust=True).startup_warnings(
        (definition,),
    ) == (
        f"skipping MCP tool hook in {definition.source_path}: "
        "MCP invocation is not available yet",
    )
    assert HookRegistry(bypass_hook_trust=True).startup_warnings(
        (definition,),
        hook_states={definition.key: {"enabled": False}},
    ) == ()


def test_controller_reuses_root_hook_trust_across_linked_worktree(
    tmp_path,
) -> None:
    repository_root = tmp_path / "repository"
    git_dir = repository_root / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    (worktree_root / ".git").write_text(
        f"gitdir: {git_dir}\n",
        encoding="utf-8",
    )
    (worktree_root / PROJECT_CONFIG_DIR).mkdir()

    repository_config = repository_root / PROJECT_CONFIG_DIR / "config.toml"
    repository_config.parent.mkdir()
    repository_config.write_text(
        "[[hooks.PreToolUse]]\n"
        'hooks = [{ type = "command", command = "project-check" }]\n',
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path / "home" / "config.toml")
    store.update({
        ("projects", str(repository_root)): {"trust_level": "trusted"},
    })
    controller = _controller(
        worktree_root,
        ConfigSession(store, workspace=worktree_root),
    )

    initial = controller.inspect_hooks().hooks[0]
    trusted = controller.trust_hook(
        initial.key,
        expected_content_hash=initial.content_hash,
    ).hooks[0]
    main = controller.inspect_hooks(workspace=repository_root).hooks[0]

    assert initial.source_path == str(repository_config.resolve())
    assert initial.trust_state == "untrusted"
    assert trusted.key == main.key == initial.key
    assert trusted.trust_state == main.trust_state == "trusted"
    assert trusted.active and main.active
    assert store.read_raw()["hooks"]["state"][initial.key] == {
        "trusted_hash": initial.content_hash,
    }


def test_controller_builds_isolated_hook_scopes_for_config_snapshots(tmp_path) -> None:
    source_path = tmp_path / "user.toml"
    old = resolve_hook_definitions(
        {"PreToolUse": [_hook("old")]},
        source_scope="user",
        source_path=source_path,
    )
    new = resolve_hook_definitions(
        {"PreToolUse": [_hook("new")]},
        source_scope="user",
        source_path=source_path,
    )
    config_session = SimpleNamespace(resolve=Mock(side_effect=[
        SimpleNamespace(
            hooks=old,
            hook_states={old[0].key: {"trusted_hash": old[0].content_hash}},
            hook_warnings=(),
        ),
        SimpleNamespace(
            hooks=new,
            hook_states={new[0].key: {"trusted_hash": new[0].content_hash}},
            hook_warnings=(),
        ),
    ]))
    controller = _controller(tmp_path, config_session)
    context = HookExecutionContext(
        session_id="session",
        conversation_id="conversation",
        turn_id="turn",
        cwd=str(tmp_path),
        model="model",
        source="test",
        sandbox_mode="workspace-write",
        permission_mode="on-request",
        agent_id="root",
        agent_type="root",
        agent_depth=0,
    )

    old_scope = controller.hook_scope(context)
    new_scope = controller.hook_scope(context)

    assert [item.handler.command for item in old_scope.dispatcher.definitions] == [
        "old",
    ]
    assert [item.handler.command for item in new_scope.dispatcher.definitions] == [
        "new",
    ]
    assert [item.handler.command for item in old_scope.dispatcher.definitions] == [
        "old",
    ]
    assert not hasattr(controller, "hooks")
