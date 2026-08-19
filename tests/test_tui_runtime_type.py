# -*- coding: utf-8 -*-

from io import StringIO
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.output.plain_text import PlainTextOutput

from mind_app.frontend.contracts import PassiveFrontendRuntime
from mind_app.tui.core.models import (
    FragmentBlock,
    MenuActionKind,
    MenuOption,
    MenuRequest,
)
from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from mind_app.tui.core.styles import exit_summary_fragments


def test_require_tui_runtime_returns_concrete_runtime() -> None:
    runtime = TuiRuntime()

    assert require_tui_runtime(runtime) is runtime


def test_require_tui_runtime_rejects_passive_runtime() -> None:
    with pytest.raises(TypeError, match="TUI frontend requires TuiRuntime"):
        require_tui_runtime(PassiveFrontendRuntime())


def test_exit_summary_renders_as_plain_terminal_text() -> None:
    stdout = StringIO()
    runtime = TuiRuntime(output_obj=PlainTextOutput(stdout))

    runtime.print_exit_summary("sid_test_1_abcdef")

    assert stdout.getvalue() == (
        "\r\n■ To continue this session, run "
        "mind resume sid_test_1_abcdef\r\n"
    )


def test_exit_summary_command_is_bright_cyan_without_bold() -> None:
    fragments = exit_summary_fragments("sid_test_1_abcdef")
    command_style, command = fragments[-1]

    assert command == "mind resume sid_test_1_abcdef"
    assert command_style == "fg:#4DE3FF"
    assert "bold" not in command_style


@pytest.mark.anyio
async def test_menu_action_is_dispatched_on_next_event_loop_turn() -> None:
    runtime = TuiRuntime()
    calls = []

    assert runtime.emit_menu_action(
        lambda: calls.append("ran"),
        name="test menu action",
    )
    assert calls == []

    await asyncio.sleep(0)

    assert calls == ["ran"]


@pytest.mark.anyio
async def test_stale_menu_action_cannot_run_in_replacement_session() -> None:
    runtime = TuiRuntime()
    calls = []
    first_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="First",
        options=(MenuOption("first", "First"),),
    )))
    await asyncio.sleep(0)

    first_session = runtime.screen.menu.active_session_id
    runtime.emit_menu_action(lambda: calls.append("stale"))
    runtime.cancel_menu()
    runtime.push_menu(MenuRequest(
        title="Second",
        options=(MenuOption("second", "Second"),),
    ))
    second_session = runtime.screen.menu.active_session_id

    await asyncio.sleep(0)

    assert first_session is not None
    assert second_session is not None
    assert second_session > first_session
    assert calls == []
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.request.title == "Second"
    assert await first_task is None

    runtime.cancel_menu()


@pytest.mark.anyio
async def test_menu_action_failure_opens_child_and_keeps_current_root() -> None:
    runtime = TuiRuntime()
    root_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        view_id="root",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)

    def fail() -> None:
        raise ValueError("navigation failed")

    assert runtime.emit_menu_action(
        fail,
        name="test navigation",
        kind=MenuActionKind.NAVIGATION,
    )
    await asyncio.sleep(0)

    state = runtime.screen.menu.state
    assert state is not None
    assert state.request.title == "Menu navigation failed"
    assert state.request.status == "test navigation"
    assert state.request.body == ("Failed: ValueError: navigation failed",)
    assert not root_task.done()

    runtime.cancel_menu()
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.request.view_id == "root"
    runtime.cancel_menu()
    assert await root_task is None


@pytest.mark.anyio
async def test_menu_action_failure_after_session_close_does_not_reopen_menu() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        view_id="root",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)

    def close_then_fail() -> None:
        runtime.cancel_menu()
        raise ValueError("late failure")

    assert runtime.emit_menu_action(
        close_then_fail,
        name="test domain action",
        kind=MenuActionKind.DOMAIN,
    )
    await asyncio.sleep(0)

    assert runtime.screen.menu.state is None
    assert await task is None
    assert any(
        "test domain action: ValueError: late failure" in text
        for _style, text in runtime.document.transcript_fragments(width=80)
    )


