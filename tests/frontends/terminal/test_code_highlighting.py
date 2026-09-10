"""验证共享语法主题、语言识别和有界高亮失败路径。"""

import pytest

from agent.application.views import (
    PatchFileView,
    PatchHunkView,
    PatchLineView,
    PatchView,
)
from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalTheme,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.highlighting import (
    MAX_HIGHLIGHT_BYTES,
    MAX_HIGHLIGHT_LINE_BYTES,
    MAX_HIGHLIGHT_LINES,
    StreamingCodeHighlighter,
    SyntaxTheme,
    highlight_code_lines,
    resolve_syntax_theme,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from frontends.terminal.renderers.dispatch import render_presentation_view
from frontends.terminal.traces.render import render_tool_trace_parts
from frontends.tui.adapters.markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_markdown,
)
from frontends.tui.core.styles import prompt_style


@pytest.mark.parametrize("level", (
    TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI256,
    TerminalColorLevel.ANSI16, TerminalColorLevel.UNKNOWN,
    TerminalColorLevel.NONE,
))
@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
def test_patch_markdown_and_preview_share_syntax_colors(level, background) -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        TerminalColorSupport.fixed(level), TerminalTheme(background=background),
    )
    source = 'stages: "validate"'
    view = PatchView("colors", "applied", "", files=(PatchFileView(
        "add", "", ".gitlab-ci.yml",
        (PatchHunkView((PatchLineView("add", source, new_line=1),)),), added=1,
    ),))
    block = render_presentation_view(view, terminal_capabilities=capabilities)[0]
    markdown = render_tui_markdown(
        f"```yaml\n{source}\n```", terminal_capabilities=capabilities,
    )
    preview = render_tool_trace_parts(
        "", preview=f".gitlab-ci.yml\n1 +{source}",
        syntax_theme=resolve_syntax_theme(capabilities),
    )
    token_styles = []
    for token in ("stages", "validate"):
        patch_style = next(span.style for span in block.spans if token in span.text)
        preview_style = next(span.style for span in preview if token in span.text)
        markdown_style = next(style for style, text in markdown.fragments if token in text)
        assert patch_style == preview_style
        assert markdown_style == prompt_style(patch_style)
        token_styles.append(patch_style.foreground)
    if level is TerminalColorLevel.NONE:
        assert token_styles == [None, None]
    else:
        assert all(token_styles)
        assert token_styles[0] != token_styles[1]
    assert all(span.style.background is None for span in block.spans)


@pytest.mark.parametrize("header", ("#!/bin/sh", "#!/usr/bin/env bash", "#!/usr/bin/env -S bash -e"))
def test_extensionless_shell_uses_verified_first_line(header) -> None:
    source = 'if command -v pwsh; then\n  echo "$repo_root"\nfi'
    lines = highlight_code_lines(
        source, path=".githooks/pre-commit", first_line=header, theme=SyntaxTheme.MOCHA,
    )
    assert lines is not None
    assert next(span for span in lines[0] if span.text == "if").style.foreground == "#CBA6F7"
    assert any(span.style.foreground == "#A6E3A1" for span in lines[1])
    assert highlight_code_lines(source, path=".githooks/pre-commit") is None


@pytest.mark.parametrize("path", (".gitlab-ci.yml", "C:\\repo\\.gitlab-ci.yml", "Dockerfile"))
def test_known_filenames_are_resolved_without_reading_files(path) -> None:
    source = 'FROM python:3.11' if path == "Dockerfile" else 'stages: "validate"'
    assert highlight_code_lines(source, path=path, theme=SyntaxTheme.MOCHA) is not None


def test_unknown_language_and_oversized_input_preserve_plain_fallback() -> None:
    assert highlight_code_lines(".idea/", path=".gitignore") is None
    assert highlight_code_lines("plain", language="missing-language") is None
    assert highlight_code_lines("x" * (MAX_HIGHLIGHT_LINE_BYTES + 1), path="code.py") is None
    assert StreamingCodeHighlighter().render(
        "x" * (MAX_HIGHLIGHT_LINE_BYTES + 1) + "\n", language="python",
    ) is None


@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
@pytest.mark.parametrize("level", (TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI16, TerminalColorLevel.NONE))
def test_streaming_and_nested_markdown_keep_same_theme(background, level) -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        TerminalColorSupport.fixed(level), TerminalTheme(background=background),
    )
    renderer = TuiMarkdownStreamRenderer(terminal_capabilities=capabilities)
    source = '```python\n\ndef greet():\n    text = """hello\nworld\n"""\n'
    assert renderer.render(source) == render_tui_markdown(source, terminal_capabilities=capabilities)
    source += '    return text\n```\n'
    assert renderer.render(source, final=True) == render_tui_markdown(source, terminal_capabilities=capabilities)
    nested = '> - ```yaml\n>   stages: "validate"\n>   ```'
    assert renderer.render(nested, final=True) == render_tui_markdown(nested, terminal_capabilities=capabilities)


