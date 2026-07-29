# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from mind_app.runtime.hooks.runtime import HookRuntime
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
    runtime = HookRuntime((definition,), trust_store=store)

    assert runtime.installed_count == 1
    assert runtime.active_count == 0
    assert runtime.status().hooks[0].trust_state == "untrusted"

    store.trust(definition)
    status = runtime.refresh_trust()

    assert status.active_count == 1
    assert status.hooks[0].trust_state == "trusted"

    changed = _project_definition(
        tmp_path / "config.toml",
        command="check-changed-project",
    )
    status = runtime.reload((changed,))

    assert status.active_count == 0
    assert status.hooks[0].trust_state == "untrusted"

    runtime.reload((definition,))
    store.revoke(definition)
    status = runtime.refresh_trust()

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
