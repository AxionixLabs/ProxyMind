"""验证 IDE 背景策略同时覆盖活动画布和原生滚屏输出。"""

import io
from unittest.mock import Mock

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.styles import Style

from agent.application.views import (
    PatchFileView,
    PatchHunkView,
    PatchLineView,
    PatchView,
)
from agent.application.views.builders.tools import (
    build_native_tool_result_view,
    build_tool_start_view,
)
from agent.domain.transcripts import TranscriptEntry
from frontends.interaction.contracts import PromptContext
from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalTheme,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from frontends.terminal.renderers.dispatch import render_presentation_view
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.presentation import TuiPresentationSink
from frontends.tui.core.approval_render import (
    TUI_APPROVAL_STYLE,
    approval_patch_pager_lines,
)
from frontends.tui.core.document import TuiDocument
from frontends.tui.core.menu import TUI_MENU_STYLE
from frontends.tui.core.models import FormattedText
from frontends.tui.core.render import join_formatted_lines
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import (
    build_tui_application_style,
    build_tui_style_transformation,
    styled_block_fragments,
)
from frontends.tui.features.history import load_history_transcript
from tests.frontends.tui.rendering.frame_scenarios import render_next_frame
from tests.pty.contract import TerminalSize
from tests.pty.terminal import TerminalScreen


def _capabilities(kind: TerminalKind) -> TerminalCapabilities:
    """创建具有相同色深和主题的对照终端。"""
    return TerminalCapabilities(
        identity=TerminalIdentity(kind, kind.value),
        color_support=TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        theme=TerminalTheme(background=(0, 0, 0)),
    )


class _FrameOutput(Vt100_Output):
    """复用真实 VT 输出器并提供稳定的内联可用高度。"""

    def get_rows_below_cursor_position(self) -> int:
        """返回从当前绘制原点到终端底部的可用行数。"""
        return self.get_size().rows


@pytest.mark.parametrize("kind", (
    TerminalKind.JETBRAINS_JEDITERM,
    TerminalKind.VSCODE,
    TerminalKind.ZED,
))
@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255), None))
def test_ide_style_removes_all_backgrounds_and_preserves_text_cues(
    kind: TerminalKind,
    background: tuple[int, int, int] | None,
) -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(kind, kind.value),
        color_support=TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        theme=TerminalTheme(background=background),
    )
    style = build_tui_application_style(
        Style.from_dict({}), TUI_APPROVAL_STYLE, TUI_MENU_STYLE,
        capabilities=capabilities,
    )
    transformation = build_tui_style_transformation(capabilities)
    selectors = [f"class:{selector}" for selector, _value in style.style_rules]
    selectors.extend((
        "class:approval-card class:approval-option-selected",
        "class:input-surface bg:default fg:default",
        "fg:ansigreen bg:ansired italic underline",
        "fg:ansicyan reverse",
    ))
    for selector in selectors:
        original = style.get_attrs_for_style_str(selector)
        rendered = transformation.transform_attrs(original)
        assert rendered.bgcolor == "", selector
        assert not rendered.reverse, selector
        assert rendered.color == (
            "" if original.color in {"default", "ansidefault"} else original.color
        ), selector
        assert rendered.italic == original.italic
        assert rendered.underline == original.underline
        if original.reverse:
            assert rendered.bold

    selection = transformation.transform_attrs(style.get_attrs_for_style_str(
        "class:transcript.overlay.selection",
    ))
    assert selection.bold and selection.underline
    assert transformation.transform_attrs(style.get_attrs_for_style_str(
        "class:approval-option-selected",
    )).bold


