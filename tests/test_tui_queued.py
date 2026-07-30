# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth
from unittest.mock import Mock

from mind_app.frontend import ApplicationView
from mind_app.interaction.contracts import PromptContext
from mind_app.tui.adapters.application import TuiApplicationSink
from mind_app.tui.core.queued import TuiQueuedMessages, TuiSubmission
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.styles import text_block


def test_queue_uses_next_turn_title() -> None:
    queue = TuiQueuedMessages()
    queue.append(_submission("next task"))

    text = _fragments_text(queue.fragments(width=100))

    assert "• Messages queued for the next turn (Esc edits latest)" in text
    assert "  ↳ next task" in text


def test_queued_input_candidate_uses_dim_style() -> None:
    style = TuiRuntime().screen.application.style

    assert style.get_attrs_for_style_str("class:queue.text").dim


def test_multiline_message_is_flattened_and_ellipsized() -> None:
    queue = TuiQueuedMessages()
    queue.append(_submission("first line\nsecond line with a long suffix"))

    text = _fragments_text(queue.fragments(width=28))
    lines = text.splitlines()

    assert len(lines) == 2
    assert lines[1].endswith("…")
    assert all(get_cwidth(line) <= 28 for line in lines)


def test_queue_reserves_last_row_for_hidden_count() -> None:
    queue = TuiQueuedMessages()
    for index in range(7):
        queue.append(_submission(f"message {index + 1}"))

    text = _fragments_text(queue.fragments(width=100, max_rows=6))
    lines = text.splitlines()

    assert len(lines) == 6
    assert lines[-1] == "    … 3 more"
    assert "message 5" not in text


def test_running_input_replaces_information_footer_with_queue_hint() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
        mode="chat",
        model="gpt-test high",
        permissions_label="Full Access",
        workspace_label="ProxyMind",
    )
    runtime.execution_active = True
    runtime.submissions.queued_messages.append(_submission("already queued"))
    runtime.screen.input.buffer.text = "next task"

    text = _fragments_text(runtime.screen._footer_fragments())

    assert runtime.screen._footer_visible()
    assert text == "  tab to queue message"
    assert runtime.context.model not in text
    assert runtime.context.permissions_label not in text
    assert runtime.context.workspace_label not in text


def test_queued_submission_restores_information_footer() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
        mode="chat",
        model="gpt-test high",
        permissions_label="Full Access",
        workspace_label="ProxyMind",
    )
    runtime.execution_active = True
    runtime.screen.input.buffer.text = "queued task"

    runtime.screen.input.buffer.validate_and_handle()

    text = _fragments_text(runtime.screen._footer_fragments())

    assert runtime.screen._footer_visible()
    assert "tab to queue message" not in text
    assert "gpt-test high" in text
    assert "Full Access" in text
    assert "ProxyMind" in text

    runtime.screen.input.buffer.text = "another task"

    assert (
        _fragments_text(runtime.screen._footer_fragments())
        == "  tab to queue message"
    )


def test_foreground_barrier_keeps_normal_input_in_visible_queue() -> None:
    runtime = TuiRuntime()
    runtime.set_foreground_active(True)
    runtime.screen.input.buffer.text = "next task"

    runtime.screen.input.buffer.validate_and_handle()

    assert runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()


@pytest.mark.anyio
async def test_queued_paste_restores_editable_state_and_reuses_number() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    first_text = "a" * 1200
    second_text = "b" * 1200
    replacement_text = "c" * 1200

    first = runtime.input_model._display_paste(first_text, "")
    second = runtime.input_model._display_paste(second_text, first)
    runtime.screen.input.buffer.text = f"{first}\n{second}"

    runtime.screen.input.buffer.validate_and_handle()

    assert runtime.submissions.queued_messages.active
    assert runtime.input_model.submission_state() == {}
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == f"{first}\n{second}"
    assert set(runtime.input_model.submission_state()) == {first, second}
    assert runtime.input_model.history.get_strings() == []

    replacement = runtime.input_model._display_paste(
        replacement_text,
        second,
    )
    assert replacement == first
    runtime.screen.input.buffer.text = f"{second}\n{replacement}"
    runtime.screen.input.buffer.validate_and_handle()

    value = await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert value == f"{second_text}\n{replacement_text}"
    assert runtime.input_model.submission_state() == {}


@pytest.mark.anyio
async def test_queued_pastes_keep_independent_placeholder_snapshots() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    contents = ("a" * 1200, "b" * 1200)

    for content in contents:
        placeholder = runtime.input_model._display_paste(content, "")
        assert placeholder == "[Pasted Content 1200 chars]"
        runtime.screen.input.buffer.text = placeholder
        runtime.screen.input.buffer.validate_and_handle()

    first = await runtime.read_message(PromptContext(mode="chat", model="test"))
    second = await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert (first, second) == contents


@pytest.mark.parametrize("command", ["/compact", "/fork"])
def test_streaming_rejected_command_never_enters_message_queue(command) -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.screen.input.buffer.text = command

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert f"'{command}' is disabled while a task is in progress." in (
        _fragments_text(runtime.document.fragments(width=100))
    )
    command_fragment = next(
        fragment
        for fragment in runtime.document.blocks[-1].block.fragments
        if fragment[1] == command
    )
    assert command_fragment[0] == "class:prompt.command.slash"


def test_streaming_background_command_is_not_echoed_to_transcript() -> None:
    runtime = TuiRuntime()
    handler = Mock(return_value=True)
    runtime.bind_stream_command_handler(handler)
    runtime.set_execution_active(True)
    runtime.screen.input.buffer.text = "/helix-link"

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    handler.assert_called_once_with("/helix-link")
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()

    runtime.set_execution_active(False)

    assert "/helix-link" not in _fragments_text(
        runtime.document.fragments(width=100)
    )


def test_streaming_ps_is_dispatched_without_queuing_command_block() -> None:
    runtime = TuiRuntime()
    handler = Mock(return_value=True)
    runtime.bind_stream_command_handler(handler)
    runtime.set_execution_active(True)
    runtime.screen.input.buffer.text = "/ps"

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    handler.assert_called_once_with("/ps")
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active

    runtime.set_execution_active(False)

    assert "/ps" not in _fragments_text(
        runtime.document.fragments(width=100)
    )


def test_background_status_waits_for_stream_boundary() -> None:
    runtime = TuiRuntime()
    sink = TuiApplicationSink(runtime)
    runtime.set_execution_active(True)

    sink._emit_active(ApplicationView(
        type="tui.helix.status",
        renderable=text_block("Helix ready"),
    ))

    assert not runtime.document.blocks

    runtime.set_execution_active(False)

    assert "Helix ready" in _fragments_text(
        runtime.document.fragments(width=100)
    )


def _submission(text: str) -> TuiSubmission:
    return TuiSubmission(
        value=text,
        editable_text=text,
        paste_store={},
    )


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)
