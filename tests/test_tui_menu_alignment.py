# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.keys import Keys
from metadata import const

from mind_app.tui.core.menu import (
    TUI_MENU_STYLE,
    TuiMenu,
)
from mind_app.tui.core.models import (
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    MenuTab,
    ViewCompletion,
)


@pytest.mark.anyio
async def test_mcp_option_details_align_to_restart_width() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 100,
    )
    request = MenuRequest(
        title="External MCP",
        options=(
            MenuOption("start", "start", "启动服务"),
            MenuOption("force", "force", "强制启动"),
            MenuOption("stop", "stop", "停止服务"),
            MenuOption("restart", "restart", "重启服务"),
            MenuOption("status", "status", "查看状态"),
        ),
    )

    task = asyncio.create_task(menu.request(request))
    await asyncio.sleep(0)
    lines = _fragments_text(menu.fragments()).splitlines()[3:]
    separator_columns = [
        get_cwidth(line.split(" · ", 1)[0])
        for line in lines
    ]
    menu.finish(None)
    await task

    assert len(set(separator_columns)) == 1


@pytest.mark.anyio
async def test_menu_rows_fit_terminal_width_with_wide_text() -> None:
    width = 42
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    request = MenuRequest(
        title="Background Commands",
        status="running=2",
        options=(
            MenuOption(
                "first",
                "python -m example --message 这是一个很长的命令参数",
                "tool pid=12345",
            ),
            MenuOption(
                "second",
                "npm run dev -- --host 127.0.0.1",
                "shell pid=8",
            ),
        ),
    )

    task = asyncio.create_task(menu.request(request))
    await asyncio.sleep(0)
    lines = _fragments_text(menu.fragments()).splitlines()
    menu.finish(None)
    await task

    assert all(get_cwidth(line) <= width for line in lines)
    option_lines = lines[4:]
    assert "…" in option_lines[0]
    assert all(get_cwidth(line) <= width for line in option_lines)


@pytest.mark.anyio
async def test_skills_and_resume_details_share_adaptive_terminal_width() -> None:
    detail = " ".join(
        f"detail-{index}"
        for index in range(30)
    )
    requests = (
        MenuRequest(
            title="Skills",
            options=(MenuOption("skill", "review", detail),),
        ),
        MenuRequest(
            title="Resume conversation",
            options=(MenuOption("record", "08-05 10:30", detail),),
        ),
    )

    async def option_width(request: MenuRequest, width: int) -> int:
        menu = TuiMenu(
            invalidate=lambda: None,
            focus_menu=lambda: None,
            focus_input=lambda: None,
            get_width=lambda: width,
        )
        task = asyncio.create_task(menu.request(request))
        await asyncio.sleep(0)
        line = _fragments_text(menu.fragments()).splitlines()[2]
        menu.finish(None)
        await task
        return get_cwidth(line)

    narrow = [await option_width(request, 48) for request in requests]
    wide = [await option_width(request, 96) for request in requests]

    assert narrow == [46, 46]
    assert wide == [94, 94]


@pytest.mark.anyio
async def test_selected_menu_option_highlights_only_prefix_and_label() -> None:
    width = 36
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="External MCP",
        options=(
            MenuOption("start", "start", "启动服务"),
            MenuOption("stop", "stop", "停止服务"),
        ),
    )))
    await asyncio.sleep(0)

    fragments = menu.fragments()
    first_row_start = next(
        index
        for index, (style, _text) in enumerate(fragments)
        if style == "class:tui-menu.index.active"
    )
    first_row_end = next(
        index
        for index in range(first_row_start, len(fragments))
        if fragments[index][1] == "\n"
    )
    active_row = fragments[first_row_start:first_row_end]

    menu.finish(None)
    await task

    assert sum(get_cwidth(text) for _style, text in active_row) < width
    assert [style for style, _text in active_row] == [
        "class:tui-menu.index.active",
        "class:tui-menu.label.active",
        "class:tui-menu.detail-selected",
    ]
    assert TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.label.active"
    ).bgcolor == ""
    assert TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.detail"
    ).bgcolor == ""
    assert not TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.index"
    ).bold
    assert not TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.label"
    ).bold
    assert TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.label.active"
    ).bold


