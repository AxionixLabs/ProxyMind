# -*- coding: utf-8 -*-

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mind_app.tui.core.document import TranscriptBlock
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.features.transcript_export import TranscriptExporter


def _cell(
    text: str,
    *,
    kind: str,
    raw_text: str | None = None,
    stream_continuation: bool = False,
) -> TranscriptBlock:
    block = FragmentBlock((("", text),))
    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind=kind,
        raw_text=raw_text,
        stream_continuation=stream_continuation,
    )


def test_transcript_exporter_writes_semantic_markdown_and_raw(tmp_path: Path) -> None:
    exporter = TranscriptExporter(tmp_path)
    cells = (
        _cell("rendered question", kind="user", raw_text="**question**"),
        _cell(
            "rendered answer",
            kind="assistant",
            raw_text="answer [docs](https://example.com)",
        ),
        _cell("more", kind="assistant", raw_text="continued", stream_continuation=True),
        _cell(
            "decorated tool",
            kind="operation",
            raw_text="command\n```nested```",
        ),
    )

    markdown_result = exporter.export(cells, "markdown")
    raw_result = exporter.export(cells, "raw")

    markdown = markdown_result.path.read_text(encoding="utf-8")
    raw = raw_result.path.read_text(encoding="utf-8")

    assert markdown_result.format == "markdown"
    assert markdown_result.cell_count == 4
    assert markdown.startswith("# Transcript\n\n## User\n\n**question**")
    assert "## Assistant\n\nanswer [docs](https://example.com)\ncontinued" in markdown
    assert "## Tool\n\n````text\ncommand\n```nested```\n````" in markdown
    assert raw == (
        "**question**\n\n"
        "answer [docs](https://example.com)\ncontinued\n\n"
        "command\n```nested```\n"
    )
    assert markdown_result.path.suffix == ".md"
    assert raw_result.path.suffix == ".txt"
    assert markdown_result.path != raw_result.path
    assert not tuple(tmp_path.glob("*.tmp"))


def test_transcript_exporter_strips_terminal_hyperlink_metadata(tmp_path: Path) -> None:
    fragments = FragmentBlock((
        ("[ZeroWidthEscape]", "\x1b]8;;https://example.com\x1b\\"),
        ("class:link", "docs"),
        ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
    ))
    cell = TranscriptBlock(
        display_block=fragments,
        transcript_block=fragments,
        kind="assistant",
    )

    result = TranscriptExporter(tmp_path).export((cell,), "raw")

    assert result.path.read_text(encoding="utf-8") == "docs\n"


def test_transcript_exporter_preserves_continuation_blank_lines(
    tmp_path: Path,
) -> None:
    cells = (
        _cell("first", kind="assistant", raw_text="first\n\n"),
        _cell(
            "second",
            kind="assistant",
            raw_text="second",
            stream_continuation=True,
        ),
    )

    result = TranscriptExporter(tmp_path).export(cells, "raw")

    assert result.path.read_text(encoding="utf-8") == "first\n\nsecond\n"


def test_transcript_exporter_cleans_temporary_file_after_failure(
    tmp_path: Path,
) -> None:
    exporter = TranscriptExporter(tmp_path)

    with patch.object(os, "replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            exporter.export((_cell("content", kind="assistant"),), "markdown")

    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("output_format", ["markdown", "raw"])
def test_transcript_exporter_rejects_empty_transcript(
    tmp_path: Path,
    output_format: str,
) -> None:
    with pytest.raises(ValueError, match="transcript is empty"):
        TranscriptExporter(tmp_path).export((), output_format)


def test_transcript_exporter_uses_unique_name_for_same_timestamp(
    tmp_path: Path,
) -> None:
    exporter = TranscriptExporter(tmp_path)
    cells = (_cell("content", kind="assistant"),)

    with patch(
        "mind_app.tui.features.transcript_export.datetime",
    ) as current_datetime:
        current_datetime.now.return_value.astimezone.return_value.strftime\
            .return_value = "20260803-120000-000000"
        first = exporter.export(cells, "raw")
        second = exporter.export(cells, "raw")

    assert first.path.name == "transcript-20260803-120000-000000.txt"
    assert second.path.name == "transcript-20260803-120000-000000-2.txt"
    assert first.path.read_text(encoding="utf-8") == "content\n"
    assert second.path.read_text(encoding="utf-8") == "content\n"


def test_transcript_exporter_reports_unwritable_directory(
    tmp_path: Path,
) -> None:
    exporter = TranscriptExporter(tmp_path / "blocked")

    with patch.object(
        Path,
        "mkdir",
        side_effect=PermissionError("read only"),
    ), pytest.raises(PermissionError, match="read only"):
        exporter.export((_cell("content", kind="assistant"),), "raw")


def test_transcript_exporter_writes_large_raw_content(tmp_path: Path) -> None:
    exporter = TranscriptExporter(tmp_path)
    content = "0123456789abcdef" * 65_536

    result = exporter.export(
        (_cell(content, kind="operation"),),
        "raw",
    )

    assert result.path.stat().st_size == len(content) + 1
    assert result.path.read_text(encoding="utf-8") == f"{content}\n"
