# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.controller import Mind
from mind_app.runtime.hooks.catalog import HookCatalogStaleError
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.runtime import HookRuntime
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
    controller.hooks = HookRuntime.empty()
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
        ("PostToolUse", 1, 1, "After a tool executes"),
    ]
    assert catalog.hooks[0].command == "check-project"
    assert catalog.hooks[0].matcher == "shell_command"
    assert catalog.hooks[0].trust_state == "untrusted"
    assert catalog.hooks[1].on_error == "continue"


def test_controller_inspection_does_not_replace_last_turn_runtime(tmp_path) -> None:
    definitions = _definitions(tmp_path / "project.toml")
    controller = _controller(
        tmp_path,
        definitions,
        registry=HookRegistry(),
    )
    previous = controller.hooks

    catalog = controller.inspect_hooks()

    assert catalog.workspace == str(tmp_path.resolve())
    assert controller.hooks is previous
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
    previous = controller.hooks

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
    assert controller.hooks is previous
