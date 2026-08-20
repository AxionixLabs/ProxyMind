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
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.tui.features.hooks import (
    _hook_detail_body,
    hook_detail_menu,
    hook_event_menu,
    hook_list_menu,
    manage_hooks,
)
from mind_app.tui.core.models import (
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.menu import TuiMenu
from prompt_toolkit.utils import get_cwidth


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
                review_count=int(trust_state in {"untrusted", "modified"}),
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


def test_hook_detail_uses_codex_handler_branches(tmp_path) -> None:
    base = _catalog(tmp_path, trust_state="trusted").hooks[0]
    mcp = replace(
        base,
        handler_type="mcp_tool",
        command=None,
        mcp_server="files",
        mcp_tool="read",
    )
    prompt = replace(base, handler_type="prompt", command=None)
    agent = replace(base, handler_type="agent", command=None)

    assert "MCP Server files" in _hook_detail_body(mcp)
    assert "MCP Tool  read" in _hook_detail_body(mcp)
    assert "Handler   Prompt" in _hook_detail_body(prompt)
    assert "Handler   Agent" in _hook_detail_body(agent)


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


async def _wait_for_menu_action(mock: Mock) -> None:
    await _wait_for_call(mock)


def test_hook_event_menu_uses_codex_descriptions_and_columns(
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

    assert menu.options[0].detail == ""
    assert menu.options[0].columns == (
        "PreToolUse", "9999", "9999", "Before a tool executes"
    )
    assert menu.options[1].columns == (
        "PostToolUse", "0", "0", "After a tool executes"
    )
    assert menu.view_id == "hooks:events"
    assert menu.help_text == ""
    assert menu.footer_hint == "Press enter to view hooks; esc to close"
    assert (
        menu.description_layout
        is MenuDescriptionLayout.COLUMNS
    )


def test_hook_event_menu_shows_discovery_warnings(tmp_path) -> None:
    catalog = _catalog(
        tmp_path,
        trust_state="untrusted",
        warnings=("skipping empty hook command",),
    )

    menu = hook_event_menu(catalog)

    assert menu.body == (
        "",
        "⚠ 1 hook needs review before it can run.",
        "",
        "Issues",
        "⚠ skipping empty hook command",
        "",
        "Event                 Installed   Active      Review      Description",
    )


@pytest.mark.anyio
async def test_hook_event_menu_renders_review_column_and_fixed_rows(tmp_path) -> None:
    catalog = _catalog(tmp_path, trust_state="untrusted")
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 112,
    )
    task = asyncio.create_task(menu.request(hook_event_menu(catalog)))
    await asyncio.sleep(0)

    text = "".join(value for _style, value in menu.fragments())
    assert "Event                 Installed   Active      Review      Description" in text
    assert "PreToolUse" in text
    assert "Press t to trust all; enter to review hooks; esc to close" in "".join(
        value for _style, value in menu.footer_fragments()
    )

    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hook_event_menu_renders_all_codex_events_without_gutter(tmp_path) -> None:
    catalog = HookRegistry().inspect((), workspace=tmp_path)
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 112,
    )
    task = asyncio.create_task(menu.request(hook_event_menu(catalog)))
    await asyncio.sleep(0)

    text = "".join(value for _style, value in menu.fragments())
    event_names = {item.event for item in catalog.events}
    event_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and line.strip().split(maxsplit=1)[0] in event_names
    ]

    assert [line.split(maxsplit=1)[0] for line in event_lines] == [
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
    ]
    assert all("›" not in line for line in event_lines)
    assert all(not line.split(maxsplit=1)[0].rstrip(".").isdigit() for line in event_lines)

    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hook_event_refresh_restores_selected_event_by_key(tmp_path) -> None:
    first = _catalog(tmp_path, trust_state="trusted")
    second = replace(
        first.events[0],
        event="PostToolUse",
        description="After a tool executes",
    )
    catalog = replace(first, events=(first.events[0], second))
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(hook_event_menu(catalog)))
    await asyncio.sleep(0)
    menu._move(1)
    assert menu.state.request.options[menu.state.selected].value == "PostToolUse"

    refreshed = replace(catalog, events=(second, first.events[0]))
    assert menu.replace_present_if_id(
        "hooks:events",
        hook_event_menu(refreshed),
    )
    assert menu.state.request.options[menu.state.selected].value == "PostToolUse"
    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hook_list_refresh_handles_deleted_selected_hook(tmp_path) -> None:
    first = _catalog(tmp_path, trust_state="trusted").hooks[0]
    second = replace(first, key="project:PreToolUse:1", command="second")
    catalog = replace(_catalog(tmp_path, trust_state="trusted"), hooks=(first, second))
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(hook_list_menu(catalog, "PreToolUse")))
    await asyncio.sleep(0)
    menu._move(1)
    assert menu.state.request.options[menu.state.selected].value == second.key

    refreshed = replace(catalog, hooks=(first,))
    assert menu.replace_present_if_id(
        "hooks:list:PreToolUse",
        hook_list_menu(refreshed, "PreToolUse"),
    )
    assert menu.state.request.options[menu.state.selected].value == first.key
    menu.cancel()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize("width", (40, 80))
