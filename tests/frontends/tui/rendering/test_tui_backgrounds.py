"""验证 IDE 背景策略同时覆盖活动画布和原生滚屏输出。"""

import io

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
from frontends.tui.core.approval_render import TUI_APPROVAL_STYLE
from frontends.tui.core.menu import TUI_MENU_STYLE
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import (
    build_tui_application_style,
    build_tui_style_transformation,
    styled_block_fragments,
)
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
