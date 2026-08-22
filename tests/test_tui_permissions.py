# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mind_nova import const

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.models import (
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.permissions import (
    choose_permissions_mode,
    render_permissions_status,
)
from mind_core.permissions import preset_permissions


@pytest.mark.anyio
async def test_permissions_menu_uses_primary_selection_contract() -> None:
    runtime = SimpleNamespace(
        select_menu=AsyncMock(return_value="ask-for-approval"),
    )

    selected = await choose_permissions_mode(
        runtime,
        preset_permissions("auto"),
    )

    request = runtime.select_menu.await_args.args[0]
    assert selected == preset_permissions("auto")
    assert request.view_id == "permissions:root"
    assert request.title == "Update Model Permissions · Ask for approval"
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert not any(option.is_current for option in request.options)
    assert [option.label for option in request.options] == [
        "Read Only",
        "Ask for approval",
        "Approve for me",
        "Full Access",
    ]


@pytest.mark.anyio
async def test_permissions_menu_applies_non_full_modes_without_confirmation() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(
        choose_permissions_mode(runtime, preset_permissions("read-only")),
    )
    await asyncio.sleep(0)

    root = runtime.screen.menu.state
    assert root is not None
    assert root.request.options[0].dismiss_on_select is True
    runtime.screen.menu._choose_index(1)
    assert await task == preset_permissions("auto")
    assert not runtime.screen.menu.active


@pytest.mark.anyio
async def test_full_access_opens_confirmation_child_and_applies_mode() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(
        choose_permissions_mode(runtime, preset_permissions("read-only")),
    )
    await asyncio.sleep(0)

    root = runtime.screen.menu.state
    assert root is not None
    assert root.request.view_id == "permissions:root"
    assert root.request.options[3].dismiss_on_select is False
    assert root.request.options[3].dismiss_parent_on_child_accept is True

    runtime.screen.menu._choose_index(3)
    await asyncio.sleep(0)

    child = runtime.screen.menu.state
    assert child is not None
    assert child.request.view_id == "permissions:confirm:full-access"
    assert child.request.title == "Enable full access?"
    assert child.request.body == (
        f"When {const.APP_DESC} runs with full access, it can edit any file on your computer "
        "and run commands with network, without your approval.",
        "",
    )
    assert child.request.body_warning == (
        "Exercise caution when enabling full access. This significantly increases "
        "the risk of data loss, leaks, or unexpected behavior."
    )
    assert not task.done()

    runtime.screen.menu._choose_index(0)
    assert await task == preset_permissions("full-access")
    assert not runtime.screen.menu.active


@pytest.mark.anyio
async def test_permissions_confirmation_escape_returns_to_root() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(
        choose_permissions_mode(runtime, preset_permissions("read-only")),
    )
    await asyncio.sleep(0)

    runtime.screen.menu._set_selection(3)
    runtime.screen.menu._choose_index(3)
    await asyncio.sleep(0)
    assert runtime.screen.menu.active_view_id() == "permissions:confirm:full-access"

    runtime.cancel_menu()
    assert runtime.screen.menu.active_view_id() == "permissions:root"
    assert runtime.screen.menu.state is not None
    assert runtime.screen.menu.state.selected == 3
    assert not task.done()

    runtime.cancel_menu()
    assert await task is None


def test_elevated_permissions_status_renders_on_one_line() -> None:
    application = SimpleNamespace(emit=Mock())

    render_permissions_status(application, preset_permissions("full-access"))

    status, gap = (
        call.args[0]
        for call in application.emit.call_args_list
    )
    text = "".join(value for _style, value in status.renderable.fragments)

    assert status.type == "tui.permissions.status"
    assert text == "• Permissions updated to Full Access"
    assert status.renderable.fragments[0] == ("dim", "• ")
    assert status.renderable.fragments[1][1] == "Permissions updated to Full Access"
    assert status.renderable.fragments[1][0] == ""
    assert gap.type == "tui.gap"


def test_permission_status_uses_selected_display_label() -> None:
    application = SimpleNamespace(emit=Mock())

    render_permissions_status(
        application,
        preset_permissions("auto", display_label="Approve for me"),
    )

    status = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in status.renderable.fragments)

    assert text == "• Permissions updated to Approve for me"
    assert status.renderable.fragments == (
        ("dim", "• "),
        ("", "Permissions updated to Approve for me"),
    )


def test_elevated_permissions_use_prominent_footer_style() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
        model="test-model",
        permissions_label="Full Access",
    )

    fragments = runtime.screen._footer_fragments()

    assert ("class:footer.access.full", "Full Access") in fragments
