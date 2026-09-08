# -*- coding: utf-8 -*-

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest

from frontends.tui.core.document import TranscriptBlock
from frontends.tui.core.menu import TuiMenu
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.transcript_export import (
    TranscriptExporter,
    _destination_menu,
    _filename_prompt,
    export_conversation,
)
from frontends.tui.core.models import MenuTextInputMode
from frontends.tui.rendering.fragments import fragments_text


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


def test_transcript_exporter_writes_codex_semantic_markdown(tmp_path: Path) -> None:
    exporter = TranscriptExporter(tmp_path)
    cells = (
        _cell("rendered question", kind="user", raw_text="**question**"),
        _cell(
            "rendered answer",
            kind="assistant",
            raw_text="answer [docs](https://example.com)",
        ),
        _cell(
            "more",
            kind="assistant",
            raw_text="continued",
            stream_continuation=True,
        ),
        _cell(
            "decorated tool",
            kind="operation",
            raw_text="command\n```nested```",
        ),
    )

    result = exporter.export(cells, "conversation.md")
    markdown = result.path.read_text(encoding="utf-8")

    assert result.path == tmp_path / "conversation.md"
    assert result.cell_count == 4
    assert markdown.startswith("# Mind conversation\n\n## User\n\n**question**")
    assert (
        "## Assistant\n\n"
        "answer [docs](https://example.com)\ncontinued"
    ) in markdown
    assert "## Activity\n\n    command\n    ```nested```\n" in markdown
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

    markdown = TranscriptExporter(tmp_path).render((cell,))

    assert markdown == "# Mind conversation\n\n## Assistant\n\ndocs\n"


def test_transcript_exporter_preserves_continuation_blank_lines() -> None:
    cells = (
        _cell("first", kind="assistant", raw_text="first\n\n"),
        _cell(
            "second",
            kind="assistant",
            raw_text="second",
            stream_continuation=True,
        ),
    )

    markdown = TranscriptExporter().render(cells)

    assert markdown.endswith("## Assistant\n\nfirst\n\nsecond\n")


def test_transcript_exporter_excludes_session_and_prior_export_feedback() -> None:
    cells = (
        _cell(">_ Mind (v1.2.8)", kind="system"),
        _cell("question", kind="user"),
        _cell("• Saved conversation to old.md", kind="system"),
        _cell("■ Export failed: disk full", kind="system"),
        _cell("answer", kind="assistant"),
    )

    markdown = TranscriptExporter().render(cells)

    assert ">_ Mind" not in markdown
    assert "Saved conversation" not in markdown
    assert "Export failed" not in markdown
    assert "## User\n\nquestion" in markdown
    assert "## Assistant\n\nanswer" in markdown


