# -*- coding: utf-8 -*-

from mind_app.presentation.models import StyledBlock, TextSpan, TextStyle
from mind_app.presentation.terminal_text import (
    TerminalTextFilter,
    sanitize_terminal_line,
    sanitize_styled_block,
    sanitize_terminal_hyperlink,
    sanitize_terminal_text,
)


def test_terminal_text_removes_escape_and_control_sequences() -> None:
    value = (
        "start"
        "\x1b[31mred\x1b[0m"
        "\x1b]52;c;SGVsbG8=\x1b\\"
        "\x1bPprivate\x1b\\"
        "\x9b2J"
        "\x07\x08\x00"
        "end"
    )

    assert sanitize_terminal_text(value) == "startredend"


def test_terminal_text_expands_tabs_by_display_column() -> None:
    assert sanitize_terminal_text("47031FDAQ001MK\tdevice") == (
        "47031FDAQ001MK  device"
    )
    assert sanitize_terminal_text("设备\t就绪") == "设备    就绪"


def test_terminal_text_normalizes_line_endings() -> None:
    assert sanitize_terminal_text("one\r\ntwo\rthree") == "one\ntwo\nthree"


def test_stream_filter_handles_sequences_across_deltas() -> None:
    text_filter = TerminalTextFilter()

    assert text_filter.feed("safe\x1b]52;") == "safe"
    assert text_filter.feed("c;SGVsbG8=\x1b") == ""
    assert text_filter.feed("\\after") == "after"
    assert text_filter.finish() == ""


def test_incomplete_sequence_is_discarded_at_boundary() -> None:
    text_filter = TerminalTextFilter()

    assert text_filter.feed("safe\x1b[31") == "safe"
    assert text_filter.finish() == ""
    assert text_filter.feed("next") == "next"


def test_styled_block_preserves_styles_and_properties() -> None:
    accent = TextStyle(foreground="#ffffff", bold=True)
    block = StyledBlock(
        plain_text="a\tb\x1b[2J",
        spans=(
            TextSpan("a\x1b[", accent),
            TextSpan("2J\tb", accent),
        ),
        preserve_spans=True,
        direct=True,
    )

    sanitized = sanitize_styled_block(block)

    assert sanitized.plain_text == "a       b"
    assert sanitized.spans == (TextSpan("a       b", accent),)
    assert sanitized.preserve_spans
    assert sanitized.direct


def test_terminal_text_sanitizing_is_idempotent() -> None:
    value = "a\tb\x1b]0;title\x07"
    once = sanitize_terminal_text(value)

    assert sanitize_terminal_text(once) == once


def test_terminal_text_preserves_non_string_scalar_values() -> None:
    assert sanitize_terminal_text(0) == "0"
    assert sanitize_terminal_text(False) == "False"


def test_terminal_hyperlink_accepts_absolute_urls_and_rejects_controls() -> None:
    assert sanitize_terminal_hyperlink("https://example.com/docs") == (
        "https://example.com/docs"
    )
    assert sanitize_terminal_hyperlink("file:///tmp/example.py") == (
        "file:///tmp/example.py"
    )
    assert sanitize_terminal_hyperlink("relative/path") is None
    assert sanitize_terminal_hyperlink("https://example.com/\x1b\\") is None
    assert sanitize_terminal_hyperlink("https://example.com/\x9ctail") is None


def test_terminal_line_filters_controls_and_flattens_whitespace() -> None:
    value = "tool\tname\nnext\x1b]52;c;payload\x1b\\"

    assert sanitize_terminal_line(value) == "tool name next"
