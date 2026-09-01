# -*- coding: utf-8 -*-

from agent.ports.presentation import TextSpan
from agent.application.views import (
    FailureView,
    RunIncompleteView,
)
from frontends.terminal.renderers.dispatch import (
    render_presentation_transcript_view,
    render_presentation_view,
)
from frontends.terminal.text_layout import text_display_width
from frontends.terminal.renderers.failure import (
    render_failure_block,
    render_failure_display_parts,
    render_failure_text
)


def _display_text(parts: list[TextSpan]) -> str:
    return "".join(part.text for part in parts)


def test_failure_display_without_width_keeps_existing_text() -> None:
    error = "PermissionDeniedError: quota insufficient"

    block = render_failure_block("turn.failed", error)

    assert block.plain_text == render_failure_text("turn.failed", error)
    assert _display_text(list(block.spans)) == block.plain_text
    assert all(not part.style.bold for part in block.spans)


def test_failure_display_wraps_continuation_after_branch() -> None:
    error = "PermissionDeniedError: Error code: 403 - user quota insufficient"

    block = render_failure_block("turn.failed", error, terminal_width=32)
    display_lines = _display_text(list(block.spans)).splitlines()

    assert block.plain_text == f"■ turn.failed\n  └ {error}"
    assert len(display_lines) > 2
    assert display_lines[1].startswith("  └ ")
    assert all(line.startswith("    ") for line in display_lines[2:])
    assert all(text_display_width(line) <= 32 for line in display_lines)


def test_failure_display_wraps_wide_characters_by_display_width() -> None:
    error = "用户额度不足，剩余额度为负数，请充值后继续使用"

    parts = render_failure_display_parts(
        "turn.failed",
        error,
        terminal_width=16,
    )
    display_lines = _display_text(parts).splitlines()

    assert len(display_lines) > 2
    assert all(line.startswith("  ") for line in display_lines[2:])
    assert all(text_display_width(line) <= 16 for line in display_lines)


def test_failure_view_dispatch_uses_terminal_width() -> None:
    error = "PermissionDeniedError: Error code: 403 - user quota insufficient"

    blocks = render_presentation_view(
        FailureView(phase="turn.failed", error=error),
        terminal_width=24,
    )
    display_lines = _display_text(list(blocks[0].spans)).splitlines()

    assert len(display_lines) > 2
    assert all(line.startswith("  ") for line in display_lines[2:])
    assert all(text_display_width(line) <= 24 for line in display_lines)


def test_failure_transcript_keeps_width_independent_logical_lines() -> None:
    error = "PermissionDeniedError: Error code: 403 - user quota insufficient"

    blocks = render_presentation_transcript_view(
        FailureView(phase="turn.failed", error=error),
        terminal_width=24,
    )
    transcript = _display_text(list(blocks[0].spans))

    assert transcript == f"■ turn.failed\n  └ {error}"
    assert len(transcript.splitlines()) == 2


def test_incomplete_view_dispatch_displays_reason() -> None:
    blocks = render_presentation_view(RunIncompleteView(
        usage={"output_tokens": 7},
        reason="max_output_tokens",
        can_continue=False,
    ))

    assert blocks[0].plain_text == "■ turn.incomplete\n  └ max_output_tokens"