@pytest.mark.anyio
@pytest.mark.parametrize("kind", (
    TerminalKind.JETBRAINS_JEDITERM,
    TerminalKind.VSCODE,
    TerminalKind.WINDOWS_TERMINAL,
))
async def test_input_background_policy_preserves_layout_after_redraw(
    kind: TerminalKind,
) -> None:
    stream = io.StringIO()
    size = Size(rows=16, columns=80)
    output = _FrameOutput(stream, lambda: size, enable_cpr=False)
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(
            input_obj=input_obj, output_obj=output,
            terminal_capabilities=_capabilities(kind),
        )
        try:
            runtime.set_prompt_context(PromptContext(model="layout-probe"))
            await runtime.open()
            for value, height in (("draft", 1), ("x" * 165, 3), ("draft", 1), ("a\n中文\nz", 3)):
                runtime.screen.input.buffer.document = Document(value, len(value))
                frame = await render_next_frame(runtime)
                positions = frame.visible_windows_to_write_positions
                editor = positions[runtime.screen.input.window]
                footer = positions[runtime.screen.footer_window]
                assert (editor.ypos, editor.height, footer.ypos) == (2, height, height + 3)
                assert runtime.screen.input.buffer.text == value
                assert runtime.screen.input.buffer.cursor_position == len(value)

                terminal = TerminalScreen(TerminalSize(rows=size.rows, columns=size.columns))
                terminal.feed(stream.getvalue().encode("utf-8"))
                assert "layout-probe" in terminal.snapshot().visible_lines[footer.ypos]
                for row in range(1, height + 3):
                    background = terminal.cell(row, size.columns - 1).background
                    if kind is TerminalKind.WINDOWS_TERMINAL:
                        assert background != "default"
                    else:
                        assert background == "default"

                if value == "draft" and kind is not TerminalKind.WINDOWS_TERMINAL:
                    assert "draft" + " " * 20 not in stream.getvalue()
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", (
    TerminalKind.JETBRAINS_JEDITERM,
    TerminalKind.WINDOWS_TERMINAL,
))
async def test_native_scrollback_applies_same_background_policy(kind: TerminalKind) -> None:
    stream = io.StringIO()
    output = _FrameOutput(stream, lambda: Size(rows=16, columns=80), enable_cpr=False)
    runtime = TuiRuntime(output_obj=output, terminal_capabilities=_capabilities(kind))
    try:
        runtime.screen.application.print_text([
            ("class:approval-card class:approval-option-selected", "approval\n"),
            ("fg:ansigreen bg:ansired", "patch\n"),
            ("fg:ansicyan reverse", "selection\n"),
        ])
        terminal = TerminalScreen(TerminalSize(rows=16, columns=80))
        terminal.feed(stream.getvalue().encode("utf-8"))
        assert terminal.cell(0, 0).bold
        assert terminal.cell(1, 0).foreground == "green"
        assert terminal.cell(2, 0).foreground == "cyan"
        if kind is TerminalKind.JETBRAINS_JEDITERM:
            assert all(terminal.cell(row, 0).background == "default" for row in range(3))
            assert not terminal.cell(2, 0).reverse
            assert terminal.cell(2, 0).bold
        else:
            assert terminal.cell(0, 0).background != "default"
            assert terminal.cell(1, 0).background == "red"
            assert terminal.cell(2, 0).reverse
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("level", (
    TerminalColorLevel.UNKNOWN,
    TerminalColorLevel.ANSI16,
    TerminalColorLevel.ANSI256,
    TerminalColorLevel.TRUECOLOR,
    TerminalColorLevel.NONE,
))
@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
async def test_ide_patch_fallback_keeps_foregrounds_in_final_output(level, background) -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        color_support=TerminalColorSupport.fixed(level),
        theme=TerminalTheme(background=background),
    )
    view = PatchView("patch-colors", "applied", "", files=(PatchFileView(
        "update", ".gitignore", ".gitignore",
        (PatchHunkView((
            PatchLineView("remove", "old-pattern/", old_line=1),
            PatchLineView("add", "new-pattern/", new_line=1),
        )),),
        added=1, removed=1,
    ),))
    block = render_presentation_view(view, terminal_capabilities=capabilities)[0]
    assert all(span.style.background is None for span in block.spans)
    assert all(fill is None for fill in block.line_fill_styles)
    stream = io.StringIO()
    output = _FrameOutput(stream, lambda: Size(rows=16, columns=80), enable_cpr=False)
    runtime = TuiRuntime(output_obj=output, terminal_capabilities=capabilities)
    try:
        runtime.screen.application.print_text(list(styled_block_fragments(block)))
        terminal = TerminalScreen(TerminalSize(rows=16, columns=80))
        terminal.feed(stream.getvalue().encode("utf-8"))
        snapshot = terminal.snapshot()
        for text, color in (("old-pattern/", "red"), ("new-pattern/", "green")):
            row = next(i for i, line in enumerate(snapshot.visible_lines) if text in line)
            column = snapshot.visible_lines[row].index(text)
            expected = "default" if level is TerminalColorLevel.NONE else color
            assert terminal.cell(row, column - 1).foreground == expected
            assert terminal.cell(row, column).foreground == expected
        assert all(
            terminal.cell(row, column).background == "default"
            for row in range(3) for column in range(80)
        )
        assert capabilities.color_support.effective_level is level
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("level", tuple(TerminalColorLevel))
@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
async def test_patch_syntax_colors_survive_lifecycle_reflow_and_final_output(level, background) -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        TerminalColorSupport.fixed(level), TerminalTheme(background=background),
    )
    old = 'stages: "old"'
    new = 'stages: "validate" # ' + "long-comment " * 8
    raw_patch = f"*** Begin Patch\n*** Update File: .gitlab-ci.yml\n@@\n-{old}\n+{new}\n*** End Patch"
    data = {
        "files": [{
            "path": ".gitlab-ci.yml", "source_path": None, "action": "modify",
            "added_lines": 1, "removed_lines": 1,
        }],
        "delta": {"exact": True, "changes": [{
            "path": ".gitlab-ci.yml", "source_path": None, "action": "modify",
            "old_content": old + "\n", "new_content": new + "\n",
            "hunks": [{"lines": [
                {"kind": "remove", "text": old, "old_line": 1, "new_line": None},
                {"kind": "add", "text": new, "old_line": None, "new_line": 1},
            ]}],
        }]},
    }
    preview = build_tool_start_view(
        "apply_patch", {"patch": raw_patch}, patch_preview=data, call_id="colors",
    )
    result = build_native_tool_result_view(
        "apply_patch", {"patch": raw_patch}, ok=True, data=data, call_id="colors",
    )
    entry = TranscriptEntry(
        timestamp="2026-09-10T00:00:00.000Z", event="tool.completed",
        session_id="session-colors", turn_id="turn-colors", actor="tool",
        payload={
            "call_id": "colors", "name": "apply_patch", "ok": True,
            "arguments": {"patch": raw_patch},
            "result": {"ok": True, "text": "", "attachments": [], "data": data},
        },
    )
    host = Mock()
    host.conversation.history.read_transcript.return_value = (entry,)
    history = TuiDocument()
    history.replace_blocks(load_history_transcript(
        host, "session-colors", terminal_width=80, terminal_capabilities=capabilities,
    ))
    approval = approval_patch_pager_lines(
        {"tool": "apply_patch", "call_id": "colors", "patch": raw_patch, "preview": data},
        terminal_capabilities=capabilities,
    )
    stream = io.StringIO()
    output = _FrameOutput(stream, lambda: Size(rows=40, columns=160), enable_cpr=False)
    runtime = TuiRuntime(output_obj=output, terminal_capabilities=capabilities)
    presentation = TuiPresentationSink(TuiOutputControl("", runtime=runtime, animate=False))

    def printed_colors(fragments: FormattedText) -> tuple[str, ...]:
        stream.seek(0)
        stream.truncate(0)
        runtime.screen.application.print_text(list(fragments))
        terminal = TerminalScreen(TerminalSize(rows=40, columns=160))
        terminal.feed(stream.getvalue().encode("utf-8"))
        snapshot = terminal.snapshot()
        colors = []
        for text in ("stages", "validate"):
            row = next(i for i, line in enumerate(snapshot.visible_lines) if text in line)
            column = snapshot.visible_lines[row].index(text)
            colors.append(terminal.cell(row, column).foreground)
        assert all(
            terminal.cell(row, column).background == "default"
            for row in range(40) for column in range(160)
        )
        return tuple(colors)

    try:
        observed = []
        for view in (preview, result):
            await presentation.emit(view)
            assert len(runtime.document.blocks) == 1
            for width in (40, 80):
                observed.append(printed_colors(runtime.document.fragments(width=width)))
        for width in (40, 80):
            observed.append(printed_colors(history.fragments(width=width)))
        observed.append(printed_colors(join_formatted_lines(list(approval))))
        assert all(colors == observed[0] for colors in observed)
        if level is TerminalColorLevel.NONE:
            assert observed[0] == ("default", "default")
        else:
            assert "default" not in observed[0]
            assert observed[0][0] != observed[0][1]
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
async def test_patch_canvas_keeps_syntax_colors_when_terminal_width_changes(background) -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.JETBRAINS_JEDITERM, "test"),
        TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        TerminalTheme(background=background),
    )
    view = PatchView("canvas", "proposed", "", files=(PatchFileView(
        "add", "", ".gitlab-ci.yml",
        (PatchHunkView((PatchLineView(
            "add", 'stages: "validate" # ' + "wrapped " * 8, new_line=1,
        ),)),), added=1,
    ),))
    stream = io.StringIO()
    size = Size(rows=24, columns=80)
    output = _FrameOutput(stream, lambda: size, enable_cpr=False)
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(
            input_obj=input_obj, output_obj=output, terminal_capabilities=capabilities,
        )
        presentation = TuiPresentationSink(TuiOutputControl("", runtime=runtime, animate=False))
        try:
            await runtime.open()
            await presentation.emit(view)
            observed = []
            for width in (80, 40, 80):
                size = Size(rows=24, columns=width)
                frame = await render_next_frame(runtime)
                colors = []
                for text in ("stages", "validate"):
                    row = next(
                        row for row, cells in frame.data_buffer.items()
                        if text in "".join(cells[column].char for column in sorted(cells))
                    )
                    cells = frame.data_buffer[row]
                    line = "".join(cells[column].char for column in sorted(cells))
                    attrs = runtime.screen.application.style.get_attrs_for_style_str(
                        cells[line.index(text)].style,
                    )
                    colors.append(attrs.color.lower())
                    assert not attrs.bgcolor
                observed.append(colors)
            assert observed[0] == observed[1] == observed[2]
            expected = ["89b4fa", "a6e3a1"] if background == (0, 0, 0) else ["1e66f5", "40a02b"]
            assert observed[0] == expected
        finally:
            await runtime.close()