@pytest.mark.anyio
async def test_runtime_close_completes_all_menu_view_futures() -> None:
    runtime = TuiRuntime()
    root_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        view_id="close-root",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)
    child_future = runtime.screen.menu.push(MenuRequest(
        title="Child",
        view_id="close-child",
        options=(MenuOption("child", "Child"),),
    ))

    await runtime.close()

    assert root_task.done()
    assert child_future.done()
    assert await root_task is None
    assert await child_future is None
    assert runtime.screen.bottom_pane.active_view is None


@pytest.mark.anyio
async def test_dismiss_menus_by_id_uses_earliest_present_view() -> None:
    runtime = TuiRuntime()
    root_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        view_id="root",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)
    runtime.push_menu(MenuRequest(
        title="Child",
        view_id="child",
        options=(MenuOption("child", "Child"),),
    ))
    runtime.push_menu(MenuRequest(
        title="Grandchild",
        view_id="grandchild",
        options=(MenuOption("grandchild", "Grandchild"),),
    ))

    assert runtime.dismiss_menus_by_id(("child", "root")) == 3
    assert runtime.screen.menu.state is None
    assert await root_task is None


@pytest.mark.anyio
async def test_view_identity_guards_replacement_and_batch_updates_by_session() -> None:
    runtime = TuiRuntime()
    first_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="First",
        view_id="same",
        options=(MenuOption("first", "First"),),
    )))
    await asyncio.sleep(0)
    first_identity = runtime.active_menu_view_identity()
    assert first_identity is not None

    runtime.cancel_menu()
    assert await first_task is None

    second_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Second",
        view_id="same",
        options=(MenuOption("second", "Second"),),
    )))
    await asyncio.sleep(0)
    second_identity = runtime.active_menu_view_identity()
    assert second_identity is not None
    assert second_identity.session_id != first_identity.session_id

    assert not runtime.replace_present_menu_if_id(
        "same",
        MenuRequest(
            title="Stale",
            view_id="same",
            options=(MenuOption("stale", "Stale"),),
        ),
        session_id=first_identity.session_id,
    )
    assert runtime.screen.menu.state.request.title == "Second"

    runtime.replace_present_menu_if_id(
        "same",
        MenuRequest(
            title="Refresh",
            view_id="same",
            options=(MenuOption("second", "Second"),),
        ),
        session_id=second_identity.session_id,
    )
    assert runtime.screen.menu.state.request.title == "Refresh"

    runtime.cancel_menu()
    assert await second_task is None


@pytest.mark.anyio
async def test_batch_menu_replacement_is_atomic_for_generation_conflicts() -> None:
    runtime = TuiRuntime()
    root_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Root",
        view_id="root-batch",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)
    session_id = runtime.active_menu_session_id()
    runtime.push_menu(MenuRequest(
        title="Child",
        view_id="child-batch",
        options=(MenuOption("child", "Child"),),
    ))

    assert runtime.replace_present_menu_if_id(
        "root-batch",
        MenuRequest(
            title="Root v2",
            view_id="root-batch",
            options=(MenuOption("root", "Root"),),
        ),
        session_id=session_id,
    )
    assert runtime.replace_present_menus_if_id(
        (
            (
                "root-batch",
                MenuRequest(
                    title="Stale root",
                    view_id="root-batch",
                    generation=1,
                    options=(MenuOption("root", "Root"),),
                ),
            ),
            (
                "child-batch",
                MenuRequest(
                    title="Child v2",
                    view_id="child-batch",
                    options=(MenuOption("child", "Child"),),
                ),
            ),
        ),
        session_id=session_id,
    ) == 0
    assert runtime.screen.menu._menu_views()[0].state.request.title == "Root v2"
    assert runtime.screen.menu.state.request.title == "Child"

    assert runtime.replace_present_menus_if_id(
        (
            (
                "root-batch",
                MenuRequest(
                    title="Root v3",
                    view_id="root-batch",
                    options=(MenuOption("root", "Root"),),
                ),
            ),
            (
                "child-batch",
                MenuRequest(
                    title="Child v2",
                    view_id="child-batch",
                    options=(MenuOption("child", "Child"),),
                ),
            ),
        ),
        session_id=session_id,
    ) == 2
    assert runtime.screen.menu._menu_views()[0].state.request.title == "Root v3"
    assert runtime.screen.menu.state.request.title == "Child v2"

    runtime.dismiss_menus_by_id(("root-batch",), session_id=session_id)
    assert await root_task is None