def _render_hunks(*hunks: PatchHunkView, path: str = "sample.py", width: int = 80):
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        TerminalTheme(background=(0, 0, 0)),
    )
    view = PatchView("syntax-state", "applied", "", files=(PatchFileView(
        "update", path, path, hunks,
    ),))
    return render_presentation_view(
        view, terminal_width=width, terminal_capabilities=capabilities,
    )[0]


def test_replaced_multiline_string_openers_do_not_cancel_each_other() -> None:
    block = _render_hunks(PatchHunkView((
        PatchLineView("remove", 'text = """old', old_line=1),
        PatchLineView("add", 'text = """new', new_line=1),
        PatchLineView("context", "inside", old_line=2, new_line=2),
        PatchLineView("context", '"""', old_line=3, new_line=3),
        PatchLineView("context", "return True", old_line=4, new_line=4),
    )))
    inside = next(span for span in block.spans if span.text == "inside")
    keyword = next(span for span in block.spans if span.text == "return")
    assert inside.style.foreground == "#A6E3A1"
    assert keyword.style.foreground == "#CBA6F7"
    assert keyword.style.bold and not keyword.style.dim
    for text, dim in (("old", True), ("new", False)):
        span = next(span for span in block.spans if text in span.text)
        assert span.style.foreground == "#A6E3A1"
        assert span.style.dim is dim


def test_context_uses_new_side_after_multiline_delimiters_are_removed() -> None:
    block = _render_hunks(PatchHunkView((
        PatchLineView("remove", '"""', old_line=1),
        PatchLineView("context", "return True", old_line=2, new_line=1),
        PatchLineView("remove", '"""', old_line=3),
    )))
    keyword = next(span for span in block.spans if span.text == "return")
    assert keyword.style.foreground == "#CBA6F7"
    assert keyword.style.bold and not keyword.style.dim
    assert all(span.style.dim for span in block.spans if span.text == '"""')


def test_distant_hunks_do_not_inherit_unseen_multiline_state() -> None:
    block = _render_hunks(
        PatchHunkView((PatchLineView("add", 'text = """opening', new_line=1),)),
        PatchHunkView((PatchLineView("add", "return True", new_line=100),)),
    )
    assert "⋮" in block.plain_text
    keyword = next(span for span in block.spans if span.text == "return")
    assert keyword.style.foreground == "#CBA6F7"


def test_extensionless_patch_uses_each_sides_own_shebang() -> None:
    block = _render_hunks(PatchHunkView((
        PatchLineView("remove", "#!/usr/bin/env python", old_line=1),
        PatchLineView("remove", "return True", old_line=2),
        PatchLineView("add", "#!/bin/sh", new_line=1),
        PatchLineView("add", "if command -v pwsh; then", new_line=2),
        PatchLineView("add", "  echo done", new_line=3),
        PatchLineView("add", "fi", new_line=4),
    )), path=".githooks/pre-commit")
    for keyword, dim in (("return", True), ("if", False)):
        span = next(span for span in block.spans if span.text == keyword)
        assert span.style.foreground == "#CBA6F7"
        assert span.style.dim is dim


def test_shebang_fragment_away_from_first_line_keeps_diff_fallback() -> None:
    block = _render_hunks(PatchHunkView((
        PatchLineView("add", "#!/bin/sh", new_line=40),
        PatchLineView("add", "if command -v pwsh; then", new_line=41),
    )), path=".githooks/pre-commit")
    span = next(span for span in block.spans if "if command" in span.text)
    assert span.style.foreground == "ansigreen"


@pytest.mark.parametrize("source", (
    pytest.param("x\n" * MAX_HIGHLIGHT_LINES, id="line-count"),
    pytest.param(
        ("x" * 1023 + "\n") * (MAX_HIGHLIGHT_BYTES // 1024 + 1),
        id="total-bytes",
    ),
))
def test_code_block_input_limits_keep_plain_fallback(source) -> None:
    assert highlight_code_lines(source, path="sample.py") is None


def test_highlighted_unicode_and_tabs_keep_text_and_empty_continuation_gutter() -> None:
    source = '\ttext = "' + "界🙂" * 12 + '"'
    block = _render_hunks(
        PatchHunkView((PatchLineView("add", source, new_line=10),)), width=24,
    )
    rows = block.plain_text.splitlines()
    rows = rows[next(index for index, row in enumerate(rows) if row.startswith("    10 +")):]
    assert rows[0].startswith("    10 +")
    assert all(row.startswith("        ") for row in rows[1:])
    assert "".join(row[8:] for row in rows) == source.replace("\t", "    ")
    assert any(span.style.foreground == "#A6E3A1" for span in block.spans)