async def test_hook_views_clip_long_commands_at_narrow_widths(tmp_path, width) -> None:
    entry = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        command="python -c " + "x" * 240,
    )
    catalog = replace(_catalog(tmp_path, trust_state="trusted"), hooks=(entry,))
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    task = asyncio.create_task(menu.request(hook_detail_menu(entry)))
    await asyncio.sleep(0)
    lines = "".join(value for _style, value in menu.fragments()).splitlines()
    assert all(get_cwidth(line) <= width for line in lines)
    assert any("Command   python -c" in line and "…" in line for line in lines)
    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hooks_menu_trusts_all_review_hooks_from_root(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="untrusted")
    second = replace(
        initial.hooks[0],
        key="project:PostToolUse:0",
        event="PostToolUse",
        command="python second_hook.py",
    )
    initial = replace(
        initial,
        hooks=(initial.hooks[0], second),
        installed_count=2,
        events=tuple(
            replace(item, installed_count=1, review_count=1)
            if item.event in {"PreToolUse", "PostToolUse"}
            else item
            for item in initial.events
        ),
    )
    updated = replace(
        initial,
        hooks=tuple(replace(item, trust_state="trusted", active=True) for item in initial.hooks),
        active_count=2,
        events=tuple(
            replace(item, active_count=1, review_count=0)
            if item.event in {"PreToolUse", "PostToolUse"}
            else item
            for item in initial.events
        ),
    )
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, updated]),
        trust_hook=Mock(),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
    await _wait_for_call(mind.trust_hook)
    for _ in range(100):
        if mind.trust_hook.call_count == 2:
            break
        await asyncio.sleep(0)

    assert mind.trust_hook.call_count == 2
    assert {
        call.args[0] for call in mind.trust_hook.call_args_list
    } == {initial.hooks[0].key, initial.hooks[1].key}
    runtime.cancel_menu()
    await task


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
    await _wait_for_menu(runtime, "PreToolUse hooks")
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
    assert root.status == "Lifecycle hooks from config and enabled plugins."
    assert root.options[0].columns == (
        "PreToolUse", "1", "1", "Before a tool executes"
    )
    current = runtime.screen.menu.state
    assert current is not None
    assert current.request.title == "PreToolUse hooks"
    assert current.request.options[0].selected_body[:2] == (
        "Event     PreToolUse",
        "Matcher   shell_command",
    )
    assert views == []
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
    await _wait_for_menu(runtime, "PreToolUse hooks")
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
    modified = replace(
        untrusted,
        trust_state="modified",
    )
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
    assert hook_detail_menu(modified).options[1].label == "Trust hook"
    assert hook_detail_menu(modified).body[-1] == (
        "Trust     Modified since last trusted - review required"
    )
    assert hook_detail_menu(trusted).options[1].label == "Disable hook"
    assert hook_detail_menu(disabled).options[1].label == "Enable hook"
    assert hook_detail_menu(disabled).status == "PreToolUse | disabled"
    assert hook_detail_menu(managed).options == ()
    assert hook_detail_menu(managed).status == "PreToolUse | managed"
    assert hook_detail_menu(managed).body == (
        "Event     PreToolUse",
        "Matcher   shell_command",
        f"Source    Project config - {tmp_path / '.codex' / 'config.toml'}",
        "Command   python check_hook.py",
        "Mode      Sync",
        "Timeout   5s",
        "Context   limit: 2500 approximate tokens",
        "Trust     Managed",
    )


def test_hook_list_menu_uses_codex_rows_details_and_dynamic_footer(tmp_path) -> None:
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

    for entry, expected_label, expected_footer in (
        (untrusted, "[!] Hook 1 · new", "Press t to trust; esc to go back"),
        (trusted, "[x] Hook 1", "Press space or enter to toggle; esc to go back"),
        (disabled, "[ ] Hook 1", "Press space or enter to toggle; esc to go back"),
        (managed, "[x] Hook 1", "Managed hooks are always on; press esc to go back"),
    ):
        catalog = replace(_catalog(tmp_path, trust_state=entry.trust_state), hooks=(entry,))
        menu = hook_list_menu(catalog, "PreToolUse")
        assert menu.title == "PreToolUse hooks"
        assert menu.options[0].label == expected_label
        assert menu.options[0].detail == ""
        assert menu.options[0].selected_body[0] == "Event     PreToolUse"
        assert menu.footer_hint == expected_footer


