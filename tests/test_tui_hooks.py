# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mind_nova import const
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_app.runtime.hooks.catalog import (
    HookCatalogEntry,
    HookCatalogSnapshot,
    HookCatalogStaleError,
    HookEventSummary
)
from mind_core.hook_discovery import resolve_hook_definitions
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.tui.features.hooks import (
    _hook_detail_body,
    _display_source_path,
    hook_event_menu,
    hook_list_menu,
    manage_hooks,
    review_startup_hooks,
    startup_hooks_review_menu,
)
from mind_app.tui.core.models import (
    FragmentBlock,
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
        display_order=0,
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


async def _render_next_frame(runtime: TuiRuntime):
    previous = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


def test_startup_hooks_review_menu_matches_codex_semantics(tmp_path) -> None:
    menu = startup_hooks_review_menu(
        _catalog(tmp_path, trust_state="untrusted")
    )

    assert menu.title == "Hooks need review"
    assert menu.status == "1 hook is new or changed."
    assert menu.status_style == "class:tui-menu.review"
    assert menu.body == (
        "Hooks can run outside the sandbox after you trust them.",
    )
    assert menu.body_styles == ("class:tui-menu.detail",)
    assert [option.label for option in menu.options] == [
        "Review hooks",
        "Trust all and continue",
        "Continue without trusting (hooks won't run)",
    ]
    assert menu.footer_hint == STANDARD_MENU_FOOTER_HINT


@pytest.mark.anyio
async def test_startup_review_surface_appears_as_one_padded_card(
    tmp_path,
) -> None:
    catalog = _catalog(tmp_path, trust_state="untrusted")
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        runtime.begin_startup_gate()
        renderer = runtime.screen.application.renderer

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=80),
        ), patch.object(renderer, "clear", wraps=renderer.clear) as clear:
            await runtime.open()
            try:
                assert runtime.screen._startup_dimension().preferred == 0
                clear.assert_not_called()

                task = asyncio.create_task(runtime.select_menu(
                    startup_hooks_review_menu(catalog)
                ))
                await _wait_for_menu(runtime, "Hooks need review")
                for _ in range(20):
                    await asyncio.sleep(0)
                    if clear.call_count:
                        break

                clear.assert_called_once_with()
                screen = renderer.last_rendered_screen
                positions = screen.visible_windows_to_write_positions
                top = positions[runtime.screen.startup_menu_top_padding]
                content = positions[runtime.screen.startup_menu_window]
                bottom = positions[runtime.screen.startup_menu_gap]
                footer = positions[runtime.screen.startup_menu_footer_window]

                assert top.ypos == 0
                assert top.height == 1
                assert content.ypos == top.ypos + top.height
                assert bottom.ypos == content.ypos + content.height
                assert footer.ypos == bottom.ypos + bottom.height
                assert runtime.screen.startup_menu_top_padding.style == (
                    "class:menu-card"
                )

                runtime.cancel_menu()
                await task
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_review_browser_starts_with_blank_row_before_card(
    tmp_path,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        animation = AsyncMock()
        runtime.set_startup_animation(
            animation,
            final_frame=lambda: runtime.append_block(
                FragmentBlock((("", ">_ App"),)),
                kind="system",
            ),
        )
        runtime.begin_startup_gate()

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=80),
        ):
            await runtime.open()
            try:
                startup_task = asyncio.create_task(runtime.select_menu(
                    startup_hooks_review_menu(
                        _catalog(tmp_path, trust_state="untrusted")
                    )
                ))
                await _wait_for_menu(runtime, "Hooks need review")
                await _render_next_frame(runtime)
                runtime.cancel_menu()
                await startup_task

                assert runtime.startup_gate_active
                await runtime.settle_startup_gate()
                assert not runtime.startup_gate_active
                browser_task = asyncio.create_task(runtime.select_menu(
                    hook_event_menu(
                        _catalog(tmp_path, trust_state="untrusted")
                    )
                ))
                await _wait_for_menu(runtime, "Hooks")
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                gap = positions[runtime.screen.bottom_pane_top_inset.content]
                card_top = positions[runtime.screen.menu_top_padding]

                animation.assert_not_awaited()
                assert gap.ypos == 0
                assert gap.height == 1
                assert card_top.ypos == gap.ypos + gap.height
                assert runtime.screen.input.window not in positions

                runtime.cancel_menu()
                await browser_task
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_background_result_waits_until_hooks_browser_closes(
    tmp_path,
) -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(runtime.select_menu(
        hook_event_menu(_catalog(tmp_path, trust_state="trusted"))
    ))
    await _wait_for_menu(runtime, "Hooks")

    runtime.queue_background_block(FragmentBlock((("", "External MCP ready"),)))

    assert runtime.document.blocks == []
    assert len(runtime._background_blocks) == 1

    runtime.cancel_menu()
    await task

    assert not runtime._background_blocks
    assert "External MCP ready" in "".join(
        text
        for block in runtime.document.blocks
        for _style, text in block.display_block.fragments
    )


