# -*- coding: utf-8 -*-

"""验证 Markdown 增量解析、表格与流式安全前缀。"""


import asyncio
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    call,
    patch,
)
import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from frontends.terminal import highlighting as code_highlight
from frontends.tui.adapters import markdown as tui_markdown
from frontends.tui.adapters.markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_assistant_markdown,
    render_tui_markdown,
)
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.core.models import (
    FragmentBlock,
    LineFill,
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    sanitize_formatted_text,
    split_formatted_lines,
    wrap_formatted_lines,
)
from frontends.tui.core.runtime import TuiRuntime
from tests.frontends.tui.rendering.frame_scenarios import (
    document_text as _document_text,
    render_next_frame as _render_next_frame,
)


@pytest.mark.anyio
async def test_continuous_markdown_stream_renders_only_complete_source_lines() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                with patch(
                        "frontends.tui.adapters.markdown.render_tui_markdown",
                    wraps=render_tui_markdown,
                ) as render:
                    runtime.set_execution_active(True)
                    for index in range(40):
                        prefix = "" if index == 0 else "\n"
                        await output.append_assistant_delta(
                            prefix
                            + f"- **entry {index:02d}** "
                            + "[docs](https://example.com/reference)"
                        )
                        await asyncio.sleep(0.005)

                    await asyncio.sleep(0.12)

                    render.assert_not_called()
                    active = runtime.document.active_block
                    assert active is not None
                    active_text = fragments_text(active.fragments)
                    assert "**" not in active_text
                    assert "https://" not in active_text
                    assert "entry 00 docs" in active_text
                    assert "entry 38 docs" in active_text
                    assert "entry 39 docs" not in active_text
                    assert any(
                        "bold" in style
                        for style, text in active.fragments
                        if text.strip()
                    )

                    await output.settle_stream()

                    render.assert_not_called()
                    active = runtime.document.active_block
                    assert active is not None
                    assert "entry 39 docs" in fragments_text(active.fragments)

                    source = output.assistant.text
                    await output.prepare_external_output()

                    render.assert_called_once_with(
                        source,
                        hyperlinks=runtime.hyperlinks_enabled,
                        width=max(1, runtime.terminal_width - 2),
                    )
                    settled = runtime.document.blocks[-1].display_block
                    settled_text = fragments_text(settled.fragments)
                    assert "**" not in settled_text
                    assert "https://" not in settled_text
                    assert any(
                        "bold" in style
                        for style, text in settled.fragments
                        if text.strip()
                    )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


def test_markdown_uses_terminal_native_semantic_hierarchy() -> None:
    block = render_tui_markdown(
        "# Primary\n\n"
        "## Secondary\n\n"
        "### Tertiary\n\n"
        "#### Detail\n\n"
        "Body with **strong**, *emphasis*, ~~old~~ and `code`.\n\n"
        "- bullet\n"
        "1. ordered\n\n"
        "> quoted\n\n"
        "---\n\n"
        "[docs](https://example.com)",
        width=60,
    )
    fragments = list(block.fragments)
    text = fragments_text(fragments)

    assert text.startswith(
        "# Primary\n\n## Secondary\n\n### Tertiary\n\n#### Detail"
    )
    assert any(
        "bold" in style and "underline" in style and value == "# Primary"
        for style, value in fragments
    )
    assert any(
        "bold" in style and "italic" in style and value == "### Tertiary"
        for style, value in fragments
    )
    assert any(
        "dim" in style and "italic" in style and value == "#### Detail"
        for style, value in fragments
    )
    assert any("strike" in style and value == "old" for style, value in fragments)
    assert any(
        style == "class:terminal.accent" and value == "code"
        for style, value in fragments
    )
    assert any("dim" in style and value == "- " for style, value in fragments)
    assert any(
        style == "class:terminal.accent" and value == "1. "
        for style, value in fragments
    )
    assert any(
        style == "class:terminal.success dim" and value == "▎ "
        for style, value in fragments
    )
    assert any("dim" in style and value == "quoted" for style, value in fragments)
    assert "———" in text
    assert any(
        "underline" in style and value == "docs"
        for style, value in fragments
    )
    assert any(style == "" and "Body with " in value for style, value in fragments)


