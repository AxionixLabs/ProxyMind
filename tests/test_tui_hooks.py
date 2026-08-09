# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.runtime.hooks.catalog import (
    HookCatalogEntry,
    HookCatalogSnapshot,
    HookCatalogStaleError,
    HookEventSummary
)
from mind_app.tui.features.hooks import (
    hook_detail_menu,
    hook_event_menu,
    manage_hooks,
)


class _Runtime(object):
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    async def select_menu(self, request):
        self.requests.append(request)
        return next(self.responses)


def _catalog(
    tmp_path: Path,
    *,
    trust_state: str,
    trust_policy: str = "content_hash",
    enabled: bool = True,
    warnings: tuple[str, ...] = (),
) -> HookCatalogSnapshot:
    active = enabled and trust_state in {"trusted", "managed"}
    entry = HookCatalogEntry(
        key="project:PreToolUse:0",
        event="PreToolUse",
        command="python check_hook.py",
        command_windows=None,
        status_message=None,
        matcher="shell_command",
        matcher_subject="tool_name",
        timeout_sec=5,
        run_async=False,
        additional_context_limit=2500,
        source_scope="project",
        source_path=str(tmp_path / ".codex" / "config.toml"),
        trust_policy=trust_policy,
        trust_state=trust_state,
        enabled=enabled,
        active=active,
        content_hash="sha256:" + "a" * 64,
    )
    return HookCatalogSnapshot(
        workspace=str(tmp_path),
        installed_count=1,
        active_count=int(active),
        events=(
            HookEventSummary(
                event="PreToolUse",
                description="Before a tool executes",
                matcher_subject="tool_name",
                control_policy="gate",
                installed_count=1,
                active_count=int(active),
            ),
            HookEventSummary(
                event="PostToolUse",
                description="After a tool executes",
                matcher_subject="tool_name",
                control_policy="notify",
                installed_count=0,
                active_count=0,
            ),
        ),
        hooks=(entry,),
        warnings=warnings,
    )


def test_hook_event_menu_localizes_descriptions_and_aligns_large_counts(
    tmp_path,
) -> None:
    catalog = HookCatalogSnapshot(
        workspace=str(tmp_path),
        installed_count=9999,
        active_count=9999,
        events=(
            HookEventSummary(
                event="PreToolUse",
                description="Before a tool executes",
                matcher_subject="tool_name",
                control_policy="gate",
                installed_count=9999,
                active_count=9999,
            ),
            HookEventSummary(
                event="PostToolUse",
                description="After a tool executes",
                matcher_subject="tool_name",
                control_policy="notify",
                installed_count=0,
                active_count=0,
            ),
        ),
    )

    menu = hook_event_menu(catalog)

    assert menu.options[0].detail == (
        "installed=9999 active=9999 | gate | "
        "coverage=client-full/server-approval-only | "
        "match=tool_name | 工具执行前"
    )
    assert menu.options[1].detail == (
        "installed=   0 active=   0 | notify | "
        "coverage=client-only | match=tool_name | 工具执行后"
    )


def test_hook_event_menu_shows_discovery_warnings(tmp_path) -> None:
    catalog = _catalog(
        tmp_path,
        trust_state="untrusted",
        warnings=("skipping empty hook command",),
    )

    menu = hook_event_menu(catalog)

    assert menu.body == ("Warning: skipping empty hook command",)


@pytest.mark.anyio
async def test_hooks_menu_trusts_the_inspected_hook_content(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="untrusted")
    updated = _catalog(tmp_path, trust_state="trusted")
    runtime = _Runtime([
        "PreToolUse",
        initial.hooks[0].key,
        "trust",
        None,
        None,
    ])
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=initial),
        trust_hook=Mock(return_value=updated),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await manage_hooks(runtime, mind)

    mind.trust_hook.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash="sha256:" + "a" * 64,
        workspace=tmp_path,
    )
    assert runtime.requests[0].title == "Hooks"
    assert runtime.requests[0].status == "installed=1 active=0"
    assert (
        "gate | coverage=client-full/server-approval-only | match=tool_name"
        in runtime.requests[0].options[0].detail
    )
    assert runtime.requests[1].title == "PreToolUse"
    assert runtime.requests[2].body[0] == "Command: python check_hook.py"
    assert runtime.requests[1].options[0].detail.endswith(
        "matcher[tool_name]=shell_command"
    )
    assert runtime.requests[2].body[3] == "Matcher (tool_name): shell_command"
    assert runtime.requests[2].body[4] == (
        "Coverage: client-full/server-approval-only"
    )
    assert [view.type for view in views] == ["tui.hooks.status", "tui.gap"]


@pytest.mark.anyio
async def test_hooks_menu_refreshes_after_stale_trust_request(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="untrusted")
    refreshed = _catalog(tmp_path, trust_state="trusted")
    runtime = _Runtime([
        "PreToolUse",
        initial.hooks[0].key,
        "trust",
        None,
        None,
    ])
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, refreshed]),
        trust_hook=Mock(
            side_effect=HookCatalogStaleError("hook content changed"),
        ),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await manage_hooks(runtime, mind)

    assert mind.inspect_hooks.call_count == 2
    assert runtime.requests[3].status == "installed=1 active=1"
    assert [view.type for view in views] == ["tui.hooks.failure", "tui.gap"]
    assert "".join(
        text for _style, text in views[0].renderable.fragments
    ) == "/hooks · Failed · hook content changed"


def test_hook_detail_menu_separates_trust_enabled_and_managed_states(
    tmp_path,
) -> None:
    untrusted = _catalog(tmp_path, trust_state="untrusted").hooks[0]
    trusted = _catalog(tmp_path, trust_state="trusted").hooks[0]
    disabled = _catalog(
        tmp_path,
        trust_state="trusted",
        enabled=False,
    ).hooks[0]
    managed = _catalog(
        tmp_path,
        trust_state="managed",
        trust_policy="managed",
    ).hooks[0]

    assert hook_detail_menu(untrusted).options[1].label == "Trust hook"
    assert hook_detail_menu(trusted).options[1].label == "Disable hook"
    assert hook_detail_menu(disabled).options[1].label == "Enable hook"
    assert hook_detail_menu(disabled).status == "PreToolUse | disabled"
    assert hook_detail_menu(managed).options == ()
    assert "Enabled: true" in hook_detail_menu(managed).body