@pytest.mark.anyio
async def test_archive_confirmation_renders_codex_text_and_spacing() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 100,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Archive this session?",
        body=(
            f"Are you sure? This will archive the current session "
            f"and exit {const.APP_DESC}",
        ),
        footer_hint="Press enter to confirm or esc to go back",
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=(
            MenuOption(
                False,
                "No, don't archive",
                "Return to the current session",
            ),
            MenuOption(
                True,
                "Yes, archive and exit",
                "Archive this session now",
            ),
        ),
    )))
    await asyncio.sleep(0)

    assert [
        line.rstrip()
        for line in _fragments_text(menu.fragments()).splitlines()
    ] == [
        "  Archive this session?",
        f"  Are you sure? This will archive the current session "
        f"and exit {const.APP_DESC}",
        "",
        "› 1. No, don't archive      Return to the current session",
        "  2. Yes, archive and exit  Archive this session now",
        "",
        "  Press enter to confirm or esc to go back",
    ]

    menu.finish(None)
    await task


def test_menu_style_matches_codex_semantics_without_selected_row_background() -> None:
    title = TUI_MENU_STYLE.get_attrs_for_style_str("class:tui-menu.title")
    status = TUI_MENU_STYLE.get_attrs_for_style_str("class:tui-menu.status")
    label = TUI_MENU_STYLE.get_attrs_for_style_str("class:tui-menu.label")
    detail = TUI_MENU_STYLE.get_attrs_for_style_str("class:tui-menu.detail")
    selected = TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.label.active"
    )

    assert title.bold and not title.dim
    assert status.dim
    assert not label.bold and not label.dim
    assert detail.dim
    assert selected.bold and selected.color == "ansiblue"
    assert selected.bgcolor == ""


@pytest.mark.anyio
async def test_menu_filters_controls_before_width_calculation() -> None:
    width = 24
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Devices\x1b]52;c;payload\x1b\\",
        options=(
            MenuOption("device", "id\tdevice", "ready\x1bPprivate\x1b\\"),
        ),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    menu.finish(None)
    await task

    assert "\x1b" not in text
    assert "payload" not in text
    assert "private" not in text
    assert all(get_cwidth(line) <= width for line in text.splitlines())


@pytest.mark.anyio
async def test_menu_normalizes_external_fields_to_single_rows() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Remote\ntitle",
        status="listener\nactive",
        body=("first\nsecond",),
        help_text="Enter\nto view",
        options=(
            MenuOption("message", "Summary\ncontinued", "call\n1"),
        ),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    menu.finish(None)
    await task

    assert text.splitlines() == [
        "  Remote title",
        "  listener active",
        "  Enter to view",
        "  first second",
        "",
        "› 1. Summary continued  call 1",
    ]


@pytest.mark.anyio
async def test_menu_selection_wraps_across_first_and_last_options() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        options=(
            MenuOption("first", "First"),
            MenuOption("second", "Second"),
            MenuOption("last", "Last"),
        ),
    )))
    await asyncio.sleep(0)

    assert menu.state is not None
    assert menu.state.selected == 0

    menu._move(-1)
    assert menu.state.selected == 2

    menu._move(1)
    assert menu.state.selected == 0

    menu.finish(None)
    await task


@pytest.mark.anyio
async def test_menu_height_caps_visible_options_at_eight_rows() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Tools",
        options=tuple(
            MenuOption(index, f"Option {index}")
            for index in range(12)
        ),
    )))
    await asyncio.sleep(0)

    assert menu.state is not None
    start, visible = menu._visible_options(menu.state)
    assert start == 0
    assert len(visible) == 8
    assert menu.height() == 10

    menu.finish(None)
    await task


@pytest.mark.anyio
async def test_menu_stack_keeps_parent_until_child_finishes() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    child_future = []

    def open_child() -> None:
        child_future.append(menu.push(MenuRequest(
            title="Child",
            options=(MenuOption("done", "Done"),),
        )))

    root_task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        options=(MenuOption(
            "open",
            "Open",
            on_select=open_child,
            dismiss_on_select=False,
        ),),
    )))
    await asyncio.sleep(0)

    root_state = menu.state
    menu._choose_index(0)

    assert menu.state is not None
    assert menu.state.request.title == "Child"
    child_state = menu.state
    assert not root_task.done()
    assert len(child_future) == 1

    menu.finish("child")
    assert await child_future[0] == "child"
    assert child_state.completion is ViewCompletion.ACCEPTED
    assert child_state.result == "child"
    assert menu.state is not None
    assert menu.state.request.title == "Root"

    menu.finish("root")
    assert root_state is not None
    assert root_state.completion is ViewCompletion.ACCEPTED
    assert root_state.result == "root"
    assert await root_task == "root"