def test_assistant_markdown_wraps_plain_paragraphs_before_prefixing() -> None:
    source = (
        "从量化指标来看，候选项集中在 app-qa（102 个文件，资产最多）、"
        "ncc-desktop-testing（SKILL.md 16.7KB，最全面，包含 cases）和 "
        "ntcpc-desktop-testing（结构最全面，包含 reports）。接下来阅读这几位候选人的 "
        "SKILL.md 正文，以评估契约质量。"
    )

    rendered_lines = {}
    for width in (48, 32):
        lines = fragments_text(
            render_tui_assistant_markdown(source, width=width).fragments
        ).splitlines()

        assert lines[0].startswith("• ")
        assert len(lines) > 1
        assert all(line.startswith("  ") for line in lines[1:])
        assert all(get_cwidth(line) <= width for line in lines)
        rendered_lines[width] = lines

    assert len(rendered_lines[32]) >= len(rendered_lines[48])


def test_multiline_list_items_align_wrapped_content_and_stay_separated() -> None:
    block = render_tui_markdown(
        "1. a deliberately long first list item for wrapping\n"
        "2. short second item",
        width=24,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert lines[0].startswith("1. ")
    assert lines[1].startswith("   ")
    assert lines[2].startswith("   ")
    assert "" in lines
    assert lines[-1].startswith("2. ")
    assert all(get_cwidth(line) <= 24 for line in lines)


def test_markdown_stream_replaces_only_the_mutable_setext_tail() -> None:
    renderer = TuiMarkdownStreamRenderer()

    paragraph = renderer.render("first\n\nTitle\n")
    assert fragments_text(paragraph.fragments) == "first\n\nTitle"
    assert all(
        "bold" not in style
        for style, text in paragraph.fragments
        if text == "Title"
    )

    heading = renderer.render("first\n\nTitle\n---\n")
    assert fragments_text(heading.fragments) == "first\n\n## Title"
    assert any(
        "bold" in style
        for style, text in heading.fragments
        if text == "## Title"
    )


@pytest.mark.anyio
async def test_split_markdown_tokens_appear_only_after_the_source_line_closes() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bo")
    await output.append_assistant_delta("ld**")
    assert runtime.document.active_block is None

    await output.append_assistant_delta("\n")
    active = runtime.document.active_block
    assert active is not None
    assert fragments_text(active.fragments) == "• bold"
    assert any(
        "bold" in style
        for style, text in active.fragments
        if text == "bold"
    )


def test_markdown_stream_does_not_rerender_stable_top_level_blocks() -> None:
    renderer = TuiMarkdownStreamRenderer()

    with patch.object(
        tui_markdown._MARKDOWN,
        "parse",
        wraps=tui_markdown._MARKDOWN.parse,
    ) as parse:
        renderer.render("first\n\nsecond\n")
        first_parse_count = parse.call_count
        renderer.render("first\n\nsecond\n\nthird\n")

    new_sources = [
        call_args.args[0]
        for call_args in parse.call_args_list[first_parse_count:]
    ]
    assert new_sources == ["second\n\nthird\n"]


@pytest.mark.parametrize(
    ("initial", "extended"),
    (
        ("- first\n\n", "- first\n\n  continuation\n\n"),
        ("1. first\n\n", "1. first\n\n   second paragraph\n\n"),
    ),
)
def test_markdown_stream_keeps_the_last_commonmark_block_mutable(
    initial: str,
    extended: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()
    renderer.render(initial, width=40)

    streamed = renderer.render(extended, width=40)
    final = render_tui_markdown(extended, width=40)

    assert fragments_text(streamed.fragments) == fragments_text(final.fragments)


def test_plain_multiline_stream_skips_repeated_markdown_parsing() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = ""

    with patch.object(tui_markdown._MARKDOWN, "parse") as parse:
        for index in range(1000):
            source += f"plain line {index}\n"
            renderer.render(source, width=80)

    parse.assert_not_called()


def test_reference_link_definition_recomputes_the_stable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()
    unresolved = renderer.render(
        "[docs][ref]\n\nfollowing paragraph\n",
        width=40,
    )
    assert "[docs][ref]" in fragments_text(unresolved.fragments)

    resolved = renderer.render(
        "[docs][ref]\n\nfollowing paragraph\n\n"
        "[ref]: https://example.com/reference\n",
        width=40,
    )

    assert "[docs][ref]" not in fragments_text(resolved.fragments)
    assert fragments_text(resolved.fragments).startswith("docs\n\n")
    assert any(
        "underline" in style
        for style, text in resolved.fragments
        if text == "docs"
    )


def test_streaming_markdown_exposes_only_source_compatible_stable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render("# one\nparagraph", width=40)
    source_len, block = renderer.stable_prefix()

    assert source_len == len("# one\n")
    assert fragments_text(block.fragments) == "# one"

    renderer.reset()
    renderer.render(
        "```markdown\n| A | B |\n|---|---|\n| 1 | 2 |\n```",
        width=40,
    )

    assert renderer.stable_prefix() == (0, FragmentBlock(()))


@pytest.mark.parametrize(
    ("source", "stable_source"),
    (
        (
            "## [docs](https://example.com)\nparagraph",
            "## [docs](https://example.com)\n",
        ),
        ("## `[items[index]]`\nparagraph", "## `[items[index]]`\n"),
        ("## \\[literal] text\nparagraph", "## \\[literal] text\n"),
        (
            "```python\nvalue = items[index]\n```\nparagraph",
            "```python\nvalue = items[index]\n```\n",
        ),
        (
            "```\n[reference]: https://example.com\n```\nparagraph",
            "```\n[reference]: https://example.com\n```\n",
        ),
    ),
)
def test_streaming_markdown_parser_allows_reference_safe_syntax(
    source: str,
    stable_source: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render(source, width=40)
    source_len, _block = renderer.stable_prefix()

    assert source_len == len(stable_source)


@pytest.mark.parametrize(
    "reference_source",
    (
        "## [docs][reference]\n\nfollowing paragraph\n",
        "## ![preview][image]\n\nfollowing paragraph\n",
        "- [x] task\n\nfollowing paragraph\n",
    ),
)
def test_streaming_markdown_keeps_reference_syntax_mutable(
    reference_source: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render(reference_source, width=40)

    assert renderer.stable_prefix() == (0, FragmentBlock(()))


def test_reference_definition_preserves_prior_committable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()
    initial = (
        "## safe heading\n\n"
        "## [docs][reference]\n\n"
        "following paragraph\n"
    )

    renderer.render(initial, width=40)
    initial_end, initial_block = renderer.stable_prefix()

    assert initial_end == len("## safe heading\n\n")
    assert fragments_text(initial_block.fragments) == "## safe heading"

    extended = initial + "\n[reference]: https://example.com/reference\n"
    rendered = renderer.render(extended, width=40)
    resolved_end, resolved_block = renderer.stable_prefix()

    assert resolved_end == initial_end
    assert resolved_block == initial_block
    assert "[docs][reference]" not in fragments_text(rendered.fragments)
    assert any(
        "underline" in style
        for style, text in rendered.fragments
        if text == "docs"
    )


def test_final_markdown_makes_remaining_reference_source_committable() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "## [docs][reference]\n\nfollowing paragraph\n"

    renderer.render(source, width=40)
    assert renderer.stable_prefix() == (0, FragmentBlock(()))

    renderer.render(source, final=True, width=40)
    source_len, block = renderer.stable_prefix()

    assert source_len == len(source)
    assert "[docs][reference]" in fragments_text(block.fragments)


@pytest.mark.anyio
async def test_streaming_fence_never_displays_raw_fence_markers() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("```python\nprint('ok')\n")

    active = runtime.document.active_block
    assert active is not None
    assert fragments_text(active.fragments) == "• print('ok')"
    assert any(
        "fg:" in style
        for style, text in active.fragments
        if "print" in text
    )

    await output.append_assistant_delta("```\n")
    assert "```" not in _document_text(runtime.document)


@pytest.mark.parametrize(
    ("language", "source_lines"),
    (
        (
            "python",
            ('value = """first\n', "second\n", 'third"""\n', "print(value)\n"),
        ),
        (
            "python",
            ('"""module docs\n', "continued docs\n", 'end docs"""\n'),
        ),
        (
            "python",
            ("\n", "\n", "first = 1\n", "\n", "second = 2\n"),
        ),
        (
            "javascript",
            ("const value = `first\n", "second ${1 + 2}\n", "third`;\n"),
        ),
        (
            "rust",
            ("/* first\n", "second\n", "third */\n", "let value = 3;\n"),
        ),
        (
            "html",
            ("<section>\n", "<!-- first\n", "second -->\n", "</section>\n"),
        ),
    ),
)
def test_streaming_open_code_fence_matches_canonical_highlighting_per_line(
    language: str,
    source_lines: tuple[str, ...],
) -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = f"```{language}\n"

    renderer.render(source, width=80)
    for source_line in source_lines:
        source += source_line
        streamed = renderer.render(source, width=80)
        canonical = render_tui_markdown(source, width=80)

        assert streamed.fragments == canonical.fragments


def test_streaming_open_code_fence_lexes_only_appended_complete_lines() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "```python\n"
    renderer.render(source, width=80)

    with patch.object(
        code_highlight,
        "_lex_regex_suffix",
        wraps=code_highlight._lex_regex_suffix,
    ) as lex_suffix:
        source += "first = 1\n"
        renderer.render(source, width=80)
        source += "second = first + 1\n"
        renderer.render(source, width=80)

    assert [item.args[1] for item in lex_suffix.call_args_list] == [
        "first = 1\n",
        "second = first + 1\n",
    ]

    closed_source = source + "```\n"
    closed = renderer.render(closed_source, width=80)
    canonical = render_tui_markdown(closed_source, width=80)

    assert closed.fragments == canonical.fragments


@pytest.mark.anyio
async def test_streaming_table_replaces_the_whole_mutable_tail() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("| Name | Value |\n")
    assert "| Name | Value |" in _document_text(runtime.document)

    await output.append_assistant_delta("|:---|---:|\n")
    header = _document_text(runtime.document)
    assert "|:---|---:|" not in header
    assert "━" in header
    assert not any(character in header for character in "┌┬┐├┼┤└┴┘│")

    await output.append_assistant_delta("| Longer Name | 1000 |\n")
    table = _document_text(runtime.document)
    assert "Longer Name" in table
    assert table.count("━") > header.count("━")
    assert any(
        "bold" in style
        for style, text in runtime.document.active_block.fragments
        if text in {"Name", "Value"}
    )


def test_streaming_table_wraps_cells_without_heavy_borders() -> None:
    renderer = TuiMarkdownStreamRenderer()
    block = renderer.render(
        "| Name | Value |\n"
        "|---|---|\n"
        "| a very long name | a very long value |\n",
        width=22,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert len(lines) >= 4
    assert all(get_cwidth(line) == 22 for line in lines)
    assert set(lines[1]) == {"━"}
    assert not any(
        character in "".join(lines)
        for character in "┌┬┐├┼┤└┴┘│"
    )


def test_narrow_markdown_table_switches_to_stacked_records() -> None:
    block = render_tui_markdown(
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |\n"
        "| Worker | Running | Processing jobs |\n",
        width=22,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert lines[:6] == [
        " Name",
        "  API",
        " Status",
        "  Ready",
        " Description",
        "  Service is available",
    ]
    assert set(lines[6]) == {"─"}
    assert "  Processing jobs" in lines
    assert all(get_cwidth(line) <= 22 for line in lines)


@pytest.mark.parametrize("width", (22, 80))
def test_streaming_table_increment_matches_full_render(width: int) -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "| Name | Value |\n|---|---|\n"
    rows = (
        "| A | one |\n",
        "| Longer Name | `two` |\n",
        "| C | a value that changes the fitted width |\n",
    )

    for row in rows:
        source += row
        assert renderer.render(source, width=width) == render_tui_markdown(
            source,
            width=width,
        )

    source += "\n| separate | paragraph |\n"
    assert renderer.render(source, width=width) == render_tui_markdown(
        source,
        width=width,
    )


def test_streaming_table_parses_only_the_appended_row() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "| Name | Value |\n|---|---|\n| existing | one |\n"
    renderer.render(source, width=80)

    with patch.object(
        tui_markdown._MARKDOWN,
        "parse",
        wraps=tui_markdown._MARKDOWN.parse,
    ) as parse:
        renderer.render(source + "| appended | two |\n", width=80)

    parse.assert_called_once()
    parsed_source = parse.call_args.args[0]
    assert "appended" in parsed_source
    assert "existing" not in parsed_source


@pytest.mark.anyio
async def test_source_reflow_invalidates_the_screen_transcript_cache() -> None:
    runtime = TuiRuntime()
    size = [60, 24]
    runtime.screen._output_size = lambda: tuple(size)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = (
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |\n"
        "| Worker | Running | Processing jobs |\n"
    )

    await output.append_assistant_delta(source)
    await output.prepare_external_output()
    runtime.screen.transcript_fragments()

    size[0] = 22
    runtime.document.set_display_width(22, reflow_sources=False)
    before_reflow = fragments_text(runtime.screen.transcript_fragments())

    assert runtime.document.set_display_width(22, reflow_sources=True)
    after_reflow = fragments_text(runtime.screen.transcript_fragments())
    expected = fragments_text(
        runtime.document.fragments(width=22, reflow_sources=False)
    )

    assert after_reflow != before_reflow
    assert after_reflow == expected
    assert after_reflow.count("Description") == 2
    assert all(get_cwidth(line) <= 22 for line in after_reflow.splitlines())


def test_markdown_only_table_fence_is_rendered_as_a_table() -> None:
    source = (
        "```markdown\n"
        "| Name | Value |\n"
        "|---|---|\n"
        "| API | Ready |\n"
        "```"
    )
    block = render_tui_markdown(source, width=30)
    text = fragments_text(block.fragments)

    assert "```" not in text
    assert "|---|" not in text
    assert "━" in text
    assert "API" in text and "Ready" in text


def test_markdown_table_fence_accepts_a_longer_closing_fence() -> None:
    block = render_tui_markdown(
        "```markdown\n"
        "| Name | Value |\n"
        "|---|---|\n"
        "| API | Ready |\n"
        "````",
        width=30,
    )
    text = fragments_text(block.fragments)

    assert "|---|" not in text
    assert "━" in text


@pytest.mark.anyio
@pytest.mark.parametrize("animate", (False, True))
async def test_streamed_tool_table_never_leaves_a_duplicate_mutable_row(
    animate: bool,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (48, 24)
    output = TuiOutputControl("", runtime=runtime, animate=animate)
    source = (
        "## 命令行工具\n\n"
        "| 工具 | 用途 |\n"
        "|---|---|\n"
        "| rg (ripgrep) | 快速文本搜索、文件发现 |\n"
        "| ast-grep | 基于语法结构的代码搜索和改写 |\n"
        "| jq | JSON 数据处理和查询 |\n"
        "| yq | YAML 数据处理和查询 |\n"
        "| sqlite3 | SQLite 数据库查询 |\n"
        "| xh | HTTP 请求（类似 httpie） |\n"
        "| 7z | 文件压缩/解压 |\n"
    )

    for line in source.splitlines(keepends=True):
        await output.append_assistant_delta(line)
        if animate:
            output._cancel_stream_render()
            while output._stream_visible_rows < len(output._stream_rows):
                output._render_stream_frame()

        active = runtime.document.active_block
        visible = fragments_text(active.fragments) if active is not None else ""
        assert visible.count("xh") <= 1
        if "xh" in visible:
            assert visible.index("## 命令行工具") < visible.index("xh")

    streamed = fragments_text(runtime.document.active_block.fragments)
    await output.prepare_external_output()
    final = fragments_text(runtime.document.blocks[-1].display_block.fragments)

    assert streamed.count("xh") == 1
    assert final.count("xh") == 1
    assert streamed == final


def test_non_table_markdown_fence_stays_a_code_block() -> None:
    block = render_tui_markdown(
        "```markdown\n# literal heading\n```",
        width=30,
    )

    assert fragments_text(block.fragments) == "# literal heading"
    assert all(
        "bold" not in style
        for style, text in block.fragments
        if "literal heading" in text
    )


@pytest.mark.anyio
async def test_final_markdown_table_rerenders_from_source_on_resize() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = (
        "| Name | Value |\n"
        "|---|---|\n"
        "| a very long name | a very long value |"
    )

    await output.append_assistant_delta(source)
    await output.prepare_external_output()

    narrow = fragments_text(runtime.document.fragments(width=24)).splitlines()
    assert all(get_cwidth(line) == 24 for line in narrow)
    assert set(narrow[1].strip()) == {"━"}
    assert not any(
        character in "".join(narrow)
        for character in "┌┬┐├┼┤└┴┘│"
    )
    cell = runtime.document.blocks[-1]
    transcript = fragments_text(
        runtime.document.transcript_cell_fragments(cell, width=24)
    ).splitlines()
    assert transcript == narrow
    assert cell.raw_text == source


@pytest.mark.anyio
async def test_stream_animation_releases_one_complete_display_row_per_tick() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("one\ntwo\nthree\n")
    output._cancel_stream_render()

    assert output._stream_visible_rows == 1
    assert _document_text(runtime.document) == "• one"

    output._render_stream_frame()
    assert output._stream_visible_rows == 2
    assert _document_text(runtime.document) == "• one\n  two"

    output._render_stream_frame()
    assert output._stream_visible_rows == 3
    assert _document_text(runtime.document) == "• one\n  two\n  three"


@pytest.mark.anyio
async def test_stream_animation_catches_up_when_line_queue_is_large() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    source = "".join(f"line {index}\n" for index in range(8))

    await output.append_assistant_delta(source)

    assert output._stream_visible_rows == len(output._stream_rows) == 8
    assert _document_text(runtime.document).endswith("  line 7")


@pytest.mark.anyio
async def test_stream_animation_catches_up_when_oldest_row_waits_too_long() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("one\ntwo\nthree\n")
    output._cancel_stream_render()
    assert output._stream_visible_rows == 1
    assert output._stream_oldest_pending_at is not None

    output._stream_oldest_pending_at -= 0.13
    output._render_stream_frame()

    assert output._stream_visible_rows == len(output._stream_rows) == 3
    assert _document_text(runtime.document).endswith("  three")


@pytest.mark.anyio
async def test_active_markdown_stream_rerenders_from_source_on_resize() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        terminal_size = Size(rows=24, columns=40)
        source = "x" * 70

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                with patch.object(
                    output._markdown_stream,
                    "render",
                    wraps=output._markdown_stream.render,
                ) as render:
                    await output.append_assistant_delta(source + "\n")
                    await _render_next_frame(runtime)
                    assert output._stream_width == 38

                    terminal_size = Size(rows=24, columns=24)
                    await _render_next_frame(runtime)
                    await asyncio.sleep(0.09)
                    await _render_next_frame(runtime)

                assert output._stream_width == 22
                assert render.call_count == 2
                lines = _document_text(runtime.document).splitlines()
                assert "".join(line[2:] for line in lines) == source
            finally:
                await runtime.close()
