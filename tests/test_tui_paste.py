# -*- coding: utf-8 -*-

from mind_app.tui.core.input import TuiInputModel
from mind_app.tui.prompting.paste import (
    describe_paste,
    format_paste_placeholder,
)
from mind_app.tui.prompting.skills import iter_paste_placeholder_tokens


def test_common_paste_content_uses_semantic_exact_labels() -> None:
    cases = (
        ('{"ok":true}', "[Data #1 \u00b7 JSON \u00b7 11 chars]"),
        (
            "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new",
            "[Diff #1 \u00b7 6 lines]",
        ),
        (
            "2026-07-23 10:00:00 INFO start\n"
            "2026-07-23 10:00:01 WARN retry\n"
            "2026-07-23 10:00:02 ERROR failed",
            "[Log #1 \u00b7 3 lines]",
        ),
        (
            "# Title\n## Part\n- one\n- two\n- three\n[one](a)\n[two](b)",
            "[Text #1 \u00b7 Markdown \u00b7 7 lines]",
        ),
        (
            "def run(value):\n    value += 1\n    value += 2\n    return value",
            "[Code #1 \u00b7 Python \u00b7 4 lines]",
        ),
        ("plain text", "[Text #1 \u00b7 10 chars]"),
    )

    for text, expected in cases:
        placeholder = format_paste_placeholder(describe_paste(text), 1)
        assert placeholder == expected
        token = next(iter_paste_placeholder_tokens(f"before {placeholder} after"))
        assert f"before {placeholder} after"[token[0]:token[1]] == placeholder


def test_folded_paste_round_trip_preserves_sequence_after_restore() -> None:
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
    third = model._display_paste(log_text, second)

    assert first == "[Text #1 \u00b7 1,200 chars]"
    assert second == "[Code #2 \u00b7 Python \u00b7 20 lines]"
    assert third == "[Log #3 \u00b7 20 lines]"
    assert first not in model.submission_state()
    assert model.restore_submission(f"{second}\n{third}") == f"{code_text}\n{log_text}"

    restored = TuiInputModel()
    restored.restore_submission_state(model.submission_state())
    fourth_text = "plain words " * 110
    fourth = restored._display_paste(fourth_text, f"{second}\n{third}")

    assert fourth == f"[Text #4 \u00b7 {len(fourth_text):,} chars]"
