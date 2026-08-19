# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from dataclasses import replace
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
from mind_app.tui.core.models import (
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.runtime import TuiRuntime


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


async def _wait_for_menu(runtime: TuiRuntime, title: str) -> None:
    for _ in range(100):
        state = runtime.screen.menu.state
        if state is not None and state.request.title == title:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"menu did not open: {title}")


async def _wait_for_call(mock: Mock) -> None:
    for _ in range(100):
        if mock.call_count:
            return None
        await asyncio.sleep(0)
    raise AssertionError("background menu action did not run")


def test_hook_event_menu_localizes_descriptions_and_summarizes_counts(
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

    assert menu.options[0].detail == "9999/9999 active · 工具执行前"
    assert menu.options[1].detail == "0/0 active · 工具执行后"
    assert menu.view_id == "hooks:events"
    assert menu.help_text == ""
    assert menu.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        menu.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
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
    runtime = TuiRuntime()
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, updated]),
        trust_hook=Mock(return_value=updated),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "Hook Details")
    runtime.screen.menu._choose_index(1)
    await _wait_for_call(mind.trust_hook)

    mind.trust_hook.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash="sha256:" + "a" * 64,
        workspace=tmp_path,
    )
    root = runtime.screen.menu._menu_views()[0].state.request
    assert root.title == "Hooks"
    assert root.status == "installed=1 active=1"
    assert root.options[0].detail == (
        "1/1 active · 工具执行前"
    )
    current = runtime.screen.menu.state
    assert current is not None
    assert current.request.title == "PreToolUse"
    assert current.request.options[0].detail.endswith(
        "matcher[tool_name]=shell_command"
    )
    assert [view.type for view in views] == ["tui.hooks.status", "tui.gap"]
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
async def test_hooks_menu_refreshes_after_stale_trust_request(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="untrusted")
    runtime = TuiRuntime()
    views = []
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=initial),
        trust_hook=Mock(
            side_effect=HookCatalogStaleError("hook content changed"),
        ),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "Hook Details")
    runtime.screen.menu._choose_index(1)
    await _wait_for_menu(runtime, "Hook operation")

    assert mind.inspect_hooks.call_count == 1
    assert [view.type for view in views] == []
    state = runtime.screen.menu.state
    assert state is not None
    assert state.request.body == ("Failed: hook content changed",)
    runtime.cancel_menu()
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


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
    assert hook_detail_menu(managed).status == "PreToolUse | managed"
    assert len(hook_detail_menu(managed).body) == 4


def test_hook_detail_menu_only_shows_actionable_configuration(tmp_path) -> None:
    entry = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        command_windows="py -3 check_hook.py",
        status_message="Checking shell command display",
        timeout_sec=10,
    )

    assert hook_detail_menu(entry).body == (
        "Command: python check_hook.py",
        "Windows command: py -3 check_hook.py",
        "Message: Checking shell command display",
        "Matcher: shell_command",
        f"Source: project · {tmp_path / '.codex' / 'config.toml'}",
        "Timeout: 10s",
    )
