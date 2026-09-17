import asyncio
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.domain.mcp_elicitation import (
    ElicitationField,
    ElicitationRequest,
    McpInvocation,
)
from frontends.tui.contracts.menu import (
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.mcp_elicitation import present_elicitation


def form(fields):
    return ElicitationRequest("input", "fixture", McpInvocation("s", "t", "c", "root"), "Ordinary information", fields=fields)


@pytest.mark.anyio
async def test_form_edits_validates_and_reviews_defaults_before_submit():
    request = form((
        ElicitationField("name", "Name", "", "string", True, min_length=1),
        ElicitationField("count", "Count", "", "integer", False, default=2, minimum=1, maximum=5),
        ElicitationField("ok", "Confirmed", "", "boolean", False, default=False),
    ))
    select = AsyncMock(side_effect=["accept", "field:0", "Tester", "field:1", "99", "3", "accept"])
    response = await present_elicitation(request, select)
    assert response.action == "accept"
    assert dict(response.content) == {"name": "Tester", "count": 3, "ok": False}
    menus = [call.args[0] for call in select.await_args_list]
    assert menus[1].status == "Complete the required fields before submitting."
    assert "Maximum: 5" in menus[4].body
    assert menus[5].status
    assert menus[-1].options[0].detail == "Tester"
    assert menus[-1].options[1].detail == "3"


@pytest.mark.anyio
async def test_array_enum_and_optional_boolean_can_be_reviewed_and_unset():
    request = form((
        ElicitationField("colors", "Colors", "", "array", True, choices=(("red", "Red"), ("blue", "Blue")), min_items=1),
        ElicitationField("ok", "Confirmed", "", "boolean", False, default=True),
    ))
    select = AsyncMock(side_effect=["field:0", "done", "choice:1", "done", "field:1", "unset", "accept"])
    response = await present_elicitation(request, select)
    assert dict(response.content) == {"colors": ("blue",)}
    assert select.await_args_list[2].args[0].status
    assert select.await_args_list[3].args[0].selected == 1


@pytest.mark.anyio
@pytest.mark.parametrize("field,current,expected", [
    (ElicitationField("ok", "Confirmed", "", "boolean", True, default=False), "false", 1),
    (ElicitationField("ok", "Confirmed", "", "boolean", True, default=True), "true", 0),
    (ElicitationField("color", "Color", "", "string", True, default="blue", choices=(("red", "Red"), ("blue", "Blue"))), "choice:1", 1),
])
async def test_field_initial_selection_preserves_current_value(field, current, expected):
    select = AsyncMock(side_effect=["field:0", current, "accept"])
    response = await present_elicitation(form((field,)), select)
    menu = select.await_args_list[1].args[0]
    assert menu.selected == expected
    assert dict(response.content) == {field.name: field.default}


@pytest.mark.anyio
@pytest.mark.parametrize("decision,expected", [("decline", "decline"), (None, "cancel"), ("cancel", "cancel")])
async def test_decline_and_cancel_do_not_include_default_answers(decision, expected):
    response = await present_elicitation(form((ElicitationField("name", "Name", "", "string", False, default="Tester"),)),
        AsyncMock(return_value=decision))
    assert response.action == expected and not response.content


@pytest.mark.anyio
async def test_url_shows_source_and_destination_before_returning_consent():
    request = ElicitationRequest("url", "fixture", McpInvocation("s", "t", "c", "worker"), "Continue in browser",
        url="https://example.org/authorize", url_host="example.org", elicitation_id="site")
    select = AsyncMock(return_value="accept")
    assert (await present_elicitation(request, select)).action == "accept"
    menu = select.await_args.args[0]
    assert "fixture" in menu.body[0] and "worker" in menu.body[0]
    assert "Destination: example.org" in menu.body
    assert request.url in menu.body


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["", "  preserve whitespace  ", "first\nsecond"])
async def test_form_strings_preserve_empty_whitespace_and_multiline_values(value):
    request = form((ElicitationField("text", "Text", "", "string", True),))
    select = AsyncMock(side_effect=["field:0", value, "accept"])
    response = await present_elicitation(request, select)
    assert dict(response.content) == {"text": value}
    menu = select.await_args_list[1].args[0]
    assert menu.text_input_allow_empty and menu.text_input_preserve_whitespace
    assert menu.text_input_max_rows == 4
    if not value:
        assert select.await_args_list[2].args[0].options[0].detail == '""'


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.anyio
async def test_real_keyboard_bounded_paste_and_cancellation_restore_underlying_menu():
    with create_pipe_input() as terminal:
        runtime = TuiRuntime(input_obj=terminal, output_obj=DummyOutput())
        coordinator = ApprovalCoordinator(runtime)
        activity = AsyncMock()
        await runtime.bind_approval_activity(activity)
        await runtime.open()
        parent = asyncio.create_task(runtime.select_menu(MenuRequest(title="Original menu", options=(MenuOption("keep", "Keep"),))))
        task = None
        try:
            await wait_until(lambda: runtime.screen.menu.active)
            task = asyncio.create_task(coordinator.request_elicitation(form((ElicitationField("name", "Name", "", "string", True),))))
            await wait_until(lambda: runtime.screen.menu.state.request.view_id == "mcp-elicitation")
            terminal.send_text("\r")
            await wait_until(lambda: runtime.screen.menu.state.request.title == "Name")
            terminal.send_text("\x1b[200~" + "x" * 5000 + "\x1b[201~")
            await wait_until(lambda: len(runtime.screen.menu.state.query) == 4096)
            assert runtime.screen.bottom_pane.active_surface == "menu"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await wait_until(lambda: runtime.screen.menu.state.request.title == "Original menu")
            await wait_until(lambda: not runtime.screen.approval.active)
            assert runtime.screen.bottom_pane.active_surface == "menu"
            assert not parent.done()
            assert [call.args[0] for call in activity.await_args_list] == [True, False]
            terminal.send_text("\r")
            assert await asyncio.wait_for(parent, 3) == "keep"
        finally:
            if task is not None:
                task.cancel()
            parent.cancel()
            await coordinator.close()
            await asyncio.gather(*(item for item in (task, parent) if item is not None), return_exceptions=True)
            await runtime.close()