@pytest.mark.anyio
async def test_menu_child_accept_can_dismiss_marked_parent() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )

    def open_child() -> None:
        menu.push(MenuRequest(
            title="Child",
            options=(MenuOption("done", "Done"),),
        ))

    root_task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        options=(MenuOption(
            "open",
            "Open",
            on_select=open_child,
            dismiss_on_select=False,
            dismiss_parent_on_child_accept=True,
        ),),
    )))
    await asyncio.sleep(0)

    root_state = menu.state
    menu._choose_index(0)
    assert menu.state is not None
    assert menu.state.request.title == "Child"
    child_state = menu.state

    menu.finish("done")

    assert not menu.active
    assert root_state is not None
    assert root_state.completion is ViewCompletion.ACCEPTED
    assert child_state.completion is ViewCompletion.ACCEPTED
    assert await root_task == "done"


@pytest.mark.anyio
async def test_menu_bulk_dismiss_returns_zero_when_no_view_id_matches() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        view_id="root",
        options=(MenuOption("root", "Root"),),
    )))
    await asyncio.sleep(0)

    assert menu.dismiss_views_by_id(("missing", "")) == 0
    assert menu.active

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_child_cancel_clears_parent_completion_marker() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )

    def open_child() -> None:
        menu.push(MenuRequest(
            title="Child",
            options=(MenuOption("done", "Done"),),
        ))

    root_task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        options=(MenuOption(
            "open",
            "Open",
            on_select=open_child,
            dismiss_on_select=False,
            dismiss_parent_on_child_accept=True,
        ),),
    )))
    await asyncio.sleep(0)

    root_state = menu.state
    menu._choose_index(0)
    child_state = menu.state
    menu.cancel()

    assert child_state is not None
    assert child_state.completion is ViewCompletion.CANCELLED
    assert root_state is not None
    assert not root_state.dismiss_after_child_accept
    assert menu.state is root_state

    menu.cancel()
    assert root_state.completion is ViewCompletion.CANCELLED
    assert await root_task is None


@pytest.mark.anyio
async def test_menu_close_cancels_every_pending_frame() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )

    def open_child() -> None:
        menu.push(MenuRequest(
            title="Child",
            options=(MenuOption("done", "Done"),),
        ))

    root_task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        options=(MenuOption(
            "open",
            "Open",
            on_select=open_child,
            dismiss_on_select=False,
        ),),
    )))
    await asyncio.sleep(0)

    root_state = menu.state
    menu._choose_index(0)
    child_state = menu.state
    await menu.close()

    assert root_state is not None
    assert root_state.completion is ViewCompletion.CANCELLED
    assert child_state is not None
    assert child_state.completion is ViewCompletion.CANCELLED
    assert not menu.active
    assert await root_task is None


@pytest.mark.anyio
async def test_menu_callback_push_does_not_complete_new_child_frame() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )

    def open_child() -> None:
        menu.push(MenuRequest(
            title="Child",
            options=(MenuOption("done", "Done"),),
        ))

    root_task = asyncio.create_task(menu.request(MenuRequest(
        title="Root",
        options=(MenuOption(
            "open",
            "Open",
            on_select=open_child,
        ),),
    )))
    await asyncio.sleep(0)

    root_state = menu.state
    menu._choose_index(0)

    assert root_state is not None
    assert root_state.completion is None
    assert menu.state is not None
    assert menu.state.request.title == "Child"
    assert not root_task.done()

    menu.cancel()
    menu.cancel()
    assert await root_task is None


@pytest.mark.anyio
async def test_searchable_menu_uses_filtered_absolute_indices() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        options=(
            MenuOption("one", "Alpha"),
            MenuOption("two", "Beta"),
            MenuOption("three", "Alphabet"),
        ),
    )))
    await asyncio.sleep(0)

    menu._update_query("alp")
    _start, options = menu._visible_options(menu.state)
    assert [option.value for option in options] == ["one", "three"]
    assert menu.state is not None
    assert menu.state.selected == 0

    menu._choose_index(2)
    assert await task == "three"


