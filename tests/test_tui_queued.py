# -*- coding: utf-8 -*-

import pytest
from types import SimpleNamespace
from prompt_toolkit.application.current import set_app
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.utils import get_cwidth
from unittest.mock import Mock

from agent.ports.presentation import ApplicationView
from mind_app.interaction.contracts import PromptContext
from mind_app.presentation.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
)
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.core.queued import (
    TuiPendingSteers,
    TuiQueuedMessages,
    TuiSubmission,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import text_block


def test_queue_uses_next_turn_title() -> None:
    queue = TuiQueuedMessages()
    queue.append(_submission("next task"))

    text = _fragments_text(queue.fragments(width=100))

    assert "• Queued follow-up inputs" in text
    assert "  ↳ next task" in text
    assert "    alt + ↑ edit last queued message" in text
    assert text.splitlines() == [
        "• Queued follow-up inputs",
        "  ↳ next task",
        "    alt + ↑ edit last queued message",
    ]


def test_pending_steer_uses_current_turn_title() -> None:
    pending = TuiPendingSteers()
    pending.add(_submission("adjust current task"))

    text = _fragments_text(pending.fragments(width=100))

    assert (
        "• Messages to be submitted after next tool call "
        "(press ctrl + c to interrupt and send immediately)"
    ) in text
    assert "  ↳ adjust current task" in text
    assert "Queued follow-up inputs" not in text

    title_fragments = pending.fragments(width=100)[:2]
    assert title_fragments == [
        (
            "class:queue.label",
            "• Messages to be submitted after next tool call",
        ),
        (
            "class:queue.hint",
            " (press ctrl + c to interrupt and send immediately)",
        ),
    ]


def test_pending_steer_lists_each_enter_submission() -> None:
    pending = TuiPendingSteers()
    pending.add(_submission("first"))
    pending.add(_submission("second"))
    pending.add(_submission("third"))

    text = _fragments_text(pending.fragments(width=100))

    assert text.splitlines() == [
        (
            "• Messages to be submitted after next tool call "
            "(press ctrl + c to interrupt and send immediately)"
        ),
        "  ↳ first",
        "  ↳ second",
        "  ↳ third",
    ]


@pytest.mark.parametrize(
    "keys",
    (
        (Keys.Escape, Keys.Up),
        (Keys.ShiftLeft,),
    ),
)
def test_codex_queue_edit_shortcuts_restore_latest_message(keys) -> None:
    runtime = TuiRuntime()
    runtime.defer_submission(_submission("first task"))
    runtime.defer_submission(_submission("latest task"))

    binding = next(
        item
        for item in runtime.input_model.key_bindings.bindings
        if item.keys == keys
    )
    binding.handler(SimpleNamespace(
        app=SimpleNamespace(current_buffer=runtime.screen.input.buffer),
    ))

    assert runtime.screen.input.buffer.text == "latest task"
    assert runtime.submissions.queued_messages.pop_next().value == "first task"


@pytest.mark.anyio
async def test_queue_edit_shortcut_does_not_reopen_pending_steer() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)

    def track_pending(submission, queue_only) -> bool:
        assert not queue_only
        runtime.track_pending_steer(submission)
        return True

    runtime.bind_turn_input_handler(track_pending)
    buffer = runtime.screen.input.buffer
    buffer.text = "submitted now"
    buffer.cursor_position = len(buffer.text)
    buffer.validate_and_handle()

    assert buffer.text == ""
    assert runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active

    with set_app(runtime.screen.application):
        processor = runtime.screen.application.key_processor
        processor.feed(KeyPress(Keys.Escape, "\x1b"))
        processor.feed(KeyPress(Keys.Up, "\x1b[A"))
        processor.process_keys()

    assert buffer.text == ""
    assert runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    await runtime.close()


def test_tab_queue_is_restored_before_rejected_steer() -> None:
    runtime = TuiRuntime()
    rejected = _submission("rejected enter")
    queued = _submission("tab follow up")
    runtime.defer_rejected_steer(rejected)
    runtime.defer_submission(queued)

    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == queued.editable_text
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == rejected.editable_text