@pytest.mark.anyio
async def test_menu_view_protocol_exposes_completion_and_routing_contract() -> None:
    runtime = TuiRuntime()
    menu = runtime.screen.menu
    future = menu.push(MenuRequest(
        title="Protocol",
        options=(MenuOption("ok", "OK"),),
        view_id="protocol",
    ))

    view = runtime.screen.bottom_pane.active_view
    assert view is not None
    assert view.view_id() == "protocol"
    assert view.generation() == 1
    assert view.identity().session_id == runtime.active_menu_session_id()
    assert view.selected_index() == 0
    assert view.active_tab_id() is None
    assert not view.is_complete()
    assert not view.dismiss_after_child_accept()
    view.clear_dismiss_after_child_accept()
    assert view.handle_key_event(SimpleNamespace(key="down", data=""))
    assert menu.state is not None
    assert menu.state.selected == 0

    assert not view.handle_paste("ignored")
    assert view.on_ctrl_c()
    assert view.is_complete()
    assert view.completion().value == "cancelled"
    assert future.done()


@pytest.mark.anyio
async def test_searchable_menu_view_exposes_paste_and_selected_index() -> None:
    runtime = TuiRuntime()
    future = runtime.screen.menu.push(MenuRequest(
        title="Search",
        view_id="search",
        searchable=True,
        options=(
            MenuOption("alpha", "Alpha"),
            MenuOption("beta", "Beta"),
        ),
    ))

    view = runtime.screen.bottom_pane.active_view
    assert view is not None
    assert view.handle_paste("bet\n")
    assert view.selected_index() == 1
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.query == "bet"

    runtime.cancel_menu()
    assert await future is None


@pytest.mark.anyio
async def test_stale_parent_view_cannot_route_input_to_child() -> None:
    runtime = TuiRuntime()
    root_future = runtime.screen.menu.push(MenuRequest(
        title="Root",
        view_id="root",
        searchable=True,
        options=(MenuOption("root", "Root"),),
    ))
    root_view = runtime.screen.bottom_pane.active_view
    assert root_view is not None

    child_future = runtime.screen.menu.push(MenuRequest(
        title="Child",
        view_id="child",
        searchable=True,
        options=(MenuOption("child", "Child"),),
    ))
    child_view = runtime.screen.bottom_pane.active_view
    assert child_view is not None
    assert child_view is not root_view

    assert not root_view.on_ctrl_c()
    assert not root_view.handle_paste("root")
    assert runtime.screen.menu.active_view_id() == "child"

    runtime.cancel_menu()
    runtime.cancel_menu()
    assert await child_future is None
    assert await root_future is None


@pytest.mark.anyio
async def test_runtime_close_erases_empty_canvas() -> None:
    runtime = TuiRuntime()
    runtime._exit_application = AsyncMock()

    await runtime.close()

    runtime._exit_application.assert_awaited_once_with(erase=True)


@pytest.mark.anyio
async def test_runtime_close_preserves_conversation_without_bottom_area() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        FragmentBlock((("class:user", "question"),)),
        kind="user",
    )
    runtime.append_block(
        FragmentBlock((("class:assistant", "answer"),)),
        kind="assistant",
    )
    runtime.submissions.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))
    runtime._exit_application = AsyncMock()

    await runtime.close()

    runtime._exit_application.assert_awaited_once_with(erase=False)
    assert not runtime.screen.input_area.filter()
    assert not runtime.screen._footer_visible()
    assert runtime.screen._interaction_height() == 0
    assert runtime.screen._bottom_pane_top_inset_height() == 0
    assert runtime.screen._queued_height() == 0
