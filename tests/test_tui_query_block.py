# -*- coding: utf-8 -*-

import asyncio
import pytest

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.models import MenuOption, MenuRequest
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.styles import query_block


def test_slash_command_uses_purple_transcript_style() -> None:
    block = query_block("/permissions")

    assert block.fragments == (
        ("class:prompt.command.slash", "/permissions"),
    )


def test_non_slash_input_keeps_existing_transcript_style() -> None:
    shell = query_block("!git status")
    message = query_block("hello")

    assert shell.fragments == (("class:prompt", "!git status"),)
    assert message.fragments == (
        ("class:prompt.kicker", "› "),
        ("class:prompt", "hello"),
    )


def test_query_transcript_uses_regular_font_weight() -> None:
    style = TuiRuntime().input_model.style

    assert not style.get_attrs_for_style_str("class:prompt").bold


def test_unknown_slash_text_is_not_styled_as_a_command() -> None:
    block = query_block("/今天天气")

    assert block.fragments == (
        ("class:prompt.kicker", "› "),
        ("class:prompt", "/今天天气"),
    )


def test_unknown_slash_command_stays_editable_and_never_enters_queue() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.text = "/今天天气"

    keep_text = runtime.submissions.accept_input(buffer)

    assert keep_text
    assert buffer.text == "/今天天气"
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active
    assert runtime.document.blocks[-1].kind == "notice"
    assert runtime.document.blocks[-1].display_block.fragments == (
        ("class:input.notice.hint", "•"),
        (
            "class:input.notice.hint",
            " Unrecognized command '/今天天气'. "
            'Type "/" for a list of supported commands.',
        ),
    )
    assert "".join(
        text for _style, text in runtime.document.blocks[-1].display_block.fragments
    ) == (
        "• Unrecognized command '/今天天气'. "
        'Type "/" for a list of supported commands.'
    )


def test_root_slash_is_rejected_with_hint_before_queueing() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.text = "/"

    keep_text = runtime.submissions.accept_input(buffer)

    assert not keep_text
    assert buffer.text == ""
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active
    assert "".join(
        text for _style, text in runtime.document.blocks[-1].display_block.fragments
    ) == (
        "• Choose a slash command from the menu or type its full name."
    )


@pytest.mark.parametrize("text", ("", "   "))
def test_empty_message_without_attachments_is_silent(text: str) -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.text = text

    buffer.validate_and_handle()

    assert buffer.text == ""
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active
    assert not runtime.document.blocks


def test_surface_command_blanks_footer_as_soon_as_it_is_submitted() -> None:
    runtime = TuiRuntime()
    runtime.screen.input.buffer.text = "/ps"

    runtime.screen.input.buffer.validate_and_handle()

    assert runtime.submissions.surface_submission_pending
    assert runtime.screen._footer_visible()
    assert runtime.screen._footer_fragments() == []
    assert not runtime.document.blocks


@pytest.mark.anyio
async def test_empty_message_with_attachments_skips_empty_query_block() -> None:
    runtime = TuiRuntime()
    runtime.bind_pending_attachment_check(lambda: True)

    runtime.submissions.accept_input(runtime.screen.input.buffer)
    value = await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert value == ""
    assert not runtime.document.blocks


@pytest.mark.anyio
async def test_surface_command_is_staged_without_showing_default_footer() -> None:
    runtime = TuiRuntime()
    runtime.submissions.message_queue.put_nowait("/ps")

    value = await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert value == "/ps"
    assert runtime.document.has_pending_submission
    assert not runtime.document.blocks
    assert runtime.screen._footer_visible()
    assert runtime.screen._footer_fragments() == []
    assert runtime.screen._footer_height() == 1


@pytest.mark.anyio
async def test_menu_discards_staged_command_before_activating() -> None:
    runtime = TuiRuntime()
    runtime.submissions.message_queue.put_nowait("/mcp")
    await runtime.read_message(PromptContext(mode="chat", model="test"))

    task = asyncio.create_task(runtime.select_menu(
        MenuRequest(
            title="External MCP",
            options=(MenuOption(value="status", label="status"),),
        )
    ))
    await asyncio.sleep(0)

    assert not runtime.document.has_pending_submission
    assert not runtime.document.blocks
    assert runtime.screen.bottom_pane.active_surface == "menu"
    assert not runtime.screen._footer_visible()

    runtime.finish_menu("status")
    assert await task == "status"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    ["/fast", "/model gpt-test", "/mcp status", "/q", "quit"],
)
async def test_registered_command_stays_pending_until_dispatch_finishes(
    command,
) -> None:
    runtime = TuiRuntime()
    runtime.submissions.message_queue.put_nowait(command)

    await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert runtime.document.has_pending_submission
    assert not runtime.document.blocks

    runtime.discard_pending_submission()

    assert not runtime.document.has_pending_submission
    assert not runtime.document.has_conversation
