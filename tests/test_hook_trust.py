# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from mind_app.runtime.hooks.registry import HookRegistry
from mind_core.hook_trust import (
    HookTrustStore,
    HookTrustStoreError
)
from mind_core.hooks import resolve_hook_definitions


def _project_definition(
    source_path: Path,
    *,
    command: str = "check-project",
    enabled: bool = True,
):
    return resolve_hook_definitions(
        {
            "PreToolUse": [{
                "command": command,
                "enabled": enabled,
            }],
        },
        source_scope="project",
        source_path=source_path,
    )[0]


def test_project_hook_requires_exact_persisted_content_hash(tmp_path) -> None:
    definition = _project_definition(tmp_path / "config.toml")
    store = HookTrustStore(tmp_path / "hook-trust.json")
    registry = HookRegistry(trust_store=store)
    runtime = registry.build((definition,))

    assert runtime.installed_count == 1
    assert runtime.active_count == 0
    assert runtime.status().hooks[0].trust_state == "untrusted"

    registry.trust(definition)
    status = registry.build((definition,)).status()

    assert status.active_count == 1
    assert status.hooks[0].trust_state == "trusted"

    changed = _project_definition(
        tmp_path / "config.toml",
        command="check-changed-project",
    )
    status = registry.build((changed,)).status()

    assert status.active_count == 0
    assert status.hooks[0].trust_state == "untrusted"

    registry.revoke(definition)
    status = registry.build((definition,)).status()

    assert status.active_count == 0
    assert status.hooks[0].trust_state == "untrusted"


def test_enabled_toggle_does_not_change_hook_content_hash(tmp_path) -> None:
    enabled = _project_definition(tmp_path / "config.toml", enabled=True)
    disabled = _project_definition(tmp_path / "config.toml", enabled=False)

    assert enabled.content_hash == disabled.content_hash


def test_hook_trust_store_rejects_malformed_document(tmp_path) -> None:
    path = tmp_path / "hook-trust.json"
    path.write_text('{"version": 1, "trusted": []}', encoding="utf-8")

    with pytest.raises(HookTrustStoreError, match="records are invalid"):
        HookTrustStore(path).load()


def test_registry_fails_closed_for_project_hooks_when_trust_is_invalid(
    tmp_path,
) -> None:
    path = tmp_path / "hook-trust.json"
    path.write_text('{"version": 1, "trusted": []}', encoding="utf-8")
    project = _project_definition(tmp_path / "project.toml")
    user = resolve_hook_definitions(
        {"PreToolUse": [{"command": "check-user"}]},
        source_scope="user",
        source_path=tmp_path / "user.toml",
    )[0]

    runtime = HookRegistry(
        trust_store=HookTrustStore(path),
    ).build((project, user))
    status = runtime.status()

    assert status.active_count == 1
    assert status.trust_error
    assert [item.active for item in status.hooks] == [False, True]
    assert [item.command for item in runtime.definitions] == ["check-user"]
