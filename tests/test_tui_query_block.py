# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import Mock, patch
import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.document import TuiDocument
from mind_app.tui.core.models import FragmentBlock, MenuOption, MenuRequest
from mind_app.tui.core.render import (
    fragments_text,
    split_formatted_lines
)
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


def test_submitted_query_has_transparent_padding_before_response() -> None:
    document = TuiDocument()
    document.append_block(
        FragmentBlock((("", "─ Worked for 1m ─"),)),
        kind="system",
    )
    document.append_block(query_block("lock this behavior"), kind="user")
    document.append_block(
        FragmentBlock((("", "• response"),)),
        kind="assistant",
    )

    lines = split_formatted_lines(document.fragments(width=80))

    assert lines == [
        [("", "─ Worked for 1m ─")],
        [],
        [("", " ")],
        [
            ("class:prompt.kicker", "› "),
            ("class:prompt", "lock this behavior"),
        ],
        [("", " ")],
        [],
        [("", "• response")],
    ]


def test_submitted_query_keeps_bottom_padding_while_last_cell() -> None:
    document = TuiDocument()
    document.append_block(query_block("waiting for response"), kind="user")

    assert split_formatted_lines(document.fragments(width=80)) == [
        [("", " ")],
        [
            ("class:prompt.kicker", "› "),
            ("class:prompt", "waiting for response"),
        ],
        [("", " ")],
    ]


def test_consecutive_user_cells_keep_history_boundary_separator() -> None:
    document = TuiDocument()
    document.append_block(query_block("first question"), kind="user")
    document.append_block(query_block("second question"), kind="user")

    assert split_formatted_lines(document.fragments(width=80)) == [
        [("", " ")],
        [
            ("class:prompt.kicker", "› "),
            ("class:prompt", "first question"),
        ],
        [("", " ")],
        [],
        [("", " ")],
        [
            ("class:prompt.kicker", "› "),
            ("class:prompt", "second question"),
        ],
        [("", " ")],
    ]


def test_content_surface_gap_is_independent_of_transcript_tail_kind() -> None:
    runtime = TuiRuntime()
    runtime.append_block(query_block("waiting for response"), kind="user")

    assert runtime.document.visible_tail_kind == "user"
    assert runtime.screen._bottom_pane_top_inset_height() == 1

    runtime.append_block(
        FragmentBlock((("", "• response"),)),
        kind="assistant",
    )

    assert runtime.document.visible_tail_kind == "assistant"
    assert runtime.screen._bottom_pane_top_inset_height() == 1


def test_visible_tail_kind_uses_stable_cache_and_restores_with_document() -> None:
    document = TuiDocument()
    document.append_block(query_block("question"), kind="user")
    snapshot = document.capture_state()

    document.append_block(FragmentBlock(()), kind="notice")
    assert document.visible_tail_kind == "user"

    document.append_block(
        FragmentBlock((("", "• answer"),)),
        kind="assistant",
    )

    with patch.object(
        document,
        "_block_lines",
        side_effect=AssertionError("stable tail kind rescanned blocks"),
    ):
        assert document.visible_tail_kind == "assistant"

    document.restore_state(snapshot)
    assert document.visible_tail_kind == "user"


def test_submitted_query_padding_replaces_activity_top_gap() -> None:
    runtime = TuiRuntime()
    runtime.append_block(query_block("waiting for response"), kind="user")
    runtime.screen.set_activity_renderable(
        FragmentBlock((("", "• Thinking"),))
    )

    assert runtime.screen._bottom_pane_top_inset_visible()
    assert runtime.screen._status_interaction_gap_height() == 1


def test_query_transcript_uses_regular_font_weight() -> None:
    style = TuiRuntime().input_model.style

    assert not style.get_attrs_for_style_str("class:prompt").bold


def test_unknown_slash_text_is_not_styled_as_a_command() -> None:
    block = query_block("/今天天气")

    assert block.fragments == (
        ("class:prompt.kicker", "› "),
        ("class:prompt", "/今天天气"),
    )


def test_submitted_query_bypasses_command_styling_and_preserves_source() -> None:
    runtime = TuiRuntime()
    prompt = "/permissions\n  !printf '你好'\n$HOME"

    assert runtime.append_submitted_query(prompt, "turn_remote")

    cell = runtime.document.blocks[0]
    transcript = fragments_text(cell.transcript_block.fragments)

    assert cell.kind == "user"
    assert cell.turn_id == "turn_remote"
    assert cell.prompt == prompt
    assert cell.raw_text == prompt
    assert transcript == "› /permissions\n    !printf '你好'\n  $HOME"
    assert all(
        style != "class:prompt.command.slash"
        for style, _text in cell.transcript_block.fragments
    )


