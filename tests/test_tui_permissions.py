# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.permissions import render_permissions_status
from mind_core.permissions import preset_permissions


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
        mode="chat",
        model="test-model",
        permissions_label="Full Access",
    )

    fragments = runtime.screen._footer_fragments()

    assert ("class:footer.access.full", "Full Access") in fragments
