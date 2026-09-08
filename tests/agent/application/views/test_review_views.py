# -*- coding: utf-8 -*-

import pytest

from agent.application.views.builders.review import (
    build_review_cancelled_view,
    build_review_failed_view,
    build_review_reconciliation_view,
    build_review_started_view,
    review_output_text,
)
from agent.ports.presentation import StyledBlock
from protocol.schema.review import (
    ReviewCodeLocation,
    ReviewFinding,
    ReviewLineRange,
    ReviewOutput,
)


def _finding(
    title: str,
    body: str,
    path: str,
    start: int,
    end: int,
) -> ReviewFinding:
    return ReviewFinding(
        title=title,
        body=body,
        confidence_score=0.9,
        priority=1,
        code_location=ReviewCodeLocation(
            path=path,
            line_range=ReviewLineRange(start=start, end=end),
        ),
    )


def _output(*findings: ReviewFinding) -> ReviewOutput:
    return ReviewOutput(
        findings=findings,
        overall_correctness="incorrect" if findings else "correct",
        overall_explanation=(
            "Issues remain." if findings else "No findings."
        ),
        overall_confidence_score=0.9,
    )


def test_review_output_without_findings_has_no_empty_heading() -> None:
    assert review_output_text(_output()) == "No findings."


def test_review_output_formats_one_multiline_finding() -> None:
    output = _output(_finding(
        "Reject stale terminal",
        "First line.\nSecond line.",
        "agent/runtime.py",
        10,
        12,
    ))

    assert review_output_text(output) == (
        "Issues remain.\n\n"
        "Review comment:\n\n"
        "- Reject stale terminal — agent/runtime.py:10-12\n"
        "  First line.\n"
        "  Second line."
    )


def test_review_output_formats_multiple_findings() -> None:
    output = _output(
        _finding("First", "One.", "a.py", 1, 1),
        _finding("Second", "Two.", "b.py", 20, 24),
    )

    assert review_output_text(output) == (
        "Issues remain.\n\n"
        "Full review comments:\n\n"
        "- First — a.py:1-1\n"
        "  One.\n"
        "- Second — b.py:20-24\n"
        "  Two."
    )


@pytest.mark.parametrize(
    "view",
    (
        build_review_started_view("current changes"),
        build_review_failed_view("Review failed."),
        build_review_cancelled_view("interrupted"),
        build_review_reconciliation_view("connection lost"),
    ),
)
def test_review_styled_blocks_preserve_plain_text(view) -> None:
    assert isinstance(view.renderable, StyledBlock)
    assert view.renderable.plain_text == "".join(
        span.text for span in view.renderable.spans
    )
