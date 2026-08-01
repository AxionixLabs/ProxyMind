# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.controller import Mind
from mind_app.runtime.hooks.catalog import HookCatalogStaleError
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.scope import HookExecutionContext
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.hook_discovery import resolve_hook_definitions


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
        ("UserPromptSubmit", 0, 0, "When the user submits a prompt"),
        ("SubagentStart", 0, 0, "When a subagent is created"),
        ("SubagentStop", 0, 0, "Right before a subagent ends its turn"),
        ("Stop", 0, 0, "Right before Codex ends its turn"),
        ("SessionEnd", 0, 0, "When a root session ends"),
    ]
    assert catalog.hooks[0].command == "check-project"
    assert catalog.hooks[0].matcher == "shell_command"
    assert catalog.hooks[0].trust_state == "untrusted"
    assert catalog.hooks[0].enabled
    assert catalog.hooks[1].trust_state == "trusted"
    assert catalog.hooks[1].active
    assert catalog.warnings == ("skipping invalid hook",)


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
        mode="chat",
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