@pytest.mark.anyio
async def test_searchable_menu_ctrl_w_removes_previous_query_word() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        options=(MenuOption("one", "Alpha"),),
    )))
    await asyncio.sleep(0)

    menu._update_query("alpha beta  ")
    ctrl_w = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == (Keys.ControlW,)
    )
    ctrl_w(None)

    assert menu.state is not None
    assert menu.state.query == "alpha"
    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_searchable_menu_treats_number_keys_as_query_text() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        options=(MenuOption("one", "Item 1"),),
    )))
    await asyncio.sleep(0)

    number = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == ("1",)
    )
    number(SimpleNamespace(data="1"))

    assert menu.state is not None
    assert menu.state.query == "1"
    assert not task.done()
    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_footer_wraps_and_contributes_to_desired_height() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 24,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        footer_note="A long note that wraps",
        footer_hint="Press enter to confirm or esc to go back",
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    lines = text.splitlines()

    assert "  A long note that wraps" in lines
    assert lines[-2:] == [
        "  Press enter to confirm",
        "  or esc to go back",
    ]
    assert all(get_cwidth(line) <= 24 for line in lines)
    assert menu.height() == len(lines)

    menu.cancel()
    await task


@pytest.mark.anyio
async def test_menu_footer_hint_is_hidden_when_cancellation_is_disabled() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        footer_note="Status",
        footer_hint="Press enter to confirm or esc to go back",
        allow_cancel=False,
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    assert "Status" in text
    assert "Press enter" not in text
    assert menu.height() == 5

    menu.cancel()
    await task


@pytest.mark.anyio
async def test_menu_can_replace_legacy_help_row_with_footer_only() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        help_text="",
        footer_hint="Press enter to confirm or esc to go back",
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    lines = _fragments_text(menu.fragments()).splitlines()

    assert lines == [
        "  Options",
        "",
        "› 1. One",
        "  ",
        "  Press enter to confirm or esc to go back",
    ]
    assert menu.height() == 5

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_ignores_cancel_key_when_cancellation_is_disabled() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Required choice",
        allow_cancel=False,
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    handled = menu.handle_key_event(SimpleNamespace(
        key=Keys.Escape,
        key_sequence=(),
        data="",
    ))

    assert handled is False
    assert not task.done()
    menu.finish("one")
    assert await task == "one"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("width", "stacked"),
    ((40, True), (60, False), (100, False)),
)
async def test_menu_stacks_descriptions_only_when_declared_and_narrow(
    width: int,
    stacked: bool,
) -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Key bindings",
        description_layout=(
            MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
        ),
        options=(MenuOption(
            "binding",
            "Configure key binding",
            "设置一个较长的快捷键说明，并确保窄终端中的内容不会越界。",
        ),),
    )))
    await asyncio.sleep(0)

    lines = _fragments_text(menu.fragments()).splitlines()
    option_lines = lines[3:]

    assert (len(option_lines) > 1) is stacked
    assert all(get_cwidth(line) <= width for line in lines)
    assert menu.height() == len(lines)

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_default_menu_description_layout_remains_single_line() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 40,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Commands",
        options=(MenuOption(
            "command",
            "Configure key binding",
            "A description that would stack in the opt-in layout.",
        ),),
    )))
    await asyncio.sleep(0)

    assert len(_fragments_text(menu.fragments()).splitlines()[2:]) == 1

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_searchable_menu_bracketed_paste_is_single_line_query() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        options=(MenuOption("one", "Alpha"),),
    )))
    await asyncio.sleep(0)

    paste = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == (Keys.BracketedPaste,)
    )
    paste(SimpleNamespace(data="alpha\nbeta"))

    assert menu.state is not None
    assert menu.state.query == "alpha beta"
    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_searchable_menu_dims_placeholder_but_not_query() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        search_placeholder="Type to filter",
        options=(MenuOption("one", "Alpha"),),
    )))
    await asyncio.sleep(0)

    assert (
        "class:tui-menu.search.placeholder",
        "Type to filter",
    ) in menu.fragments()

    menu._update_query("alpha")
    assert ("class:tui-menu.search", "alpha") in menu.fragments()

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_current_and_selected_detail_are_rendered_for_active_option() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Model",
        options=(MenuOption(
            "model",
            "gpt",
            "available",
            selected_detail="selected",
            is_current=True,
        ),),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    menu.cancel()
    await task

    assert "gpt (current)" in text
    assert "selected" in text
    assert "available" not in text