@pytest.mark.anyio
async def test_startup_hooks_review_trusts_current_hashes_in_one_update(
    tmp_path,
) -> None:
    catalog = _catalog(tmp_path, trust_state="untrusted")
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=catalog),
        trust_hooks=Mock(return_value=catalog),
    )

    task = asyncio.create_task(review_startup_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks need review")
    assert runtime.startup_gate_active
    assert (
        runtime.screen.application.layout.current_control
        is runtime.screen.startup_menu_control
    )
    runtime.screen.menu._choose_index(1)
    await task

    assert not runtime.startup_gate_active
    mind.trust_hooks.assert_called_once_with(
        ((catalog.hooks[0].key, catalog.hooks[0].content_hash),),
        workspace=tmp_path,
    )


@pytest.mark.anyio
async def test_startup_hooks_review_returns_full_browser_catalog(tmp_path) -> None:
    catalog = _catalog(tmp_path, trust_state="untrusted")
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=catalog),
        trust_hook=Mock(),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(review_startup_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks need review")
    assert runtime.startup_gate_active
    runtime.screen.menu._choose_index(0)
    selected_catalog = await task

    assert selected_catalog is catalog
    assert not runtime.startup_gate_active


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


def test_hook_event_menu_selects_first_event_needing_review(tmp_path) -> None:
    catalog = _catalog(tmp_path, trust_state="untrusted")
    pre_tool, post_tool = catalog.events
    stop = replace(
        post_tool,
        event="Stop",
        description=f"Right before {const.APP_DESC} ends its turn",
        review_count=1,
    )
    catalog = replace(
        catalog,
        events=(
            replace(pre_tool, review_count=0),
            replace(post_tool, review_count=1),
            stop,
        ),
    )

    menu = hook_event_menu(catalog)

    assert menu.selected == 1
    assert menu.options[menu.selected].value == "PostToolUse"
    assert hook_event_menu(
        replace(
            catalog,
            events=tuple(
                replace(event, review_count=0)
                for event in catalog.events
            ),
        )
    ).selected == 0


@pytest.mark.anyio
async def test_hooks_browser_opens_and_returns_to_review_event(tmp_path) -> None:
    base = _catalog(tmp_path, trust_state="untrusted")
    pre_tool, post_tool = base.events
    changed = replace(
        base.hooks[0],
        key="project:PostToolUse:0",
        event="PostToolUse",
    )
    catalog = replace(
        base,
        events=(
            replace(
                pre_tool,
                installed_count=0,
                review_count=0,
            ),
            replace(
                post_tool,
                installed_count=1,
                review_count=1,
            ),
        ),
        hooks=(changed,),
    )
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 112,
    )
    task = asyncio.create_task(menu.request(hook_event_menu(
        catalog,
        on_event=lambda event: menu.push(hook_list_menu(catalog, event)),
    )))
    await asyncio.sleep(0)

    assert menu.state.request.options[menu.state.selected].value == "PostToolUse"
    menu._choose_index(menu.state.selected)
    assert menu.state.request.title == "PostToolUse hooks"
    assert menu.state.request.options[menu.state.selected].value == changed.key

    menu.cancel()
    assert menu.state.request.title == "Hooks"
    assert menu.state.request.options[menu.state.selected].value == "PostToolUse"
    menu.cancel()
    await task


@pytest.mark.anyio
async def test_hooks_browser_ctrl_c_closes_all_pages_to_input(tmp_path) -> None:
    catalog = _catalog(tmp_path, trust_state="trusted")
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=catalog),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")

    assert runtime.screen.menu.handle_key_event(
        SimpleNamespace(key="c-c", data="")
    )
    await task

    assert not runtime.screen.menu.active
    assert runtime.screen.bottom_pane.input_visible


