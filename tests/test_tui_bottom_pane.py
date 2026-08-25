# -*- coding: utf-8 -*-

import asyncio

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.tui.core.bottom_pane import TuiBottomPane
from mind_app.tui.core.models import FragmentBlock, MenuOption, MenuRequest
from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import TuiRuntime


def test_bottom_pane_restores_previous_surface_focus() -> None:
    focused: list[str] = []
    pane = TuiBottomPane(
        focus_surface=focused.append,
        focus_input=lambda: focused.append("input"),
        invalidate=lambda: None,
    )

    pane.activate("menu")
    pane.activate("approval")
    pane.deactivate("approval")
    pane.deactivate("menu")

    assert focused == ["menu", "approval", "menu", "input"]
    assert pane.active_surface is None


@pytest.mark.parametrize("surface", ["approval", "menu"])
def test_bottom_surface_exit_uses_current_natural_layout(surface: str) -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    screen.bottom_pane.activate(surface)

    screen._deactivate_bottom_surface(surface)

    assert screen.bottom_pane.input_visible
    assert screen._visible_height() == screen._natural_visible_height()


def test_nested_bottom_surface_does_not_release_until_input_returns() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    screen.bottom_pane.activate("menu")
    screen.bottom_pane.activate("approval")

    screen._deactivate_bottom_surface("approval")

    assert screen.bottom_pane.active_surface == "menu"
    assert screen._visible_height() == screen._natural_visible_height()


@pytest.mark.parametrize(
    (
        "rows",
        "expected_status_height",
        "expected_process_height",
        "expected_gap_height",
    ),
    (
        (5, 0, 0, 0),
        (6, 1, 0, 0),
        (8, 1, 0, 1),
        (9, 1, 0, 1),
        (10, 1, 0, 1),
    ),
)
def test_bottom_pane_shrinks_status_before_composer(
    monkeypatch: pytest.MonkeyPatch,
    rows: int,
    expected_status_height: int,
    expected_process_height: int,
    expected_gap_height: int,
) -> None:
    runtime = TuiRuntime()
    monkeypatch.setattr(
        runtime.screen.application.output,
        "get_size",
        lambda: Size(rows=rows, columns=60),
    )
    runtime.screen.set_activity_renderable(FragmentBlock(((
        "",
        "status one\nstatus two\nstatus three",
    ),)))
    runtime.set_process_status_label("background command")

    layout = runtime.screen._bottom_pane_layout()

    assert layout.outer_top_inset_height == 1
    assert layout.composer.input_stack_height == 4
    assert layout.status_height == expected_status_height
    assert layout.process_status_height == expected_process_height
    assert layout.interaction_gap_height == expected_gap_height
    assert layout.total_height <= rows


@pytest.mark.anyio
async def test_active_view_replaces_status_exec_and_queue() -> None:
    runtime = TuiRuntime()
    runtime.screen.set_activity_renderable(FragmentBlock((("", "Thinking"),)))
    runtime.set_process_status_label("background command")
    runtime.track_pending_steer(TuiSubmission(
        value="queued input",
        editable_text="queued input",
        paste_store={},
    ))

    task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Options",
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    active = runtime.screen._bottom_pane_layout()

    assert active.outer_top_inset_height == 1
    assert active.active_view.surface == "menu"
    assert active.status_height == 0
    assert active.process_status_height == 0
    assert active.queued_height == 0
    assert active.interaction_gap_height == 0
    assert active.composer.input_stack_height == 0

    runtime.screen.menu.finish(None)
    await task

    restored = runtime.screen._bottom_pane_layout()
    assert restored.active_view.surface is None
    assert restored.status_height == 1
    assert restored.process_status_height == 0
    assert restored.queued_height > 0


@pytest.mark.anyio
async def test_menu_footer_is_outside_surface_and_has_no_card_style() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Options",
        footer_note="Status",
        footer_hint="Press Esc to go back",
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    screen = runtime.screen
    view = screen.bottom_pane.active_view
    assert view is not None

    surface_text = "".join(text for _style, text in view.fragments())
    footer_text = "".join(text for _style, text in view.footer_fragments())
    assert "Press Esc" not in surface_text
    assert "Press Esc" in footer_text
    assert screen._menu_view_fragments() == view.fragments()
    assert screen._menu_footer_fragments() == view.footer_fragments()
    assert screen.menu_card.filter()
    assert screen.menu_footer.filter()
    assert screen.menu_window.style == "class:menu-card"
    assert screen.menu_footer_window.style == ""

    layout = screen._active_view_layout()
    assert layout.footer_height == view.footer_height(screen.terminal_width)
    assert layout.content_height == view.desired_height(screen.terminal_width)
    assert layout.total_height == screen._interaction_height()

    screen.menu.finish(None)
    await task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rows", "option_count", "expected_content_height"),
    ((8, 2, 4), (8, 12, 5), (12, 12, 9)),
)
async def test_menu_active_view_shrinks_to_current_terminal_budget(
    monkeypatch: pytest.MonkeyPatch,
    rows: int,
    option_count: int,
    expected_content_height: int,
) -> None:
    runtime = TuiRuntime()
    monkeypatch.setattr(
        runtime.screen.application.output,
        "get_size",
        lambda: Size(rows=rows, columns=60),
    )
    task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Options",
        options=tuple(
            MenuOption(index, f"Option {index}")
            for index in range(option_count)
        ),
    )))
    await asyncio.sleep(0)

    layout = runtime.screen._active_view_layout()

    assert layout.surface == "menu"
    assert layout.available_height == rows - 1
    assert layout.top_padding_height == 1
    assert layout.bottom_padding_height == 1
    assert layout.content_height == expected_content_height
    assert layout.total_height <= layout.available_height

    runtime.screen.menu.finish(None)
    await task