@pytest.mark.anyio
async def test_menu_initial_selection_prefers_current_then_default() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Model",
        selected=1,
        options=(
            MenuOption("default", "Default", is_default=True),
            MenuOption("requested", "Requested"),
            MenuOption("current", "Current", is_current=True),
        ),
    )))
    await asyncio.sleep(0)

    assert menu.state is not None
    assert menu.state.selected == 2
    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_refresh_preserves_selected_option_by_value() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Refresh",
        view_id="refresh",
        options=(
            MenuOption("one", "One"),
            MenuOption("two", "Two"),
            MenuOption("three", "Three"),
        ),
    )))
    await asyncio.sleep(0)

    menu._move(1)
    assert menu.state is not None
    assert menu.state.request.options[menu.state.selected].value == "two"

    assert menu.replace_present_if_id(
        "refresh",
        MenuRequest(
            title="Refresh",
            view_id="refresh",
            options=(
                MenuOption("three", "Three"),
                MenuOption("two", "Two"),
                MenuOption("one", "One"),
            ),
        ),
    )
    assert menu.state is not None
    assert menu.state.selected == 1

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_view_generation_is_monotonic_across_refreshes() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Generation",
        view_id="generation",
        options=(MenuOption("one", "One"),),
    )))
    await asyncio.sleep(0)

    assert menu.state is not None
    assert menu.state.request.generation == 1
    assert menu.replace_present_if_id(
        "generation",
        MenuRequest(
            title="Generation",
            view_id="generation",
            options=(MenuOption("one", "One"),),
        ),
    )
    assert menu.state is not None
    assert menu.state.request.generation == 2
    assert not menu.replace_present_if_id(
        "generation",
        MenuRequest(
            title="Stale",
            view_id="generation",
            generation=1,
            options=(MenuOption("one", "One"),),
        ),
    )
    assert menu.state.request.title == "Generation"

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_all_disabled_menu_enter_cancels_without_accepting() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Unavailable",
        options=(
            MenuOption("one", "One", disabled=True, disabled_reason="Busy"),
            MenuOption("two", "Two", disabled=True, disabled_reason="Busy"),
        ),
    )))
    await asyncio.sleep(0)

    text = _fragments_text(menu.fragments())
    enter = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == (Keys.ControlM,)
    )
    enter(None)

    assert "›" not in text
    assert await task is None
    assert menu.state is None


@pytest.mark.anyio
async def test_number_key_chooses_nth_enabled_filtered_option() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        options=(
            MenuOption("disabled", "Disabled", disabled=True),
            MenuOption("one", "One"),
            MenuOption("two", "Two"),
        ),
    )))
    await asyncio.sleep(0)

    choose_first = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == ("1",)
    )
    choose_first(None)

    assert await task == "one"


