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
    MAX_HIGHLIGHT_LINE_BYTES,
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