def test_transcript_exporter_cleans_temporary_file_after_failure(
    tmp_path: Path,
) -> None:
    exporter = TranscriptExporter(tmp_path)

    with patch.object(os, "link", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            exporter.export(
                (_cell("content", kind="assistant"),),
                "conversation.md",
            )

    assert not tuple(tmp_path.iterdir())


def test_transcript_exporter_rejects_empty_transcript(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No conversation content"):
        TranscriptExporter(tmp_path).export((), "conversation.md")


def test_transcript_exporter_never_overwrites_existing_file(tmp_path: Path) -> None:
    exporter = TranscriptExporter(tmp_path)
    target = tmp_path / "conversation.md"
    target.write_text("original\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="file already exists"):
        exporter.export(
            (_cell("replacement", kind="assistant"),),
            target.name,
        )

    assert target.read_text(encoding="utf-8") == "original\n"
    assert not tuple(tmp_path.glob("*.tmp"))


def test_transcript_exporter_accepts_absolute_target(tmp_path: Path) -> None:
    target = tmp_path / "absolute.md"

    result = TranscriptExporter(tmp_path / "other").export(
        (_cell("content", kind="assistant"),),
        target,
    )

    assert result.path == target
    assert target.is_file()


def test_transcript_exporter_reports_missing_parent_directory(
    tmp_path: Path,
) -> None:
    exporter = TranscriptExporter(tmp_path)

    with pytest.raises(OSError, match="could not create"):
        exporter.export(
            (_cell("content", kind="assistant"),),
            "missing/conversation.md",
        )


def test_transcript_exporter_writes_large_markdown_content(tmp_path: Path) -> None:
    content = "0123456789abcdef" * 65_536
    target = tmp_path / "large.md"

    TranscriptExporter(tmp_path).export(
        (_cell(content, kind="operation"),),
        target.name,
    )

    markdown = target.read_text(encoding="utf-8")
    assert markdown.startswith("# Mind conversation\n\n## Activity\n\n    ")
    assert content in markdown


def _host(tmp_path: Path, views: list) -> SimpleNamespace:
    """构造导出交互测试使用的最小 TUI 宿主。"""
    return SimpleNamespace(
        history_workspace=str(tmp_path),
        conversation=SimpleNamespace(sid="sid_export_123"),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )


@pytest.mark.anyio
async def test_export_conversation_matches_codex_file_menu_and_workspace(
    tmp_path: Path,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        _cell("question", kind="user", raw_text="**question**").display_block,
        kind="user",
        raw_text="**question**",
    )
    runtime.append_block(
        _cell("answer", kind="assistant", raw_text="answer").display_block,
        kind="assistant",
        raw_text="answer",
    )
    runtime.select_menu = AsyncMock(side_effect=("file", "custom.md"))
    views: list = []

    await export_conversation(runtime, _host(tmp_path, views))

    destination = runtime.select_menu.await_args_list[0].args[0]
    filename = runtime.select_menu.await_args_list[1].args[0]
    assert destination.title == "Export conversation"
    assert destination.body == ("Save the complete conversation as Markdown",)
    assert [option.label for option in destination.options] == [
        "Copy to clipboard",
        "Save to file",
    ]
    assert filename.title == "Save conversation"
    assert filename.text_input_gutter == "▌"
    assert filename.surface_horizontal_inset == 0
    assert filename.initial_query == "mind-session-sid_export_123.md"
    assert filename.text_input_mode is MenuTextInputMode.SINGLE_LINE
    assert (tmp_path / "custom.md").read_text(encoding="utf-8") == (
        "# Mind conversation\n\n"
        "## User\n\n**question**\n\n"
        "## Assistant\n\nanswer\n"
    )
    assert any(
        view.type == "tui.output"
        and "Saved conversation to" in str(view.renderable)
        for view in views
    )


@pytest.mark.anyio
async def test_export_conversation_copy_uses_same_markdown_in_raw_mode(
    tmp_path: Path,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        _cell("rendered", kind="assistant", raw_text="**source**").display_block,
        kind="assistant",
        raw_text="**source**",
    )
    runtime.set_raw_output_mode(True)
    runtime.select_menu = AsyncMock(return_value="clipboard")
    views: list = []

    with patch(
        "frontends.tui.features.transcript_export.copy_text_to_clipboard",
        new=AsyncMock(),
    ) as copy:
        await export_conversation(runtime, _host(tmp_path, views))

    copy.assert_awaited_once_with(
        "# Mind conversation\n\n## Assistant\n\n**source**\n"
    )
    assert runtime.select_menu.await_count == 1
    assert any(
        view.type == "tui.output"
        and "Copied conversation to clipboard" in str(view.renderable)
        for view in views
    )


@pytest.mark.anyio
async def test_export_conversation_direct_path_skips_destination_menu(
    tmp_path: Path,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(_cell("answer", kind="assistant").display_block, kind="assistant")
    runtime.select_menu = AsyncMock()

    await export_conversation(
        runtime,
        _host(tmp_path, []),
        requested_path="direct.md",
    )

    runtime.select_menu.assert_not_awaited()
    assert (tmp_path / "direct.md").is_file()


@pytest.mark.anyio
async def test_export_menu_surfaces_match_codex_layout() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )

    destination_result = menu.push(_destination_menu())
    assert fragments_text(menu.fragments()) == (
        "  Export conversation\n"
        "  Save the complete conversation as Markdown\n\n"
        "› 1. Copy to clipboard  Copy the complete Markdown transcript\n"
        "  2. Save to file       Choose a Markdown filename\n"
        "  \n"
        "  Press enter to confirm or esc to go back"
    )
    menu.cancel()
    assert await destination_result is None

    filename_result = menu.push(_filename_prompt("sid_export_123"))
    fragments = menu.fragments()
    rendered_lines = tuple(
        line.rstrip()
        for line in fragments_text(fragments).splitlines()
    )
    assert "\n".join(rendered_lines) == (
        "▌ Save conversation\n"
        "▌\n"
        "▌ mind-session-sid_export_123.md\n\n"
        "Press enter to confirm or esc to go back"
    )
    assert [
        text
        for style, text in fragments
        if "tui-menu.input-gutter" in style
    ] == ["▌ ", "▌", "▌ "]
    footer = menu.footer_fragments()
    assert [
        (style, text)
        for style, text in footer
        if text.strip()
    ] == [
        ("class:tui-menu.footer.hint", "Press "),
        ("class:tui-menu.footer.key", "enter"),
        ("class:tui-menu.footer.hint", " to confirm or "),
        ("class:tui-menu.footer.key", "esc"),
        ("class:tui-menu.footer.hint", " to go back"),
    ]
    menu.cancel()
    assert await filename_result is None


@pytest.mark.anyio
async def test_filename_menu_exposes_real_cursor_only_while_editing() -> None:
    runtime = TuiRuntime()
    style = runtime.screen.application.style

    assert not style.get_attrs_for_style_str(
        "class:tui-menu.footer.hint"
    ).dim
    assert style.get_attrs_for_style_str(
        "class:tui-menu.footer.key"
    ).dim

    filename_task = asyncio.create_task(
        runtime.select_menu(_filename_prompt("sid_cursor"))
    )
    await asyncio.sleep(0)

    content = runtime.screen.menu_control.create_content(80, None)
    assert not runtime.screen.menu_window.always_hide_cursor()
    assert not runtime.screen.startup_menu_window.always_hide_cursor()
    assert content.show_cursor
    assert content.cursor_position is not None

    runtime.screen.menu.cancel()
    assert await filename_task is None

    destination_task = asyncio.create_task(
        runtime.select_menu(_destination_menu())
    )
    await asyncio.sleep(0)

    assert runtime.screen.menu_window.always_hide_cursor()
    assert runtime.screen.startup_menu_window.always_hide_cursor()

    runtime.screen.menu.cancel()
    assert await destination_task is None