@pytest.mark.anyio
async def test_hooks_browser_escape_returns_to_events_before_closing(
    tmp_path,
) -> None:
    catalog = _catalog(tmp_path, trust_state="trusted")
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        inspect_hooks=Mock(return_value=catalog),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu._choose_index(0)
    await _wait_for_menu(runtime, "PreToolUse hooks")

    assert runtime.screen.menu.handle_key_event(
        SimpleNamespace(key="escape", data="")
    )
    await _wait_for_menu(runtime, "Hooks")
    assert runtime.screen.menu.active

    runtime.screen.menu.handle_key_event(
        SimpleNamespace(key="escape", data="")
    )
    await task
    assert not runtime.screen.menu.active


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
    visible_lines = text.splitlines()
    assert all(not line or line.startswith("  ") for line in visible_lines)
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


def test_hook_list_uses_display_order_without_prioritizing_review(tmp_path) -> None:
    trusted = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        key="project:PreToolUse:trusted",
        command="python trusted.py",
        display_order=0,
    )
    modified = replace(
        _catalog(tmp_path, trust_state="modified").hooks[0],
        key="project:PreToolUse:modified",
        command="python modified.py",
        display_order=1,
    )
    catalog = replace(
        _catalog(tmp_path, trust_state="modified"),
        hooks=(modified, trusted),
    )

    menu = hook_list_menu(catalog, "PreToolUse")

    assert menu.selected == 0
    assert [option.value for option in menu.options] == [
        trusted.key,
        modified.key,
    ]
    assert [option.label for option in menu.options] == [
        "[x] Hook 1",
        "[!] Hook 2 · modified",
    ]


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
    task = asyncio.create_task(menu.request(hook_list_menu(catalog, "PreToolUse")))
    await asyncio.sleep(0)
    lines = "".join(value for _style, value in menu.fragments()).splitlines()
    assert all(get_cwidth(line) <= width for line in lines)
    assert any("Command   python -c" in line for line in lines)
    assert any("…" in line for line in lines)
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
        trust_hooks=Mock(),
        trust_hook=Mock(),
        set_hook_enabled=Mock(),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    task = asyncio.create_task(manage_hooks(runtime, mind))
    await _wait_for_menu(runtime, "Hooks")
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
    await _wait_for_call(mind.trust_hooks)

    mind.trust_hooks.assert_called_once_with(
        tuple((entry.key, entry.content_hash) for entry in initial.hooks),
        workspace=tmp_path,
    )
    mind.trust_hook.assert_not_called()
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
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
    await _wait_for_call(mind.trust_hook)

    mind.trust_hook.assert_called_once_with(
        initial.hooks[0].key,
        expected_content_hash="sha256:" + "a" * 64,
        workspace=tmp_path,
    )
    root = runtime.screen.menu._menu_views()[0].state.request
    assert root.title == "Hooks"
    assert root.title_accent_suffix == ""
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
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
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


def test_hook_detail_body_separates_trust_states(tmp_path) -> None:
    untrusted = _catalog(tmp_path, trust_state="untrusted").hooks[0]
    modified = replace(
        untrusted,
        trust_state="modified",
    )
    managed = _catalog(
        tmp_path,
        trust_state="managed",
        trust_policy="managed",
    ).hooks[0]

    assert _hook_detail_body(untrusted)[-1] == (
        "Trust     New hook - review required"
    )
    assert _hook_detail_body(modified)[-1] == (
        "Trust     Modified since last trusted - review required"
    )
    assert _hook_detail_body(managed) == (
        "Event     PreToolUse",
        "Matcher   shell_command",
        "Source    Project config - "
        f"{_display_source_path(str(tmp_path / '.codex' / 'config.toml'))}",
        "Command   python check_hook.py",
        "Mode      Sync",
        "Timeout   5s",
        "Context   limit: 2500 approximate tokens",
        "Trust     Managed",
    )


def test_hook_detail_body_omits_optional_fields_and_shortens_home_source(
    tmp_path,
) -> None:
    entry = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        matcher="",
        additional_context_limit=None,
        source_path=str(Path.home() / ".codex" / "hooks.json"),
    )

    body = _hook_detail_body(entry)

    assert not any(line.startswith("Matcher") for line in body)
    assert not any(line.startswith("Context") for line in body)
    assert "Source    Project config - ~" in next(
        line for line in body if line.startswith("Source")
    )