def test_submitted_query_preview_is_width_aware_and_transcript_is_complete() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    prompt = (
        "!python3 -c \"print('" + "界" * 18 + "')\"\n"
        + "emoji " + "👩‍💻" * 12 + "\n"
        + "final marker"
    )

    runtime.append_submitted_query(
        prompt,
        "turn_long",
        max_display_rows=4,
    )

    cell = runtime.document.blocks[0]
    narrow = fragments_text(cell.display_block.fragments)
    transcript = fragments_text(cell.transcript_block.fragments)

    assert len(narrow.splitlines()) == 4
    assert narrow.startswith("› !python3")
    assert "lines Ctrl+T" in narrow
    assert all(get_cwidth(line) <= 20 for line in narrow.splitlines())
    assert "final marker" not in narrow
    assert "final marker" in transcript
    assert cell.raw_text == prompt

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    assert "final marker" in fragments_text(overlay.fragments())
    overlay.toggle_raw_mode()
    assert "final marker" in fragments_text(overlay.fragments())
    overlay.begin_search()
    overlay.append_search_text("final marker")
    assert overlay.confirm_search()
    assert overlay.search_result_position == (1, 1)
    runtime.toggle_transcript_overlay()

    assert runtime.document.set_display_width(48, reflow_sources=True)
    wide = fragments_text(runtime.document.fragments(width=48))

    assert wide != narrow
    assert "(Ctrl+T to view transcript)" in wide
    assert all(get_cwidth(line) <= 48 for line in wide.splitlines())
    assert fragments_text(cell.transcript_block.fragments) == transcript
    assert cell.raw_text == prompt


def test_submitted_query_commits_with_one_visual_invalidation() -> None:
    runtime = TuiRuntime()
    runtime.screen._invalidate_now = Mock()

    runtime.append_submitted_query("remote query", "turn_remote")

    runtime.screen._invalidate_now.assert_called_once_with()


def test_submitted_query_reflow_keeps_terminal_controls_off_canvas() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    prompt = "query\x1b]52;c;payload\x1b\\ " + "界" * 40

    runtime.append_submitted_query(
        prompt,
        "turn_control",
        max_display_rows=3,
    )

    cell = runtime.document.blocks[0]
    narrow = fragments_text(cell.display_block.fragments)
    assert "\x1b" not in narrow
    assert "payload" not in narrow

    runtime.document.set_display_width(60, reflow_sources=True)
    wide = fragments_text(runtime.document.fragments(width=60))

    assert "\x1b" not in wide
    assert "payload" not in wide
    assert cell.raw_text == prompt


@pytest.mark.anyio
@pytest.mark.parametrize("value", ("/今天天气", "/"))
async def test_invalid_slash_uses_submission_boundary_without_query_block(
    value,
) -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    read_task = asyncio.create_task(runtime.read_message(PromptContext(model="test")))
    buffer.text = value
    buffer.validate_and_handle()

    assert await read_task == value
    assert buffer.text == ""
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active
    assert runtime.input_model.history.get_strings() == []
    assert not runtime.document.blocks
    assert not runtime.document.has_pending_submission


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
    value = await runtime.read_message(PromptContext(model="test"))

    assert value == ""
    assert not runtime.document.blocks


@pytest.mark.anyio
async def test_surface_command_is_staged_without_showing_default_footer() -> None:
    runtime = TuiRuntime()
    runtime.submissions.message_queue.put_nowait("/ps")

    value = await runtime.read_message(PromptContext(model="test"))

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
    await runtime.read_message(PromptContext(model="test"))

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
    ["/new", "/model gpt-test", "/mcp status", "/q", "quit"],
)
async def test_registered_command_stays_pending_until_dispatch_finishes(
    command,
) -> None:
    runtime = TuiRuntime()
    runtime.submissions.message_queue.put_nowait(command)

    await runtime.read_message(PromptContext(model="test"))

    assert runtime.document.has_pending_submission
    assert not runtime.document.blocks

    runtime.discard_pending_submission()

    assert not runtime.document.has_pending_submission
    assert not runtime.document.has_conversation