@pytest.mark.anyio
async def test_rejected_steers_preserve_identity_before_tab_fifo() -> None:
    runtime = TuiRuntime()
    runtime.defer_rejected_steer(TuiSubmission(
        value="first rejected",
        editable_text="first rejected",
        paste_store={},
        client_message_id="message_rejected_1",
        attachments=({"kind": "image", "name": "first.png"},),
        extras={"first": 1, "shared": "old"},
        payload_bound=True,
    ))
    runtime.defer_submission(_submission("first tab"))
    runtime.defer_rejected_steer(TuiSubmission(
        value="second rejected",
        editable_text="second rejected",
        paste_store={},
        client_message_id="message_rejected_2",
        attachments=({"kind": "image", "name": "second.png"},),
        extras={"second": 2, "shared": "new"},
        payload_bound=True,
    ))
    runtime.defer_submission(_submission("second tab"))

    first_retry = await runtime.submissions.read_submission()
    second_retry = await runtime.submissions.read_submission()
    first_tab = await runtime.submissions.read_submission()
    second_tab = await runtime.submissions.read_submission()

    assert first_retry.client_message_id == "message_rejected_1"
    assert first_retry.attachments == ({"kind": "image", "name": "first.png"},)
    assert first_retry.extras == {"first": 1, "shared": "old"}
    assert first_retry.payload_bound
    assert second_retry.client_message_id == "message_rejected_2"
    assert second_retry.attachments == ({"kind": "image", "name": "second.png"},)
    assert second_retry.extras == {"second": 2, "shared": "new"}
    assert second_retry.payload_bound
    assert first_tab.value == "first tab"
    assert second_tab.value == "second tab"


@pytest.mark.parametrize(
    "identity",
    (
        TerminalIdentity(TerminalKind.VSCODE, "VS Code"),
        TerminalIdentity(TerminalKind.WARP, "Warp"),
        TerminalIdentity(TerminalKind.APPLE_TERMINAL, "Apple Terminal"),
        TerminalIdentity(
            TerminalKind.WINDOWS_TERMINAL,
            "Windows Terminal",
            multiplexer=TerminalKind.TMUX,
        ),
    ),
)
def test_queue_edit_hint_uses_terminal_fallback(identity) -> None:
    runtime = TuiRuntime(terminal_capabilities=TerminalCapabilities(
        identity=identity,
        color_level=TerminalColorLevel.UNKNOWN,
    ))
    runtime.defer_submission(_submission("next task"))

    text = _fragments_text(runtime.screen._queued_fragments())

    assert "shift + ← edit last queued message" in text