def test_hook_registry_keeps_default_context_optional_for_details(tmp_path) -> None:
    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "check"}],
            }],
        },
        source_scope="user",
        source_path=Path.home() / ".codex" / "hooks.json",
    )
    entry = HookRegistry().inspect(
        definitions,
        workspace=tmp_path,
    ).hooks[0]

    assert entry.additional_context_limit is None
    assert not any(
        line.startswith("Context")
        for line in _hook_detail_body(entry)
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

    review_menu = hook_list_menu(
        replace(_catalog(tmp_path, trust_state="untrusted"), hooks=(untrusted,)),
        "PreToolUse",
    )
    assert review_menu.status == "1 hook needs review before it can run."
    assert review_menu.status_style == "class:tui-menu.review"


@pytest.mark.anyio
async def test_hook_list_menu_renders_selected_detail_section(tmp_path) -> None:
    first = _catalog(tmp_path, trust_state="untrusted").hooks[0]
    second = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        key="project:PreToolUse:1",
        command="python trusted_hook.py",
    )
    third = replace(
        _catalog(tmp_path, trust_state="managed", trust_policy="managed").hooks[0],
        key="project:PreToolUse:2",
    )
    catalog = replace(
        _catalog(tmp_path, trust_state="untrusted"),
        hooks=(first, second, third),
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
    detail_lines = text.splitlines()
    assert "  Event     PreToolUse" in detail_lines
    assert "  Trust     New hook - review required" in detail_lines
    assert not any(line.startswith("Event") for line in detail_lines)
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

    menu._move(1)
    assert "[x] Hook 3" in "".join(value for _style, value in menu.fragments())
    assert "Managed hooks are always on; press esc to go back" in "".join(
        value for _style, value in menu.footer_fragments()
    )

    menu.cancel()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize("key", ("space", "enter"))
async def test_hook_list_menu_space_or_enter_toggles_trusted_hook(
    tmp_path,
    key,
) -> None:
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
    runtime.screen.menu.handle_key_event(
        SimpleNamespace(key=key, data=" " if key == "space" else "")
    )
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
    ("trust_state", "trust_policy"),
    (("trusted", "content_hash"), ("managed", "managed")),
)
async def test_hook_list_menu_t_is_noop_for_trusted_or_managed_hook(
    tmp_path,
    trust_state,
    trust_policy,
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
    runtime.screen.menu.handle_key_event(SimpleNamespace(key="t", data="t"))
    await asyncio.sleep(0)

    mind.trust_hook.assert_not_called()
    mind.set_hook_enabled.assert_not_called()
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("trust_state", "trust_policy"),
    (
        ("untrusted", "content_hash"),
        ("managed", "managed"),
    ),
)
async def test_hook_list_enter_and_space_do_not_toggle_read_only_hook(
    tmp_path,
    trust_state,
    trust_policy,
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

    runtime.screen.menu.handle_key_event(SimpleNamespace(key="enter", data=""))
    await asyncio.sleep(0)
    state = runtime.screen.menu.state
    assert state is not None
    assert state.request.title == "PreToolUse hooks"
    mind.set_hook_enabled.assert_not_called()
    mind.trust_hook.assert_not_called()

    runtime.cancel_menu()
    runtime.cancel_menu()
    runtime.cancel_menu()
    await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("enabled", "expected_enabled"),
    ((True, False), (False, True)),
)
async def test_hook_list_enter_toggles_trusted_hook(
    tmp_path,
    enabled,
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


def test_hook_detail_body_only_shows_actionable_configuration(tmp_path) -> None:
    entry = replace(
        _catalog(tmp_path, trust_state="trusted").hooks[0],
        command_windows="py -3 check_hook.py",
        status_message="Checking shell command display",
        timeout_sec=10,
    )

    assert _hook_detail_body(entry) == (
        "Event     PreToolUse",
        "Matcher   shell_command",
        "Source    Project config - "
        f"{_display_source_path(str(tmp_path / '.codex' / 'config.toml'))}",
        "Command   python check_hook.py",
        "Mode      Sync",
        "Timeout   10s",
        "Context   limit: 2500 approximate tokens",
        "Trust     Trusted",
    )
