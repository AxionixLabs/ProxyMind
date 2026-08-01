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


def _catalog(tmp_path: Path, *, trusted: bool) -> HookCatalogSnapshot:
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
        on_error="block",
        source_scope="project",
        source_path=str(tmp_path / ".codex" / "config.toml"),
        trust_state="trusted" if trusted else "untrusted",
        active=trusted,
        content_hash="a" * 64,
    )
    return HookCatalogSnapshot(
        workspace=str(tmp_path),
        installed_count=1,
        active_count=int(trusted),
        events=(
            HookEventSummary(
                event="PreToolUse",
                description="Before a tool executes",
                matcher_subject="tool_name",
                control_policy="gate",
                installed_count=1,
                active_count=int(trusted),
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
        "installed=9999 active=9999 | gate | match=tool_name | 工具执行前"
    )
    assert menu.options[1].detail == (
        "installed=   0 active=   0 | notify | match=tool_name | 工具执行后"
    )


@pytest.mark.anyio
async def test_hooks_menu_trusts_the_inspected_hook_content(tmp_path) -> None:
    initial = _catalog(tmp_path, trusted=False)
    updated = _catalog(tmp_path, trusted=True)
    runtime = _Runtime([
        "PreToolUse",
        initial.hooks[0].key,
        True,
        None,
        None,
    ])
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=initial),
        set_hook_trust=Mock(return_value=updated),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await manage_hooks(runtime, mind)

    mind.set_hook_trust.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash="a" * 64,
        trusted=True,
        workspace=tmp_path,
    )
    assert runtime.requests[0].title == "Hooks"
    assert runtime.requests[0].status == "installed=1 active=0"
    assert "gate | match=tool_name" in runtime.requests[0].options[0].detail
    assert runtime.requests[1].title == "PreToolUse"
    assert runtime.requests[2].body[0] == "Command: python check_hook.py"
    assert runtime.requests[1].options[0].detail.endswith(
        "matcher[tool_name]=shell_command"
    )
    assert runtime.requests[2].body[3] == "Matcher (tool_name): shell_command"
    assert [view.type for view in views] == ["tui.hooks.status", "tui.gap"]


@pytest.mark.anyio
async def test_hooks_menu_refreshes_after_stale_trust_request(tmp_path) -> None:
    initial = _catalog(tmp_path, trusted=False)
    refreshed = _catalog(tmp_path, trusted=True)
    runtime = _Runtime([
        "PreToolUse",
        initial.hooks[0].key,
        True,
        None,
        None,
    ])
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, refreshed]),
        set_hook_trust=Mock(
            side_effect=HookCatalogStaleError("hook content changed"),
        ),
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
