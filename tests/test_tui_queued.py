# -*- coding: utf-8 -*-

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
    style = TuiRuntime()._style()

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
        access_label="Full access",
        workspace_label="ProxyMind",
    )
    runtime.execution_active = True
    runtime.queued_messages.append(_submission("already queued"))
    runtime.input.buffer.text = "next task"

    text = _fragments_text(runtime._footer_fragments())

    assert runtime._footer_visible()
    assert text == "  tab to queue message"
    assert runtime.context.model not in text
    assert runtime.context.access_label not in text
    assert runtime.context.workspace_label not in text


def test_queued_submission_restores_information_footer() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
        mode="chat",
        model="gpt-test high",
        access_label="Full access",
        workspace_label="ProxyMind",
    )
    runtime.execution_active = True
    runtime.input.buffer.text = "queued task"

    runtime._accept_input(runtime.input.buffer)

    text = _fragments_text(runtime._footer_fragments())

    assert runtime._footer_visible()
    assert "tab to queue message" not in text
    assert "gpt-test high" in text
    assert "Full access" in text
    assert "ProxyMind" in text

    runtime.input.buffer.text = "another task"

    assert _fragments_text(runtime._footer_fragments()) == "  tab to queue message"


def test_foreground_barrier_keeps_normal_input_in_visible_queue() -> None:
    runtime = TuiRuntime()
    runtime.set_foreground_active(True)
    runtime.input.buffer.text = "next task"

    runtime._accept_input(runtime.input.buffer)

    assert runtime.queued_messages.active
    assert runtime.message_queue.empty()


def test_streaming_rejected_command_never_enters_message_queue() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.input.buffer.text = "/compact"

    runtime._accept_input(runtime.input.buffer)

    assert not runtime.queued_messages.active
    assert runtime.message_queue.empty()
    assert "'/compact' is disabled while a task is in progress." in (
        _fragments_text(runtime.document.fragments(width=100))
    )
    command_fragment = next(
        fragment
        for fragment in runtime.document.blocks[-1].block.fragments
        if fragment[1] == "/compact"
    )
    assert command_fragment[0] == "class:prompt.command.slash"


def test_streaming_background_command_is_dispatched_outside_message_queue() -> None:
    runtime = TuiRuntime()
    handler = Mock(return_value=True)
    runtime.bind_stream_command_handler(handler)
    runtime.set_execution_active(True)
    runtime.input.buffer.text = "/helix-link"

    runtime._accept_input(runtime.input.buffer)

    handler.assert_called_once_with("/helix-link")
    assert not runtime.queued_messages.active
    assert runtime.message_queue.empty()

    runtime.set_execution_active(False)

    assert "/helix-link" in _fragments_text(
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
