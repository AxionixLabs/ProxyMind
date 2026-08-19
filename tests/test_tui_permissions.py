# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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
        select_menu=AsyncMock(return_value="auto"),
    )

    selected = await choose_permissions_mode(
        runtime,
        preset_permissions("auto"),
    )

    request = runtime.select_menu.await_args.args[0]
    assert selected == preset_permissions("auto")
    assert request.view_id == "permissions:root"
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert [option.is_current for option in request.options] == [
        False,
        True,
        False,
    ]


def test_elevated_permissions_status_renders_on_one_line() -> None:
    application = SimpleNamespace(emit=Mock())

    render_permissions_status(application, preset_permissions("full-access"))

    status, gap = (
        call.args[0]
        for call in application.emit.call_args_list
    )
    text = "".join(value for _style, value in status.renderable.fragments)

    assert status.type == "tui.permissions.status"
    assert text == (
        "/permissions · Full Access · sandbox=danger-full-access"
        " · approval=never"
    )
    assert gap.type == "tui.gap"


def test_elevated_permissions_use_prominent_footer_style() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
        model="test-model",
        permissions_label="Full Access",
    )

    fragments = runtime.screen._footer_fragments()

    assert ("class:footer.access.full", "Full Access") in fragments
