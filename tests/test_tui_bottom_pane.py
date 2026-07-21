# -*- coding: utf-8 -*-

import asyncio

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_app.tui.core.bottom_pane import TuiBottomPane
from mind_app.tui.core.models import FragmentBlock, MenuOption, MenuRequest
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


@pytest.mark.anyio
async def test_approval_temporarily_replaces_menu_surface() -> None:
    runtime = TuiRuntime()
    runtime.append_block(FragmentBlock((("", "command query"),)), kind="user")
    menu_task = asyncio.create_task(runtime.select_menu(MenuRequest(
        title="Model",
        options=(MenuOption("gpt-test", "gpt-test"),),
    )))
    await asyncio.sleep(0)

    assert runtime._content_input_gap_height() == 2

    approval_task = asyncio.create_task(runtime.request_approval({
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    }))
    await asyncio.sleep(0)

    assert runtime.bottom_pane.active_surface == "approval"
    assert runtime._content_input_gap_height() == 1
    assert runtime.approval_card.filter()
    assert not runtime.menu_card.filter()
    assert runtime._menu_height() == 0
    assert runtime._approval_height() > 0

    runtime.approval.finish("decline")
    assert await approval_task == "decline"
    assert runtime.bottom_pane.active_surface == "menu"
    assert runtime._content_input_gap_height() == 2
    assert runtime.menu_card.filter()

    runtime.menu.finish(None)
    assert await menu_task is None
    assert runtime.bottom_pane.active_surface is None
    assert runtime.input_area.filter()


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["q", "\x1b"])
async def test_menu_closes_from_terminal_cancel_key(key: str) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        assert runtime.application.ttimeoutlen == 0.1
        await runtime.open()
        try:
            menu_task = asyncio.create_task(runtime.select_menu(MenuRequest(
                title="Model",
                options=(MenuOption("gpt-test", "gpt-test"),),
            )))
            await asyncio.sleep(0)

            pipe_input.send_text(key)

            assert await asyncio.wait_for(menu_task, timeout=1.0) is None
            assert runtime.bottom_pane.active_surface is None
        finally:
            await runtime.close()
