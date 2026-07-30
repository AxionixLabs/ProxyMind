# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.controller import Mind
from mind_app.runtime.hooks.catalog import HookCatalogStaleError
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.scope import HookExecutionContext
from mind_core.hook_trust import HookTrustStore
from mind_core.hooks import resolve_hook_definitions


def _definitions(source_path: Path):
    project = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "command": "check-project",
                "matcher": "shell_command",
            }],
        },
        source_scope="project",
        source_path=source_path,
    )[0]
    user = resolve_hook_definitions(
        {"PostToolUse": [{"command": "audit-user"}]},
        source_scope="user",
        source_path=source_path.parent / "user.toml",
    )[0]
    return project, user


def _controller(
    tmp_path: Path,
    definitions,
    *,
    registry: HookRegistry,
) -> Mind:
    controller = object.__new__(Mind)
    controller.history_workspace = str(tmp_path)
    controller.config_session = SimpleNamespace(
        resolve=Mock(return_value=SimpleNamespace(hooks=tuple(definitions))),
    )
    controller.hook_registry = registry
    return controller


def test_catalog_summarizes_registered_events_and_hook_details(tmp_path) -> None:
    project, user = _definitions(tmp_path / "project.toml")

    catalog = HookRegistry().inspect(
        (project, user),
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
    ]
    assert catalog.hooks[0].command == "check-project"
    assert catalog.hooks[0].matcher == "shell_command"
    assert catalog.hooks[0].trust_state == "untrusted"
    assert catalog.hooks[1].on_error == "continue"


def test_controller_inspection_uses_resolved_workspace(tmp_path) -> None:
    definitions = _definitions(tmp_path / "project.toml")
    controller = _controller(
        tmp_path,
        definitions,
        registry=HookRegistry(),
    )
    catalog = controller.inspect_hooks()

    assert catalog.workspace == str(tmp_path.resolve())
    controller.config_session.resolve.assert_called_once_with(
        workspace=tmp_path.resolve(),
    )


def test_controller_rejects_stale_hash_before_trusting_hook(tmp_path) -> None:
    source_path = tmp_path / "project.toml"
    original = _definitions(source_path)[0]
    changed = resolve_hook_definitions(
        {"PreToolUse": [{"command": "changed-project"}]},
        source_scope="project",
        source_path=source_path,
    )[0]
    trust_path = tmp_path / "hook-trust.json"
    controller = _controller(
        tmp_path,
        (changed,),
        registry=HookRegistry(
            trust_store=HookTrustStore(trust_path),
        ),
    )
    with pytest.raises(HookCatalogStaleError, match="content changed"):
        controller.set_hook_trust(
            changed.key,
            expected_content_hash=original.content_hash,
            trusted=True,
        )

    assert not trust_path.exists()

    catalog = controller.set_hook_trust(
        changed.key,
        expected_content_hash=changed.content_hash,
        trusted=True,
    )

    assert catalog.active_count == 1
    assert catalog.hooks[0].trust_state == "trusted"


def test_controller_builds_isolated_hook_scopes_for_config_snapshots(tmp_path) -> None:
    source_path = tmp_path / "user.toml"
    old = resolve_hook_definitions(
        {"PreToolUse": [{"command": "old"}]},
        source_scope="user",
        source_path=source_path,
    )
    new = resolve_hook_definitions(
        {"PreToolUse": [{"command": "new"}]},
        source_scope="user",
        source_path=source_path,
    )
    controller = _controller(tmp_path, old, registry=HookRegistry())
    controller.config_session.resolve.side_effect = [
        SimpleNamespace(hooks=old),
        SimpleNamespace(hooks=new),
    ]
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

    assert [item.command for item in old_scope.dispatcher.definitions] == ["old"]
    assert [item.command for item in new_scope.dispatcher.definitions] == ["new"]
    assert [item.command for item in old_scope.dispatcher.definitions] == ["old"]
    assert not hasattr(controller, "hooks")