@pytest.mark.anyio
async def test_approval_temporarily_replaces_menu_surface() -> None:
    runtime = TuiRuntime()
    runtime.append_block(FragmentBlock((("", "command context"),)), kind="system")
    menu_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Model",
        options=(MenuOption("gpt-test", "gpt-test"),),
    )))
    await asyncio.sleep(0)

    assert runtime.screen._bottom_pane_top_inset_height() == 1

    approval_task = asyncio.create_task(ApprovalCoordinator(runtime).request({
        "id": "menu-approval",
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    }))
    await _wait_for_approval(runtime, "menu-approval")

    assert runtime.screen.bottom_pane.active_surface == "approval"
    assert runtime.screen._bottom_pane_top_inset_height() == 1
    assert runtime.screen.approval_card.filter()
    assert not runtime.screen.menu_card.filter()
    approval_layout = runtime.screen._active_view_layout()
    assert approval_layout.surface == "approval"
    assert approval_layout.total_height == runtime.screen._interaction_height()
    assert approval_layout.total_height > 0

    runtime.screen.approval.finish("decline")
    assert await approval_task == "decline"
    assert runtime.screen.bottom_pane.active_surface == "menu"
    assert runtime.screen._bottom_pane_top_inset_height() == 1
    assert runtime.screen.menu_card.filter()
    menu_layout = runtime.screen._active_view_layout()
    assert menu_layout.surface == "menu"
    assert menu_layout.total_height == runtime.screen._interaction_height()

    runtime.screen.menu.finish(None)
    assert await menu_task is None
    assert runtime.screen.bottom_pane.active_surface is None
    assert runtime.screen.input_area.filter()


@pytest.mark.anyio
async def test_object_view_stack_restores_exact_parent_after_overlay() -> None:
    runtime = TuiRuntime()
    root_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        options=(MenuOption("open", "Open"),),
        view_id="root",
    )))
    await asyncio.sleep(0)

    root_view = runtime.screen.bottom_pane.active_view
    runtime.push_menu(MenuRequest(
        title="Child",
        options=(MenuOption("done", "Done"),),
        view_id="child",
    ))
    child_view = runtime.screen.bottom_pane.active_view

    assert root_view is not None
    assert child_view is not None
    assert child_view is not root_view
    assert child_view.view_id() == "child"
    assert len(runtime.screen.bottom_pane.view_stack) == 2

    approval_task = asyncio.create_task(ApprovalCoordinator(runtime).request({
        "id": "stack-approval",
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    }))
    await _wait_for_approval(runtime, "stack-approval")

    assert runtime.screen.bottom_pane.active_surface == "approval"
    assert runtime.screen.bottom_pane.active_view is None

    runtime.screen.approval.finish("decline")
    assert await approval_task == "decline"
    assert runtime.screen.bottom_pane.active_view is child_view

    runtime.cancel_menu()
    assert runtime.screen.bottom_pane.active_view is root_view

    runtime.cancel_menu()
    assert await root_task is None
    assert runtime.screen.bottom_pane.active_view is None
    assert len(runtime.screen.bottom_pane.view_stack) == 0


@pytest.mark.anyio
async def test_menu_closes_from_terminal_escape_key() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        assert runtime.screen.application.ttimeoutlen == 0.1
        await runtime.open()
        try:
            menu_task = asyncio.create_task(runtime.select_menu(MenuRequest(
                title="Model",
                options=(MenuOption("gpt-test", "gpt-test"),),
            )))
            await asyncio.sleep(0)

            pipe_input.send_text("\x1b")

            assert await asyncio.wait_for(menu_task, timeout=1.0) is None
            assert runtime.screen.bottom_pane.active_surface is None
        finally:
            await runtime.close()


async def _wait_for_approval(
    runtime: TuiRuntime,
    approval_id: str,
) -> None:
    for _ in range(40):
        state = runtime.screen.approval.state
        if state is not None and state.approval.get("id") == approval_id:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"approval was not presented: {approval_id}")
