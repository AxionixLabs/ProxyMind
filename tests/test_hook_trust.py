# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from agent.harness.hooks.registry import HookRegistry
from infrastructure.hooks.discovery import resolve_hook_definitions


def _definition(
    source_path: Path | None,
    *,
    source_scope: str,
    trust_policy: str = "content_hash",
    command: str = "check-hook",
):
    return resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": command}],
            }],
        },
        source_scope=source_scope,
        source_path=source_path,
        trust_policy=trust_policy,
    )[0]


@pytest.mark.parametrize(
    "source_scope",
    ["user", "profile", "project", "cli", "plugin"],
)
def test_non_managed_hooks_are_untrusted_by_default(
    tmp_path,
    source_scope,
) -> None:
    definition = _definition(
        tmp_path / f"{source_scope}.toml",
        source_scope=source_scope,
    )

    status = HookRegistry().build((definition,)).status()

    assert status.active_count == 0
    assert status.hooks[0].trust_policy == "content_hash"
    assert status.hooks[0].trust_state == "untrusted"
    assert status.hooks[0].enabled
    assert not status.hooks[0].active


def test_managed_hook_is_always_enabled_and_active(tmp_path) -> None:
    definition = _definition(
        tmp_path / "managed.toml",
        source_scope="managed",
        trust_policy="managed",
    )

    status = HookRegistry().build(
        (definition,),
        hook_states={
            definition.key: {
                "enabled": False,
                "trusted_hash": "sha256:" + "0" * 64,
            },
        },
    ).status()

    assert status.active_count == 1
    assert status.hooks[0].trust_policy == "managed"
    assert status.hooks[0].trust_state == "managed"
    assert status.hooks[0].enabled
    assert status.hooks[0].active


def test_source_scope_does_not_grant_managed_trust_policy(tmp_path) -> None:
    definition = _definition(
        tmp_path / "managed.toml",
        source_scope="managed",
    )

    status = HookRegistry().build((definition,)).status()

    assert status.active_count == 0
    assert status.hooks[0].trust_policy == "content_hash"
    assert status.hooks[0].trust_state == "untrusted"


def test_hook_requires_its_exact_trusted_content_hash(tmp_path) -> None:
    source_path = tmp_path / "project.toml"
    definition = _definition(source_path, source_scope="project")
    states = {
        definition.key: {"trusted_hash": definition.content_hash},
    }

    trusted = HookRegistry().build(
        (definition,),
        hook_states=states,
    ).status()

    assert trusted.active_count == 1
    assert trusted.hooks[0].trust_state == "trusted"

    changed = _definition(
        source_path,
        source_scope="project",
        command="check-changed-hook",
    )
    modified = HookRegistry().build(
        (changed,),
        hook_states=states,
    ).status()

    assert changed.key == definition.key
    assert modified.active_count == 0
    assert modified.hooks[0].trust_state == "modified"
    assert modified.hooks[0].enabled


def test_enabled_state_is_independent_from_trust(tmp_path) -> None:
    definition = _definition(
        tmp_path / "user.toml",
        source_scope="user",
    )
    registry = HookRegistry()

    trusted = registry.build(
        (definition,),
        hook_states={
            definition.key: {
                "enabled": False,
                "trusted_hash": definition.content_hash,
            },
        },
    ).status().hooks[0]
    untrusted = registry.build(
        (definition,),
        hook_states={definition.key: {"enabled": False}},
    ).status().hooks[0]

    assert (trusted.trust_state, trusted.enabled, trusted.active) == (
        "trusted",
        False,
        False,
    )
    assert (untrusted.trust_state, untrusted.enabled, untrusted.active) == (
        "untrusted",
        False,
        False,
    )
