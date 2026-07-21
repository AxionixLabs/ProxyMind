# -*- coding: utf-8 -*-

from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.queued import TuiQueuedMessages, TuiSubmission


def test_queue_uses_next_turn_title() -> None:
    queue = TuiQueuedMessages()
    queue.append(_submission("next task"))

    text = _fragments_text(queue.fragments(width=100))

    assert "• Messages queued for the next turn (Esc edits latest)" in text
    assert "  ↳ next task" in text


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


def _submission(text: str) -> TuiSubmission:
    return TuiSubmission(
        value=text,
        editable_text=text,
        paste_store={},
    )


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)