@pytest.mark.anyio
async def test_hook_list_menu_renders_selected_detail_section(tmp_path) -> None:
    first = _catalog(tmp_path, trust_state="untrusted").hooks[0]
    second = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        key="project:PreToolUse:1",
        command="python trusted_hook.py",
    )
    catalog = replace(
        _catalog(tmp_path, trust_state="untrusted"),
        hooks=(first, second),
    )
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 112,
    )
    task = asyncio.create_task(menu.request(hook_list_menu(catalog, "PreToolUse")))
    await asyncio.sleep(0)

    text = "".join(value for _style, value in menu.fragments())
    assert "[!] Hook 1 · new" in text
    assert "Event     PreToolUse" in text
    assert "Trust     New hook - review required" in text
    assert "Press t to trust; esc to go back" in "".join(
        value for _style, value in menu.footer_fragments()
    )

    menu._move(1)
    text = "".join(value for _style, value in menu.fragments())
    assert "[x] Hook 2" in text
    assert "Command   python trusted_hook.py" in text
    assert "Press space or enter to toggle; esc to go back" in "".join(
        value for _style, value in menu.footer_fragments()
    )

    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hook_list_menu_space_toggles_trusted_hook(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="trusted")
    updated = _catalog(tmp_path, trust_state="trusted", enabled=False)
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, updated]),
        trust_hook=Mock(),
        set_hook_enabled=Mock(return_value=updated),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="space", data=" "))
    await _wait_for_menu_action(mind.set_hook_enabled)

    mind.set_hook_enabled.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash=initial.hooks[0].content_hash,
        enabled=False,
        workspace=tmp_path,
    )
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
async def test_hook_list_menu_t_trusts_review_hook(tmp_path) -> None:
    initial = _catalog(tmp_path, trust_state="untrusted")
    updated = _catalog(tmp_path, trust_state="trusted")
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, updated]),
        trust_hook=Mock(return_value=updated),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
    await _wait_for_menu_action(mind.trust_hook)

    mind.trust_hook.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash=initial.hooks[0].content_hash,
        workspace=tmp_path,
    )
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("trust_state", "trust_policy", "expected_title"),
    (
        ("untrusted", "content_hash", "Hook Details"),
        ("managed", "managed", "Hook Details"),
    ),
)
async def test_hook_list_enter_and_space_do_not_toggle_read_only_hook(
    tmp_path,
    trust_state,
    trust_policy,
    expected_title,
) -> None:
    initial = _catalog(
        tmp_path,
        trust_state=trust_state,
        trust_policy=trust_policy,
    )
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=initial),
        trust_hook=Mock(),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="space", data=" "))
    await asyncio.sleep(0)
    mind.set_hook_enabled.assert_not_called()

    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, expected_title)
    mind.set_hook_enabled.assert_not_called()
    mind.trust_hook.assert_not_called()

    runtime.cancel_menu()
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("enabled", "action", "expected_enabled"),
    ((True, "Disable hook", False), (False, "Enable hook", True)),
)
async def test_hook_detail_menu_toggles_trusted_hook(
    tmp_path,
    enabled,
    action,
    expected_enabled,
) -> None:
    initial = _catalog(
        tmp_path,
        trust_state="trusted",
        enabled=enabled,
    )
    updated = _catalog(
        tmp_path,
        trust_state="trusted",
        enabled=expected_enabled,
    )
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(side_effect=[initial, updated]),
        trust_hook=Mock(),
        set_hook_enabled=Mock(return_value=updated),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "Hook Details")

    assert runtime.screen.menu.state.request.options[1].label == action
    runtime.screen.menu._choose_index(1)
    await _wait_for_call(mind.set_hook_enabled)

    mind.set_hook_enabled.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash=initial.hooks[0].content_hash,
        enabled=expected_enabled,
        workspace=tmp_path,
    )
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


def test_hook_detail_menu_only_shows_actionable_configuration(tmp_path) -> None:
    entry = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        command_windows="py -3 check_hook.py",
        status_message="Checking shell command display",
        timeout_sec=10,
    )

    assert hook_detail_menu(entry).body == (
        "Event     PreToolUse",
        "Matcher   shell_command",
        f"Source    Project config - {tmp_path / '.codex' / 'config.toml'}",
        "Command   python check_hook.py",
        "Mode      Sync",
        "Timeout   10s",
        "Context   limit: 2500 approximate tokens",
        "Trust     Trusted",
    )
