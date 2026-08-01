# -*- coding: utf-8 -*-

import asyncio

import pytest

from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.menu import (
    TUI_MENU_STYLE,
    TuiMenu,
)
from mind_app.tui.core.models import MenuOption, MenuRequest


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
    lines = _fragments_text(menu.fragments()).splitlines()[2:]
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
    assert "…" in lines[2]
    separators = [get_cwidth(line.split(" · ", 1)[0]) for line in lines[2:]]
    assert len(set(separators)) == 1


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
        "class:tui-menu.detail",
    ]
    assert TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.label.active"
    ).bgcolor == "1D3A4D"
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


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)
