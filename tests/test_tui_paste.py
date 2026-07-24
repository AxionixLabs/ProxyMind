# -*- coding: utf-8 -*-

from mind_app.tui.core.input import TuiInputModel
from mind_app.tui.prompting.paste import (
    format_paste_placeholder,
    parse_paste_placeholder,
)
from mind_app.tui.prompting.skills import iter_paste_placeholder_tokens


def test_paste_content_uses_uniform_character_count_label() -> None:
    text = "plain text"
    placeholder = format_paste_placeholder(text, 1)

    assert placeholder == "[Pasted Content 10 chars]"
    assert parse_paste_placeholder(placeholder) is not None
    assert not list(iter_paste_placeholder_tokens(
        f"before {placeholder} after"
    ))
    token = next(iter_paste_placeholder_tokens(
        f"before {placeholder} after",
        paste_placeholders=(placeholder,),
    ))
    assert f"before {placeholder} after"[token[0]:token[1]] == placeholder


def test_paste_placeholder_parser_rejects_noncanonical_display_text() -> None:
    invalid = (
        "[Pasted Content 01 chars]",
        "[Pasted Content 1,200 chars]",
        "[Pasted Content 12 lines]",
        "[Pasted Content 12 chars] #1",
        "[Text #1 - 12 chars]",
    )

    assert all(parse_paste_placeholder(value) is None for value in invalid)


def test_folded_paste_round_trip_reuses_deleted_sequence() -> None:
    model = TuiInputModel()
    first_text = "a" * 1200
    code_text = "def run(value):\n" + "\n".join(
        f"    value += {index}" for index in range(19)
    )
    log_text = "\n".join(
        f"2026-07-23 10:00:{index:02d} ERROR failure {index}"
        for index in range(20)
    )

    first = model._display_paste(first_text, "")
    second = model._display_paste(code_text, first)
    third = model._display_paste(log_text, f"{first}\n{second}")

    assert first == "[Pasted Content 1200 chars]"
    assert second == f"[Pasted Content {len(code_text)} chars] #2"
    assert third == f"[Pasted Content {len(log_text)} chars] #3"
    assert model.restore_submission(
        f"{first}\n{second}\n{third}"
    ) == f"{first_text}\n{code_text}\n{log_text}"
    assert model.restore_submission(f"{second}\n{second}") == (
        f"{code_text}\n{second}"
    )

    restored = TuiInputModel()
    restored.restore_submission_state(model.submission_state())
    replacement_text = "plain words " * 110
    replacement = restored._display_paste(
        replacement_text,
        f"{second}\n{third}",
    )

    assert replacement == f"[Pasted Content {len(replacement_text)} chars]"


def test_same_size_paste_reuses_deleted_first_and_middle_numbers() -> None:
    model = TuiInputModel()
    first_text = "a" * 1200
    second_text = "b" * 1200
    third_text = "c" * 1200
    replacement_text = "d" * 1200

    first = model._display_paste(first_text, "")
    second = model._display_paste(second_text, first)
    third = model._display_paste(third_text, second)

    assert third == "[Pasted Content 1200 chars]"
    assert set(model.submission_state()) == {second, third}
    assert model.restore_submission(f"{second}\n{third}") == (
        f"{second_text}\n{third_text}"
    )

    model = TuiInputModel()
    first = model._display_paste(first_text, "")
    second = model._display_paste(second_text, first)
    third = model._display_paste(third_text, f"{first}\n{second}")
    replacement = model._display_paste(
        replacement_text,
        f"{first}\n{third}",
    )

    assert replacement == "[Pasted Content 1200 chars] #2"
    assert set(model.submission_state()) == {first, third, replacement}


def test_same_size_numbered_placeholder_highlights_complete_token() -> None:
    first = "[Pasted Content 1200 chars]"
    second = "[Pasted Content 1200 chars] #2"
    text = f"before {second} after"

    tokens = list(iter_paste_placeholder_tokens(
        text,
        paste_placeholders=(first, second),
    ))

    assert len(tokens) == 1
    assert text[tokens[0][0]:tokens[0][1]] == second