@pytest.mark.anyio
async def test_searchable_menu_hides_number_gutter_like_codex() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Search",
        searchable=True,
        options=(
            MenuOption("one", "One"),
            MenuOption("two", "Two"),
        ),
    )))
    await asyncio.sleep(0)

    lines = _fragments_text(menu.fragments()).splitlines()
    one = next(line for line in lines if "One" in line)
    assert "1. One" not in one
    assert "› One" in one

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_page_and_end_navigation_clamp_before_trailing_disabled_rows() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Paging",
        options=tuple(
            MenuOption(
                str(index),
                f"Row {index}",
                disabled=index >= 8,
            )
            for index in range(10)
        ),
    )))
    await asyncio.sleep(0)

    menu._move(menu.VISIBLE_ROWS)
    assert menu.state is not None
    assert menu.state.selected == 7

    menu._set_selection(9, direction=-1)
    assert menu.state.selected == 7

    menu._move(-menu.VISIBLE_ROWS)
    assert menu.state.selected == 0

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_disabled_reason_uses_disabled_gutter_and_enabled_numbering() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Options",
        options=(
            MenuOption(
                "blocked",
                "Blocked",
                disabled_reason="Busy",
                disabled_gutter_marker="×",
            ),
            MenuOption("available", "Available"),
        ),
    )))
    await asyncio.sleep(0)

    lines = _fragments_text(menu.fragments()).splitlines()
    blocked = next(line for line in lines if "Blocked" in line)
    available = next(line for line in lines if "Available" in line)
    assert "×" in blocked
    assert "1." not in blocked
    assert "› 1. Available" in available
    assert menu.state is not None
    assert menu.state.selected == 1

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_menu_tabs_reset_query_selection_and_footer_hint() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Pick",
        searchable=True,
        tabs=(
            MenuTab(
                "first",
                "First",
                options=(MenuOption("alpha", "Alpha"),),
                footer_hint="First hint",
            ),
            MenuTab(
                "second",
                "Second",
                options=(MenuOption("beta", "Beta"),),
                footer_hint="Second hint",
            ),
        ),
    )))
    await asyncio.sleep(0)

    menu.handle_key_event(SimpleNamespace(key="b", data="b"))
    assert menu.state is not None
    assert menu.state.query == "b"
    menu.handle_key_event(SimpleNamespace(key="right", data=""))

    assert menu.active_tab_id() == "second"
    assert menu.state is not None
    assert menu.state.query == ""
    assert menu.state.selected == 0
    text = _fragments_text(menu.fragments())
    assert "[Second]" in text
    assert "Beta" in text
    assert "First hint" not in text
    assert "Second hint" in text

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode",
    (
        MenuColumnWidthMode.AUTO_VISIBLE,
        MenuColumnWidthMode.AUTO_ALL_ROWS,
        MenuColumnWidthMode.FIXED,
    ),
)
async def test_menu_column_width_modes_keep_rendered_rows_within_width(
    mode: MenuColumnWidthMode,
) -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 40,
    )
    task = asyncio.create_task(menu.request(MenuRequest(
        title="Columns",
        column_width_mode=mode,
        options=(
            MenuOption("short", "Short", detail="small"),
            MenuOption("long", "A much longer label", detail="description"),
        ),
    )))
    await asyncio.sleep(0)

    lines = _fragments_text(menu.fragments()).splitlines()
    assert all(get_cwidth(line) <= 40 for line in lines)
    assert any("Short" in line for line in lines)
    assert any("A much" in line for line in lines)

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("width", "menu_request", "expected"),
    (
        (
            40,
            MenuRequest(
                title="Columns",
                description_layout=(
                    MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
                ),
                options=(
                    MenuOption("one", "Short", detail="这是一个很长的中文描述"),
                    MenuOption("two", "Long label", detail="description"),
                ),
                footer_hint="Press enter",
            ),
            "\n".join((
                "  Columns",
                "",
                "› 1. Short",
                "     这是一个很长的中文描述",
                "  2. Long label",
                "     description",
                "  ",
                "  Press enter",
            )),
        ),
        (
            60,
            MenuRequest(
                title="Options",
                status="ready",
                options=(
                    MenuOption("one", "One", detail="First"),
                    MenuOption(
                        "blocked",
                        "Blocked",
                        disabled_reason="Busy",
                        disabled_gutter_marker="×",
                    ),
                    MenuOption(
                        "two",
                        "Two",
                        detail="Second",
                        is_current=True,
                    ),
                ),
                footer_note="Status",
                footer_hint="Press enter",
            ),
            "\n".join((
                "  Options",
                "  ready",
                "",
                "  1. One            First",
                "  ×  Blocked        Busy",
                "› 2. Two (current)  Second",
                "  ",
                "  Status",
                "  Press enter",
            )),
        ),
        (
            100,
            MenuRequest(
                title="Long menu",
                options=(
                    MenuOption(
                        "first",
                        "First option",
                        detail="A descriptive value",
                    ),
                    MenuOption(
                        "second",
                        "Second option",
                        detail="Another descriptive value",
                    ),
                ),
                footer_note="All options visible",
                footer_hint="Press enter",
            ),
            "\n".join((
                "  Long menu",
                "",
                "› 1. First option   A descriptive value",
                "  2. Second option  Another descriptive value",
                "  ",
                "  All options visible",
                "  Press enter",
            )),
        ),
        (
            60,
            MenuRequest(
                title="Empty",
                body=("No options available",),
                footer_hint="Press enter",
            ),
            "\n".join((
                "  Empty",
                "  No options available",
                "  ",
                "  Press enter",
            )),
        ),
        (
            60,
            MenuRequest(
                title="Disabled",
                options=(
                    MenuOption("a", "A", disabled=True, disabled_reason="Busy"),
                    MenuOption("b", "B", disabled=True),
                ),
                footer_hint="Press enter",
            ),
            "\n".join((
                "  Disabled",
                "",
                "     A  Busy",
                "     B",
                "  ",
                "  Press enter",
            )),
        ),
    ),
)
async def test_menu_text_snapshots_cover_visible_states(
    width: int,
    menu_request: MenuRequest,
    expected: str,
) -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: width,
    )
    task = asyncio.create_task(menu.request(menu_request))
    await asyncio.sleep(0)

    assert _fragments_text(menu.fragments()) == expected

    menu.cancel()
    assert await task is None


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)