@pytest.mark.parametrize(
    "identity",
    (
        TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        TerminalIdentity(TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
    ),
)
def test_queue_edit_hint_keeps_codex_default_binding(identity) -> None:
    runtime = TuiRuntime(terminal_capabilities=TerminalCapabilities(
        identity=identity,
        color_level=TerminalColorLevel.UNKNOWN,
    ))
    runtime.defer_submission(_submission("next task"))

    text = _fragments_text(runtime.screen._queued_fragments())

    assert "alt + ↑ edit last queued message" in text


def test_queue_styles_distinguish_labels_and_hints() -> None:
    style = TuiRuntime().screen.application.style

    assert style.get_attrs_for_style_str("class:queue.text").dim
    assert style.get_attrs_for_style_str("class:queue.text.queued").italic
    assert not style.get_attrs_for_style_str("class:queue.label").bold
    assert style.get_attrs_for_style_str("class:queue.marker").dim
    assert not style.get_attrs_for_style_str("class:queue.marker").bold
    assert style.get_attrs_for_style_str("class:queue.hint").dim
    assert not style.get_attrs_for_style_str("class:queue.hint").bold
    assert style.get_attrs_for_style_str("class:queue.edit-hint").dim
    assert not style.get_attrs_for_style_str("class:queue.edit-hint").bold
    assert style.get_attrs_for_style_str("class:footer.queue-hint").dim
    assert not style.get_attrs_for_style_str("class:footer.queue-hint").bold


def test_follow_up_input_uses_codex_italic_style() -> None:
    pending = TuiPendingSteers()
    pending.add(_submission("enter message"))
    queued = TuiQueuedMessages()
    queued.append(_submission("tab message"))

    assert ("class:queue.text", "enter message") in pending.fragments(width=100)
    assert (
        "class:queue.text.queued",
        "tab message",
    ) in queued.fragments(width=100)


def test_multiline_follow_up_matches_codex_preview_shape() -> None:
    queue = TuiQueuedMessages()
    queue.append(_submission(
        "cli链路的hooks呢？ codex是什么形态，是否有对齐？\n"
        "OpenAI Codex v0.147.0\n"
        "--------\n"
        "Working directory: ProxyMind\n"
        "more output"
    ))

    text = _fragments_text(queue.fragments(
        width=80,
        edit_binding="shift + ←",
    ))

    assert text.splitlines() == [
        "• Queued follow-up inputs",
        "  ↳ cli链路的hooks呢？ codex是什么形态，是否有对齐？",
        "    OpenAI Codex v0.147.0",
        "    --------",
        "    …",
        "    shift + ← edit last queued message",
    ]


def test_queue_reserves_last_row_for_hidden_count() -> None:
    queue = TuiQueuedMessages()
    for index in range(7):
        queue.append(_submission(f"message {index + 1}"))

    text = _fragments_text(queue.fragments(width=100, max_rows=6))
    lines = text.splitlines()

    assert len(lines) == 6
    assert lines[-2] == "    … 4 more"
    assert lines[-1] == "    alt + ↑ edit last queued message"
    assert "message 4" not in text


def test_running_input_replaces_information_footer_with_queue_hint() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
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


def test_running_input_shortens_queue_hint_on_narrow_terminal(monkeypatch) -> None:
    runtime = TuiRuntime()
    runtime.execution_active = True
    monkeypatch.setattr(runtime.screen, "_output_size", lambda: (20, 24))
    runtime.screen.input.buffer.text = "next task"

    text = _fragments_text(runtime.screen._footer_fragments())

    assert text == "  tab to queue"


def test_pending_steer_and_follow_up_have_separate_sections() -> None:
    runtime = TuiRuntime()
    runtime.track_pending_steer(_submission("adjust current task"))
    runtime.defer_submission(_submission("next task"))

    text = _fragments_text(runtime.screen._queued_fragments())

    assert "Messages to be submitted after next tool call" in text
    assert "  ↳ adjust current task" in text
    assert "Queued follow-up inputs" in text
    assert "  ↳ next task" in text
    assert len(text.splitlines()) <= runtime.screen.QUEUED_MAX_HEIGHT


def test_queued_submission_restores_information_footer() -> None:
    runtime = TuiRuntime()
    runtime.context = PromptContext(
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


def test_running_enter_steers_and_tab_explicitly_queues() -> None:
    runtime = TuiRuntime()
    intents = []
    runtime.set_execution_active(True)
    runtime.bind_turn_input_handler(
        lambda submission, queue_only: intents.append((
            submission.value,
            queue_only,
        )) or True
    )

    runtime.screen.input.buffer.text = "steer now"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    runtime.screen.input.buffer.text = "wait for next turn"
    runtime.submissions.queue_input(runtime.screen.input.buffer)

    assert intents == [
        ("steer now", False),
        ("wait for next turn", True),
    ]
    assert not runtime.submissions.queued_messages.active


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

    value = await runtime.read_message(PromptContext(model="test"))

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

    first = await runtime.read_message(PromptContext(model="test"))
    second = await runtime.read_message(PromptContext(model="test"))

    assert (first, second) == contents


def test_queued_preview_uses_expanded_submission_text() -> None:
    queue = TuiQueuedMessages()
    original = "expanded queued content " * 80
    placeholder = "[Pasted Content 1840 chars]"
    queue.append(TuiSubmission(
        value=original,
        editable_text=placeholder,
        paste_store={placeholder: original},
    ))

    text = _fragments_text(queue.fragments(width=100))

    assert placeholder not in text
    assert "expanded queued content" in text


@pytest.mark.anyio
async def test_queued_literal_bang_paste_is_not_rejected_as_shell() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    original = "! literal queued paste " * 80
    placeholder = runtime.input_model._display_paste(original, "")
    runtime.screen.input.buffer.text = placeholder

    runtime.screen.input.buffer.validate_and_handle()

    assert runtime.submissions.queued_messages.active
    value = await runtime.read_message(PromptContext(model="test"))

    assert value == original.strip()
    assert runtime.document.blocks[-1].kind == "user"
    assert "disabled while a task is in progress" not in _fragments_text(
        runtime.document.fragments(width=100)
    )
    assert _fragments_text(
        runtime.document.blocks[-1].display_block.fragments
    ).startswith("› ! literal queued paste")


def test_rollback_duplicate_queue_does_not_remove_previous_history() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    buffer = runtime.screen.input.buffer

    for _ in range(2):
        buffer.text = "same queued input"
        buffer.validate_and_handle()

    assert runtime.input_model.history.get_strings() == ["same queued input"]

    assert runtime.submissions.rollback_queued_input()
    assert runtime.input_model.history.get_strings() == ["same queued input"]

    assert runtime.submissions.rollback_queued_input()
    assert runtime.input_model.history.get_strings() == []


@pytest.mark.parametrize(
    "command",
    ["/compact", "/fork", "/resume", "/mcp", "/helix-unlink"],
)
def test_streaming_rejected_command_never_enters_message_queue(command) -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.screen.input.buffer.text = command

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert runtime.input_model.history.get_strings() == []
    assert f"'{command}' is disabled while a task is in progress." in (
        _fragments_text(runtime.document.fragments(width=100))
    )
    command_fragment = next(
        fragment
        for fragment in runtime.document.blocks[-1].display_block.fragments
        if fragment[1] == command
    )
    assert command_fragment[0] == "class:prompt.command.slash"


def test_streaming_background_command_is_not_echoed_to_transcript() -> None:
    runtime = TuiRuntime()
    handler = Mock(return_value=True)
    runtime.bind_stream_command_handler(handler)
    runtime.set_execution_active(True)
    runtime.screen.input.buffer.text = "/mcp start"

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    handler.assert_called_once_with("/mcp start")
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()

    runtime.set_execution_active(False)

    assert "/mcp start" not in _fragments_text(
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
