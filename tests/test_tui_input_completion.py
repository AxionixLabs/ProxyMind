# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.presentation.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
)
from infrastructure.skills import SkillSpec
from mind_app.interaction.contracts import PromptContext
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import text_block
from frontends.tui.core.token_menu import (
    TokenCompletionMenuControl,
    TokenMenuItem,
    TokenMenuSnapshot,
    token_menu_display_height,
)
from frontends.tui.prompting.skills import (
    skill_display_text,
    skill_meta_text,
    skill_completions,
    skill_query_token,
)
from frontends.tui.session.barriers import TuiForegroundTasks


async def wait_for_completion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的异步补全结果就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        state = runtime.screen.input.buffer.complete_state
        if state is not None and state.completions:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not become ready")


async def wait_for_no_completion(runtime: TuiRuntime) -> None:
    """等待当前输入的补全菜单收起。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.complete_state is None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not close")


async def wait_for_suggestion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的行内联想就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.suggestion is not None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("suggestion did not become ready")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def wait_for_cursor_position(runtime: TuiRuntime, position: int) -> None:
    """等待输入光标移动到指定位置。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.cursor_position == position:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input cursor did not move to {position}")


async def render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


async def wait_for_submission(runtime: TuiRuntime):
    """等待输入处理结果进入提交队列。"""
    return await asyncio.wait_for(
        runtime.submissions.read_submission(),
        timeout=1.0,
    )


@pytest.mark.anyio
async def test_bracketed_paste_sanitizes_control_characters() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(
                "\x1b[200~BugID\t提交时间\r\n. 3117\t已解决\x00\x1b[201~"
            )
            await wait_for_input_text(
                runtime,
                "BugID   提交时间\n. 3117  已解决",
            )
        finally:
            await runtime.close()


def rendered_input_line(runtime: TuiRuntime) -> str:
    """返回最近一次渲染中的首行输入文本。"""
    return rendered_input_lines(runtime)[0]


def rendered_input_lines(runtime: TuiRuntime) -> list[str]:
    """返回最近一次渲染中的完整输入区域文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    prompt_position = screen.visible_windows_to_write_positions[
        runtime.screen.input_prompt_window
    ]
    input_position = screen.visible_windows_to_write_positions[
        runtime.screen.input.window
    ]
    end_column = input_position.xpos + input_position.width
    return [
        "".join(
            screen.data_buffer[row][column].char
            for column in range(prompt_position.xpos, end_column)
        ).rstrip()
        for row in range(
            input_position.ypos,
            input_position.ypos + input_position.height,
        )
    ]


def rendered_window_line(runtime: TuiRuntime, window) -> str:
    """返回指定窗口最近一次渲染的首行文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[window]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos + position.width)
    ).rstrip()


def skill_spec(name: str, description: str | None = None) -> SkillSpec:
    """创建输入补全测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=description or f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


def test_skill_menu_text_matches_codex_row_shape() -> None:
    skill = skill_spec(
        "abcdefghijklmnopqrstuvwxyz-long",
        description="  write   tests\nquickly  ",
    )

    assert skill_display_text("alpha") == "alpha"
    assert skill_display_text(skill.name) == "abcdefghijklmnopqrstuvwxy..."
    assert skill_meta_text(skill) == "Skill write tests quickly"


def test_skill_query_does_not_claim_shell_parameters() -> None:
    assert skill_query_token("$HOME") is None
    assert skill_query_token("$PATH") is None
    assert skill_query_token("$1") is None
    assert skill_query_token("$-") is None
    assert skill_query_token("$_") is None
    assert skill_query_token("$home") == "$home"


@pytest.mark.parametrize(
    "text",
    (
        "@İstanbul",
        "@testЙЦУ.rs",
        "@诶",
        "@👍",
    ),
)
def test_at_query_token_accepts_codex_unicode_boundaries(text: str) -> None:
    assert skill_query_token(text) == text


def test_at_skill_completion_switches_to_dollar_sigil() -> None:
    skill = skill_spec("review")
    assert [completion.text for completion in skill_completions("@rev", (skill,))] == [
        "$review ",
    ]


def test_at_plugin_completion_preserves_at_sigil() -> None:
    plugin = SkillSpec(
        name="visualize",
        description="Create visuals",
        source="plugin",
        root=Path("visualize"),
        entry=Path("visualize/SKILL.md"),
    )
    assert [completion.text for completion in skill_completions(
        "@vis",
        (plugin,),
    )] == ["@visualize "]


def test_dollar_skill_rows_bracket_category_labels() -> None:
    items = (
        TokenMenuItem(
            display_text="Visualize",
            meta_text="Plugin Turn ideas into visuals",
            kind="skill",
        ),
        TokenMenuItem(
            display_text="APP QA",
            meta_text="Skill 通过截图测试灯具业务。",
            kind="skill",
        ),
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=items)
    )
    content = control.create_content(100, 2)

    assert "[Plugin] Turn ideas into visuals" in "".join(
        text for _style, text in content.get_line(0)
    )
    assert "[Skill] 通过截图测试灯具业务。" in "".join(
        text for _style, text in content.get_line(1)
    )


def test_token_menu_caps_long_meta_width() -> None:
    item = TokenMenuItem(
        display_text="/short",
        meta_text="x" * 200,
        kind="command",
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=(item,))
    )

    assert control.preferred_width(100) == 79


def test_token_menu_keeps_short_meta_content_width() -> None:
    item = TokenMenuItem(
        display_text="/short",
        meta_text="short help",
        kind="command",
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=(item,))
    )

    assert control.preferred_width(100) == 21


def test_token_menu_wraps_command_meta_to_terminal_width() -> None:
    item = TokenMenuItem(
        display_text="/mcp",
        meta_text="Manage MCP services",
        kind="command",
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=(item,))
    )

    content = control.create_content(24, 2)
    lines = [
        fragments_text(content.get_line(index))
        for index in range(content.line_count)
    ]

    assert content.line_count == 2
    assert "Manage MCP" in lines[0]
    assert lines[1].rstrip().endswith("services")
    assert all(get_cwidth(line) <= 24 for line in lines)
    assert control.preferred_height(24, 2, False, None) == 2

    wide_content = control.create_content(control.preferred_width(80), 1)
    assert "Manage MCP services" in fragments_text(wide_content.get_line(0))


def test_token_menu_render_snapshot_for_mixed_rows() -> None:
    items = (
        TokenMenuItem(
            display_text="short",
            meta_text="[Skill] concise help",
            kind="skill",
        ),
        TokenMenuItem(
            display_text="/model",
            meta_text="[Command] set model",
            kind="command",
        ),
        TokenMenuItem(
            display_text="very-long-name-that-truncates",
            meta_text="[Skill] " + "x" * 80,
            kind="skill",
        ),
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=items, selected=1)
    )

    content = control.create_content(60, 3)

    assert content.line_count == 3
    assert [content.get_line(index) for index in range(content.line_count)] == [
        [
            ("class:token-menu.skill", "  short                         "),
            ("class:token-menu.meta.skill", " [Skill] concise help       "),
        ],
        [
            ("class:token-menu.command.current", "  /model                        "),
            (
                "class:token-menu.meta.command.current",
                " [Command] set model        ",
            ),
        ],
        [
            (
                "class:token-menu.skill",
                "  very-long-name-that-truncates ",
            ),
            ("class:token-menu.meta.skill", " [Skill] xxxxxxxxxxxxxxx... "),
        ],
    ]


def test_token_menu_highlights_match_indices_in_rendered_rows() -> None:
    item = TokenMenuItem(
        display_text="alphabet",
        meta_text="",
        kind="skill",
        match_indices=(0, 2, 4),
    )
    control = TokenCompletionMenuControl(
        lambda: TokenMenuSnapshot(items=(item,))
    )

    fragments = control.create_content(24, 1).get_line(0)

    assert any(
        style == "class:token-menu.skill.current" and text == "  "
        for style, text in fragments
    )
    assert any("bold" in style and text == "a" for style, text in fragments)
    assert any("bold" in style and text == "p" for style, text in fragments)
    assert any("bold" in style and text == "a" for style, text in fragments[2:])


def test_light_theme_uses_deep_cyan_for_selected_token_styles() -> None:
    runtime = TuiRuntime(terminal_capabilities=TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        color_level=TerminalColorLevel.TRUECOLOR,
        theme=TerminalTheme(background=(255, 255, 255)),
    ))

    style = runtime.screen.application.style
    selected = style.get_attrs_for_style_str("class:token-menu.skill.current")
    meta_selected = style.get_attrs_for_style_str(
        "class:token-menu.meta.skill.current"
    )
    fallback = style.get_attrs_for_style_str(
        "class:completion-menu.completion.current"
    )
    fallback_meta = style.get_attrs_for_style_str(
        "class:completion-menu.meta.completion.current"
    )
    command_selected = style.get_attrs_for_style_str(
        "class:token-menu.command.current"
    )
    command_meta_selected = style.get_attrs_for_style_str(
        "class:token-menu.meta.command.current"
    )

    assert selected.color == "005F87"
    assert selected.bold
    assert meta_selected.color == "005F87"
    assert fallback.color == "005F87"
    assert fallback_meta.color == "005F87"
    assert command_selected.color == "005F87"
    assert command_selected.bgcolor == ""
    assert command_selected.bold
    assert not command_selected.dim
    assert command_meta_selected.color == "005F87"
    assert command_meta_selected.bgcolor == ""
    assert command_meta_selected.bold
    assert not command_meta_selected.dim


def test_command_menu_matches_codex_default_and_selected_styles() -> None:
    runtime = TuiRuntime()

    style = runtime.screen.application.style
    command = style.get_attrs_for_style_str("class:token-menu.command")
    command_selected = style.get_attrs_for_style_str(
        "class:token-menu.command.current"
    )
    command_meta = style.get_attrs_for_style_str(
        "class:token-menu.meta.command"
    )
    command_meta_selected = style.get_attrs_for_style_str(
        "class:token-menu.meta.command.current"
    )

    assert command.color == "default"
    assert command.bgcolor == ""
    assert not command.bold
    assert command_selected.color == "ansiblue"
    assert command_selected.bgcolor == ""
    assert command_selected.bold
    assert not command_selected.dim
    assert command_meta.color == "default"
    assert command_meta.bgcolor == ""
    assert command_meta.dim
    assert command_meta_selected.color == "ansiblue"
    assert command_meta_selected.bgcolor == ""
    assert command_meta_selected.bold
    assert not command_meta_selected.dim


@pytest.mark.anyio
async def test_skill_snapshot_marks_highlight_positions() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("generate-client"),
            skill_spec("git-commit"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$gc")
            await wait_for_completion(runtime)

            snapshot = runtime.input_model.token_menu_snapshot(
                runtime.screen.input.buffer
            )

            assert snapshot is not None
            assert [item.display_text for item in snapshot.items] == [
                "git-commit",
                "generate-client",
            ]
            assert [item.match_indices for item in snapshot.items] == [
                (0, 4),
                (0, 9),
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_popup_renders_codex_hint_line() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("generate-client"),
            skill_spec("git-commit"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$gc")
            await wait_for_completion(runtime)
            screen = await render_next_frame(runtime)

            menu_position = screen.visible_windows_to_write_positions[
                runtime.screen.completion_menu
            ]

            assert runtime.screen._completion_height() == 4
            assert "".join(
                screen.data_buffer[menu_position.ypos + 3][column].char
                for column in range(
                    menu_position.xpos,
                    menu_position.xpos + menu_position.width,
                )
            ).rstrip() == "  Press enter to insert or esc to close"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_snapshot_marks_prefix_positions() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/mo")
            await wait_for_completion(runtime)

            snapshot = runtime.input_model.token_menu_snapshot(
                runtime.screen.input.buffer
            )

            assert snapshot is not None
            assert snapshot.items[0].display_text == "/model"
            assert snapshot.items[0].match_indices == (1, 2)
        finally:
            await runtime.close()


def test_command_completion_discards_text_after_cursor() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc xxxx", cursor_position=3)

    runtime.input_model.apply_completion(
        buffer,
        Completion("/mcp", start_position=-3),
    )

    assert buffer.text == "/mcp"
    assert buffer.cursor_position == len("/mcp")


def test_completion_surface_has_no_async_footer_gap() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc", cursor_position=3)

    assert buffer.complete_state is None
    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert runtime.screen._completion_section_height() == 1
    assert (
        runtime.screen._input_stack_height()
        == runtime.screen._input_surface_height() + 1
    )
    assert not runtime.screen._footer_visible()

    buffer.document = Document("/mcp", cursor_position=4)
    runtime.input_model.refresh_completion_menu(buffer)

    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert not runtime.screen._footer_visible()
    snapshot = runtime.input_model.token_menu_snapshot(buffer)
    assert snapshot is not None
    assert snapshot.items[0].display_text == "/mcp"
    assert runtime.screen._completion_fallback_fragments() == []


def test_exact_slash_completion_keeps_native_menu_when_reapplied() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.document = Document("/mcp", cursor_position=4)
    runtime.input_model.refresh_completion_menu(buffer)

    completion = runtime.input_model._selected_menu_completion(buffer)
    assert completion is not None
    runtime.input_model._apply_menu_completion(buffer, completion)

    assert buffer.text == "/mcp"
    assert runtime.screen._native_completion_visible()
    assert runtime.screen._completion_fallback_fragments() == []


@pytest.mark.parametrize(
    ("terminal_rows", "expected_inset", "expected_footer"),
    (
        (3, 1, 0),
        (4, 1, 0),
        (5, 1, 1),
        (12, 1, 1),
    ),
)
def test_composer_budget_preserves_padding_before_optional_footer(
    terminal_rows: int,
    expected_inset: int,
    expected_footer: int,
) -> None:
    runtime = TuiRuntime()

    with patch.object(
        runtime.screen.application.output,
        "get_size",
        return_value=Size(rows=terminal_rows, columns=40),
    ):
        pane_layout = runtime.screen._bottom_pane_layout()
        layout = pane_layout.composer

    assert pane_layout.outer_top_inset_height == expected_inset
    assert layout.input_top_padding_height == 1
    assert layout.input_height == 1
    assert layout.input_bottom_padding_height == 1
    assert layout.popup_height == 0
    assert layout.footer_height == expected_footer
    if terminal_rows >= 4:
        assert pane_layout.total_height <= terminal_rows
    else:
        assert pane_layout.total_height == 4


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_rows", "expected_inset", "expected_popup"),
    (
        (3, 1, 0),
        (4, 1, 0),
        (5, 1, 1),
        (10, 1, 6),
        (12, 1, 8),
    ),
)
async def test_completion_budget_shrinks_popup_before_input_surface(
    terminal_rows: int,
    expected_inset: int,
    expected_popup: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)

                pane_layout = runtime.screen._bottom_pane_layout()
                layout = pane_layout.composer

                assert pane_layout.outer_top_inset_height == expected_inset
                assert layout.input_surface_height == 3
                assert layout.popup_height == expected_popup
                assert layout.footer_height == 0
                assert (
                    pane_layout.total_height <= terminal_rows
                    if terminal_rows >= 4
                    else pane_layout.total_height == 4
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_has_no_inline_ghost_text() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                runtime.screen.application.output,
                "get_size",
                lambda: Size(rows=24, columns=80),
            )
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.input.buffer,
                    "start_completion",
                    wraps=runtime.screen.input.buffer.start_completion,
                ) as start_completion:
                    pipe_input.send_text("/")
                    await wait_for_completion(runtime)

                start_completion.assert_not_called()
                runtime.screen.application.invalidate()
                await asyncio.sleep(0)

                buffer = runtime.screen.input.buffer
                assert buffer.text == "/"
                assert buffer.suggestion is None
                assert buffer.complete_state is not None
                assert buffer.complete_state.complete_index == 0
                assert rendered_input_line(runtime) == "› /"
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_only_opens_on_the_first_input_line() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/")
            await wait_for_completion(runtime)

            pipe_input.send_text("\x0f/")
            await wait_for_input_text(runtime, "/\n/")
            await asyncio.sleep(0)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None
            assert not runtime.screen._completion_visible()
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None
        finally:
            await runtime.close()


def test_slash_suggestion_is_not_shown_on_any_input_line() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer

    buffer.document = Document("/model\ndraft", cursor_position=6)
    assert runtime.input_model.auto_suggest.get_suggestion(
        buffer,
        buffer.document,
    ) is None

    buffer.document = Document("draft\n/model", cursor_position=12)
    assert runtime.input_model.auto_suggest.get_suggestion(
        buffer,
        buffer.document,
    ) is None


@pytest.mark.anyio
async def test_ctrl_o_closes_skill_menu_and_restores_natural_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                assert runtime.screen._completion_section_height() == 10

                pipe_input.send_text("\x0f")
                await wait_for_input_text(runtime, "$\n")
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen.input.buffer.complete_state is None
                assert not runtime.screen._completion_visible()
                assert runtime.screen._completion_section_height() == 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_forward_typing_never_leaves_a_blank_completion_frame() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        snapshots: list[tuple[str, bool]] = []

        def capture_completion_state(buffer) -> None:
            snapshots.append((
                buffer.text,
                bool(
                    runtime.screen._native_completion_visible()
                    or runtime.screen._completion_fallback_visible()
                ),
            ))

        runtime.screen.input.buffer.on_text_insert += capture_completion_state

        await runtime.open()
        try:
            pipe_input.send_text("/permissions")
            await wait_for_input_text(runtime, "/permissions")

            assert snapshots
            assert all(visible for _text, visible in snapshots)
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_canvas_frames_restore_current_layout_after_dismissal(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        frames: list[tuple[str, int, int, bool]] = []

        def capture_frame(_application) -> None:
            screen = runtime.screen.application.renderer.last_rendered_screen
            positions = screen.visible_windows_to_write_positions
            input_position = positions.get(runtime.screen.input.window)
            if input_position is None:
                return None

            input_row = (
                24 - runtime.screen._visible_height() + input_position.ypos
            )
            has_rendered_candidate = any(
                "".join(
                    cells[column].char
                    for column in sorted(cells)
                ).lstrip().startswith("/")
                for row, cells in screen.data_buffer.items()
                if row > input_position.ypos
            )
            frames.append((
                runtime.screen.input.buffer.text,
                input_row,
                runtime.screen._completion_section_height(),
                has_rendered_candidate,
            ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            runtime.screen.application.after_render += capture_frame
            await runtime.open()
            try:
                idle_screen = (
                    runtime.screen.application.renderer.last_rendered_screen
                )
                idle_position = (
                    idle_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                idle_row = (
                    24 - runtime.screen._visible_height() + idle_position.ypos
                )

                opened_at = len(frames)
                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                opened_frames = [
                    frame
                    for frame in frames[opened_at:]
                    if frame[0] == "/"
                ]
                assert idle_row == 21
                assert opened_frames
                assert all(
                    input_row == 14
                    and completion_height == 8
                    and has_rendered_candidate
                    for (
                        _text,
                        input_row,
                        completion_height,
                        has_rendered_candidate,
                    ) in opened_frames
                ), opened_frames

                dismissed_at = len(frames)
                runtime.input_model.dismiss_completion_menu(
                    runtime.screen.input.buffer
                )
                await render_next_frame(runtime)

                dismissed_frames = frames[dismissed_at:]
                assert dismissed_frames
                assert all(
                    input_row == idle_row
                    and completion_height == 0
                    and not has_rendered_candidate
                    for (
                        _text,
                        input_row,
                        completion_height,
                        has_rendered_candidate,
                    ) in dismissed_frames
                ), dismissed_frames
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("prefix", ("/", "$"))
@pytest.mark.parametrize(
    "clear_method",
    ("ctrl_u", "ctrl_w", "backspace", "delete"),
)
async def test_clearing_multiline_completion_collapses_canvas(
    prefix: str,
    clear_method: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                buffer = runtime.screen.input.buffer
                text = prefix + "\n" * 12
                buffer.document = Document(text, cursor_position=len(prefix))
                runtime.input_model.refresh_completion_menu(buffer)
                await wait_for_input_text(runtime, text)
                await wait_for_completion(runtime)
                expanded = await render_next_frame(runtime)

                assert expanded.height == 24
                assert runtime.screen._input_height() == 13
                assert runtime.screen._completion_section_height() == 8

                buffer.cursor_position = len(buffer.text)
                if clear_method == "ctrl_u":
                    pipe_input.send_text("\x15" * 13)
                elif clear_method == "ctrl_w":
                    pipe_input.send_text("\x17" * 13)
                elif clear_method == "delete":
                    runtime.screen.input.buffer.cursor_position = 0
                    pipe_input.send_text("\x1b[3~" * 13)
                else:
                    pipe_input.send_text("\x7f" * 13)
                await wait_for_input_text(runtime, "")
                collapsed = await render_next_frame(runtime)
                positions = collapsed.visible_windows_to_write_positions

                assert runtime.screen._input_height() == 1
                assert runtime.screen._completion_section_height() == 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen._visible_height() < expanded.height
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize("close_method", ("escape", "backspace"))
async def test_dismissed_slash_completion_keeps_fixed_outer_inset(
    close_method: str,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(6)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]
                before = (
                    24
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )

                assert outer_inset.height == 1
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert input_position.ypos == top_padding.ypos + 1

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                raised = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                raised_row = (
                    24
                    - runtime.screen._visible_height()
                    + raised.ypos
                )

                assert runtime.screen.canvas_spacer not in positions
                assert (
                    runtime.screen.bottom_pane_top_inset.content
                    in positions
                )

                if close_method == "escape":
                    runtime.input_model.dismiss_completion_menu(
                        runtime.screen.input.buffer
                    )
                else:
                    pipe_input.send_text("\x7f")
                    await wait_for_input_text(runtime, "")

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                dismissed = positions[runtime.screen.input.window]
                dismissed_row = (
                    24
                    - runtime.screen._visible_height()
                    + dismissed.ypos
                )

                assert raised_row < before
                assert dismissed_row == before
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert dismissed.ypos == top_padding.ypos + top_padding.height

                final_block = None
                for line_count in range(7, 15):
                    final_block = FragmentBlock(((
                        "",
                        "\n".join(
                            f"stream {index}"
                            for index in range(line_count)
                        ),
                    ),))
                    runtime.set_active_renderable(
                        final_block,
                        kind="assistant",
                    )
                    screen = await render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions
                    transcript = positions[runtime.screen.transcript_window]
                    outer_inset = positions[
                        runtime.screen.bottom_pane_top_inset.content
                    ]
                    top_padding = positions[runtime.screen.input_top_padding]
                    input_position = positions[runtime.screen.input.window]
                    assert runtime.screen._visible_height() == (
                        runtime.screen._natural_visible_height()
                    )
                    assert runtime.screen.canvas_spacer not in positions
                    assert outer_inset.ypos == (
                        transcript.ypos + transcript.height
                    )
                    assert top_padding.ypos == outer_inset.ypos + 1
                    assert input_position.ypos == (
                        top_padding.ypos + top_padding.height
                    )

                assert final_block is not None
                runtime.commit_active_renderable(final_block)
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                committed = positions[
                    runtime.screen.input.window
                ]
                committed_row = (
                    24
                    - runtime.screen._visible_height()
                    + committed.ypos
                )

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stabilize_prefix", (False, True))
async def test_stream_growth_keeps_outer_inset_after_completion_closes(
    stabilize_prefix: bool,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    FragmentBlock((("", "query"),)),
                    kind="user",
                )
                await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(6)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                if stabilize_prefix:
                    prefix = "\n".join(
                        f"stream {index}" for index in range(8)
                    )
                    runtime.commit_active_stream_prefix(
                        FragmentBlock((("", prefix),)),
                        raw_text=prefix,
                    )
                    tail = "\n".join(
                        f"stream {index}" for index in range(8, 14)
                    )
                    runtime.set_active_renderable(
                        FragmentBlock((("", tail),)),
                        kind="assistant",
                        raw_text=tail,
                        stream_continuation=True,
                    )
                else:
                    runtime.set_active_renderable(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stream {index}" for index in range(14)
                            ),
                        ),)),
                        kind="assistant",
                    )
                await render_next_frame(runtime)

                pipe_input.send_text("\x7f")
                await wait_for_input_text(runtime, "")
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1

                if stabilize_prefix:
                    tail = "\n".join(
                        f"stream {index}" for index in range(8, 15)
                    )
                    runtime.set_active_renderable(
                        FragmentBlock((("", tail),)),
                        kind="assistant",
                        raw_text=tail,
                        stream_continuation=True,
                    )
                else:
                    runtime.set_active_renderable(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stream {index}" for index in range(15)
                            ),
                        ),)),
                        kind="assistant",
                    )
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                next_input = positions[runtime.screen.input.window]
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert next_input.ypos == (
                    top_padding.ypos + top_padding.height
                )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_does_not_commit_active_stream() -> None:
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
                runtime.set_execution_active(True)
                await output.append_assistant_delta("\n".join(
                    f"line {index:02d}" for index in range(40)
                ))
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                await output.append_assistant_delta("\n" + "\n".join(
                    f"line {index:02d}" for index in range(40, 60)
                ))
                await render_next_frame(runtime)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == 0
                assert not runtime.document.blocks

                pipe_input.send_text("\x7f")
                await wait_for_input_text(runtime, "")
                await render_next_frame(runtime)

                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count == 0
                assert not runtime.document.blocks

                await output.prepare_external_output()
                runtime.set_execution_active(False)

                for _ in range(50):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                assert runtime.document.scrollback_line_count > 0
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_streaming_status_keeps_internal_interaction_gap() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming answer"),)),
                    kind="assistant",
                )
                runtime.screen.set_activity_renderable(
                    FragmentBlock((("", "Thinking"),))
                )

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                status = positions[runtime.screen.status_window]
                content_gap = positions[
                    runtime.screen.status_interaction_gap.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]

                assert content_gap.ypos == status.ypos + status.height
                assert top_padding.ypos == (
                    content_gap.ypos + content_gap.height
                )
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("wait_for_candidates", (False, True))
@pytest.mark.parametrize("stable_line_count", (0, 20))
async def test_streaming_slash_completion_has_outer_and_inner_insets(
    wait_for_candidates: bool,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming response"),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                if wait_for_candidates:
                    await wait_for_completion(runtime)
                else:
                    await wait_for_input_text(runtime, "/")

                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming response updated"),)),
                    kind="assistant",
                )
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert runtime.screen._completion_visible()
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert input_position.ypos == top_padding.ypos + 1
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize("surface", ("approval", "menu"))
async def test_bottom_surface_restores_streaming_input_and_focus(
    surface: str,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            surface_task = None
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(8)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                slash_screen = await render_next_frame(runtime)
                assert runtime.screen.input.window in (
                    slash_screen.visible_windows_to_write_positions
                )

                if surface == "approval":
                    surface_task = asyncio.create_task(
                        ApprovalCoordinator(runtime).request({
                            "tool": "shell_command",
                            "command": "\n".join(
                                f"echo line-{index}"
                                for index in range(30)
                            ),
                            "show_timer": False,
                        })
                    )
                    surface_active = (
                        lambda: runtime.screen.approval.state is not None
                    )
                elif surface == "menu":
                    surface_task = asyncio.create_task(runtime.select_menu(
                        MenuRequest(
                            title="Menu",
                            options=tuple(
                                MenuOption(index, f"Option {index}")
                                for index in range(20)
                            ),
                        ),
                    ))
                    surface_active = lambda: runtime.screen.menu.active
                for _ in range(20):
                    await asyncio.sleep(0)
                    if surface_active():
                        break
                assert surface_active()
                await render_next_frame(runtime)
                assert 1 <= runtime.screen._visible_height() <= 24

                if surface == "approval":
                    runtime.screen.approval.finish("accept")
                    assert await surface_task == "accept"
                elif surface == "menu":
                    runtime.screen.menu.finish("done")
                    assert await surface_task == "done"
                surface_task = None

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                assert runtime.screen.input.window in positions
                assert runtime.screen.bottom_pane.input_visible
                assert runtime.screen.input.buffer.text == "/"
                assert runtime.screen.application.layout.current_window is (
                    runtime.screen.input.window
                )
                assert "stream 7" in fragments_text(
                    runtime.document.live_fragments()
                )
                if stable_line_count:
                    assert runtime.document.scrollback_line_count > 0
            finally:
                if runtime.screen.approval.state is not None:
                    runtime.screen.approval.finish("decline")
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if surface_task is not None:
                    await surface_task
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_input_placeholder_reserves_first_cell_for_cursor() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.submissions.placeholder_text = "Write tests for @filename"

        await runtime.open()
        try:
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            screen = runtime.screen.application.renderer.last_rendered_screen
            input_position = screen.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            cursor = screen.get_cursor_position(runtime.screen.input.window)

            assert cursor.x == input_position.xpos
            assert rendered_input_line(runtime) == "›  Write tests for @filename"
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("shell_mode", "prompt"),
    (
        (False, "›"),
        (True, "!"),
    ),
)
async def test_input_prompt_is_separate_from_multiline_and_wrapped_text(
    shell_mode: bool,
    prompt: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=20),
        ):
            await runtime.open()
            try:
                runtime.input_model.set_shell_mode(shell_mode)
                runtime.screen.input.buffer.document = Document(
                    "abcdefghijklmnopqrs\nsecond",
                    cursor_position=len("abcdefghijklmnopqrs\nsecond"),
                )

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                prompt_position = positions[
                    runtime.screen.input_prompt_window
                ]
                input_position = positions[runtime.screen.input.window]
                cursor = screen.get_cursor_position(runtime.screen.input.window)

                assert prompt_position.width == 2
                assert input_position.xpos == prompt_position.xpos + 2
                assert input_position.width == 17
                assert runtime.screen.input.window.get_line_prefix is None
                assert rendered_input_lines(runtime) == [
                    f"{prompt} abcdefghijklmnopq",
                    "  rs",
                    "  second",
                ]
                assert cursor.x - input_position.xpos == len("second")
                assert cursor.y - input_position.ypos == 2
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_input_prompt_stays_single_when_textarea_scrolls() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=8, columns=20),
        ):
            await runtime.open()
            try:
                text = "\n".join(f"line {index}" for index in range(12))
                runtime.screen.input.buffer.document = Document(
                    text,
                    cursor_position=len(text),
                )

                await render_next_frame(runtime)
                lines = rendered_input_lines(runtime)

                assert len(lines) < len(text.splitlines())
                assert lines[0].startswith("› ")
                assert lines[-1] == "  line 11"
                assert all(line.startswith("  ") for line in lines[1:])
                assert sum(line.count("›") for line in lines) == 1
                assert not any(line.startswith((". ", "! ")) for line in lines)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_model_command_does_not_show_inline_hint() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model ")
            await wait_for_input_text(runtime, "/model ")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.suggestion is None
            assert rendered_input_line(runtime) == "› /model"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sk")
            await wait_for_completion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            bottom_position = screen.visible_windows_to_write_positions[
                runtime.screen.input_bottom_padding
            ]
            menu_position = screen.visible_windows_to_write_positions[menu_window]

            assert input_line == "› /sk"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.xpos == 0
            assert "class:token-menu.command.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
            meta_column = input_line.index("/") + len("/skills") + 2
            assert "class:token-menu.meta.command.current" in (
                screen.data_buffer[menu_position.ypos][meta_column].style
            )
            assert menu_position.ypos == bottom_position.ypos + 1
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/new", "/mcp"])
async def test_exact_slash_completion_uses_same_menu_as_prefix_command(
    command: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(command)
            await wait_for_input_text(runtime, command)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            menu_position = screen.visible_windows_to_write_positions[
                menu_window
            ]

            assert input_line == f"› {command}"
            assert completion_line.lstrip().startswith(command)
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.xpos == 0
            assert "class:token-menu.command.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
            meta_column = input_line.index("/") + len(command) + 2
            assert "class:token-menu.meta.command.current" in (
                screen.data_buffer[menu_position.ypos][meta_column].style
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "/f"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.display_text == "/fork"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_tab_completes_selected_slash_command_without_submitting() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)
            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "/fork")

            assert runtime.submissions.message_queue.empty()
            assert runtime.screen._native_completion_visible()
            snapshot = runtime.input_model.token_menu_snapshot(
                runtime.screen.input.buffer,
            )
            assert snapshot is not None
            assert snapshot.items[0].display_text == "/fork"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_tab_dispatches_selected_skills_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sk")
            await wait_for_completion(runtime)
            pipe_input.send_text("\t")

            submission = await asyncio.wait_for(
                runtime.submissions.message_queue.get(),
                timeout=1,
            )
            assert submission.value == "/skills"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_key_completes_selected_slash_command_without_submitting() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/m")
            await wait_for_completion(runtime)
            pipe_input.send_text("/")
            await wait_for_input_text(runtime, "/model ")

            assert runtime.submissions.message_queue.empty()
            assert runtime.screen.input.buffer.suggestion is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_menu_survives_left_and_right_cursor_motion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sx")
            await wait_for_input_text(runtime, "/sx")

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 2)
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 3)
            await wait_for_no_completion(runtime)

            assert buffer.complete_state is None

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 2)
            await wait_for_completion(runtime)

            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_executes_the_default_root_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/\r")
            submission = await wait_for_submission(runtime)

            assert submission.value == "/new"
            assert runtime.screen.input.buffer.text == ""
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_opens_parameter_input_for_complete_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model\r")
            await wait_for_input_text(runtime, "/model ")

            assert runtime.submissions.message_queue.empty()
            assert not runtime.screen._completion_visible()
            assert runtime.screen.input.buffer.suggestion is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_slash_command_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/aaa")
            await wait_for_input_text(runtime, "/aaa")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None
            assert runtime.screen._completion_fallback_visible()
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", "  no matches"),
            ]
            assert rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            ).lstrip() == "no matches"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"), skill_spec("beta")))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.text == "$alpha "

            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            screen = runtime.screen.application.renderer.last_rendered_screen
            menu_window = runtime.screen.completion_menu
            menu_position = screen.visible_windows_to_write_positions[
                menu_window
            ]
            assert "class:token-menu.skill.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_sorts_empty_query_by_name() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("zeta"),
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$beta ", "$zeta "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_ctrl_p_and_ctrl_n_wrap_selection() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\x10")
            for _ in range(100):
                if buffer.complete_state.current_completion.text == "$beta ":
                    break
                await asyncio.sleep(0.001)
            assert buffer.complete_state.current_completion.text == "$beta "

            pipe_input.send_text("\x0e")
            for _ in range(100):
                if buffer.complete_state.current_completion.text == "$alpha ":
                    break
                await asyncio.sleep(0.001)
            assert buffer.complete_state.current_completion.text == "$alpha "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_accepts_non_contiguous_query_matches() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("generate-client"),
            skill_spec("git-commit"),
            skill_spec("review"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$gc")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$git-commit ", "$generate-client "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_survives_left_and_right_cursor_motion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alp")
            await wait_for_completion(runtime)

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 3)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 4)

            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_closes_when_cursor_leaves_token_left_edge() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alp")
            await wait_for_completion(runtime)

            pipe_input.send_text("\x1b[D\x1b[D\x1b[D\x1b[D")
            await wait_for_cursor_position(runtime, 0)
            await wait_for_no_completion(runtime)

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 1)
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.text
                for completion in buffer.complete_state.completions
            ] == ["$alpha ", "$alphabet "]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_keeps_all_matches_beyond_visible_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        runtime.screen.application.output.get_size = lambda: Size(
            rows=10,
            columns=100,
        )

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert len(buffer.complete_state.completions) == 12
            assert runtime.screen._completion_height() == 6
            assert runtime.screen._bottom_pane_top_inset_height() == 1
            assert runtime.screen._input_surface_height() == 3
            assert not runtime.screen.completion_menu.right_margins

            runtime.input_model._select_completion(buffer, 11)

            assert buffer.complete_state.current_completion.text == "$skill-12 "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspacing_skill_query_does_not_move_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)
                screen = await render_next_frame(runtime)
                opened = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ].ypos

                query = "$skill-01"
                pipe_input.send_text(query[1:])
                await wait_for_input_text(runtime, query)

                for remaining_length in range(len(query) - 1, -1, -1):
                    pipe_input.send_text("\x7f")
                    expected = query[:remaining_length]
                    await wait_for_input_text(runtime, expected)
                    screen = await render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions

                    assert (
                        positions[runtime.screen.input.window].ypos
                        == opened
                    )
                    assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    (
        "/helix-link",
        "/helix-stop",
        "/fork",
        "/compact",
        "/mcp stop",
        "/mcp status",
    ),
)
@pytest.mark.parametrize("prior_line_count", (0, 20))
async def test_slash_command_result_uses_current_layout(
    command: str,
    prior_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                if prior_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"prior line {index}"
                                for index in range(prior_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                read_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test"),
                ))

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                pipe_input.send_text(command[1:])
                await wait_for_input_text(runtime, command)
                screen = await render_next_frame(runtime)
                command_input = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                command_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + command_input.ypos
                )

                pipe_input.send_text("\r")
                assert await read_task == command

                runtime.begin_command_layout()
                render_revision = runtime.screen.application.render_counter
                runtime.append_block(
                    FragmentBlock((("", "■ command completed"),)),
                    kind="operation",
                )
                runtime.discard_pending_submission()
                runtime.finish_command_layout()

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "command completed" in text
                )

                assert input_position.ypos - result_row == 3
                final_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )
                assert final_input_row == command_input_row
                assert all(
                    not rows.get(row)
                    for row in range(result_row + 1, input_position.ypos)
                )
                assert runtime.screen.canvas_spacer not in positions
                assert not runtime.command_layout_pending

                for _ in range(5):
                    await asyncio.sleep(0)
                assert runtime.screen.application.render_counter > (
                    render_revision
                )
                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                assert "command completed" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    (
        "/compact",
        "/fork",
        "/helix-link",
        "/helix-stop",
        "/mcp stop",
    ),
)
@pytest.mark.parametrize("terminal_rows", (12, 24))
async def test_foreground_command_uses_current_activity_layout(
    command: str,
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        release = asyncio.Event()
        started = asyncio.Event()
        running_row: list[int] = []
        foreground = TuiForegroundTasks(
            runtime,
            SimpleNamespace(await_cleanup=lambda awaitable: awaitable),
        )

        async def operation() -> str:
            await runtime.begin_operation_status(
                lambda: {"summary": "Operation running"},
            )
            started.set()
            await release.wait()
            return "ready"

        async def capture_running_frame() -> None:
            await started.wait()
            try:
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                running_row.append(
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )
            finally:
                release.set()

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=80),
        ):
            await runtime.open()
            observer = None
            try:
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                idle_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                read_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test"),
                ))
                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)
                pipe_input.send_text(command[1:])
                await wait_for_input_text(runtime, command)
                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                completion_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                pipe_input.send_text("\r")
                assert await read_task == command
                runtime.begin_command_layout()
                foreground.start(
                    "operation",
                    operation,
                    activity_kind="operation",
                    on_succeeded=lambda result: runtime.append_block(
                        text_block(f"Operation {result}"),
                        kind="operation",
                    ),
                )
                observer = asyncio.create_task(capture_running_frame())
                await foreground.wait()
                await observer

                screen = await render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                final_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + position.ypos
                )

                assert completion_row == idle_row
                assert running_row == [idle_row]
                assert final_row == idle_row
                assert not runtime.command_layout_pending
            finally:
                release.set()
                if observer is not None:
                    await observer
                await runtime.close()


@pytest.mark.anyio
async def test_stream_command_result_uses_natural_layout_after_turn() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                handled: list[str] = []

                def handle_stream_command(value: str) -> bool:
                    handled.append(value)
                    return True

                runtime.bind_stream_command_handler(handle_stream_command)
                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming answer"),)),
                    kind="assistant",
                )

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)
                pipe_input.send_text("helix-link\r")

                loop = asyncio.get_running_loop()
                deadline = loop.time() + 1.0
                while not handled and loop.time() < deadline:
                    await asyncio.sleep(0.001)
                assert handled == ["/helix-link"]

                runtime.queue_background_block(
                    FragmentBlock((("", "■ Helix MCP ready"),)),
                )
                runtime.commit_active_renderable(
                    FragmentBlock((("", "streaming answer\nfinished"),)),
                )
                runtime.set_execution_active(False)

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "Helix MCP ready" in text
                )

                assert input_position.ypos - result_row == 3
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_complete_skill_remains_selected_until_it_is_accepted() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$alpha")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$alpha"
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "$alpha ")

            assert buffer.complete_state is None
            assert runtime.submissions.message_queue.empty()
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_selected_skill_stays_dismissed_while_its_anchor_remains() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$alph")
            await wait_for_completion(runtime)
            pipe_input.send_text("\r")
            await wait_for_input_text(runtime, "$alpha ")

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None

            for key, position in (
                ("\x1b[D", 6),
                ("\x1b[D", 5),
                ("\x1b[C", 6),
                ("\x1b[C", 7),
            ):
                pipe_input.send_text(key)
                await wait_for_cursor_position(runtime, position)
                assert runtime.input_model.completion_menu_completions(
                    buffer.document
                ) is None
                assert runtime.screen._footer_visible()

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "$alpha")

            assert buffer.complete_state is None
            assert not runtime.screen._completion_visible()
            assert runtime.screen._footer_visible()

            pipe_input.send_text("\x1b[D\x1b[D")
            await wait_for_cursor_position(runtime, 4)
            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "$alha")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("\x1b[3~")
            await wait_for_input_text(runtime, "$ala")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("z")
            await wait_for_input_text(runtime, "$alza")

            assert buffer.complete_state is None
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            screen = await render_next_frame(runtime)
            assert runtime.screen.footer_window in (
                screen.visible_windows_to_write_positions
            )

            buffer.cursor_position = 1
            pipe_input.send_text("\x7f$a")
            await wait_for_input_text(runtime, "$aalza")
            await wait_for_completion(runtime)

            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is not None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_selected_skill_tracks_edits_outside_its_token() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("beta"),
        ))

        await runtime.open()
        try:
            runtime.replace_input_text("$alpha ", selected_skill=True)
            buffer = runtime.screen.input.buffer

            pipe_input.send_text("notes ")
            await wait_for_input_text(runtime, "$alpha notes ")
            buffer.cursor_position = 0
            pipe_input.send_text("ask ")
            await wait_for_input_text(runtime, "ask $alpha notes ")

            buffer.cursor_position = len("ask $alp")
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            buffer.cursor_position = len(buffer.text)
            pipe_input.send_text("$b")
            await wait_for_input_text(runtime, "ask $alpha notes $b")
            await wait_for_completion(runtime)

            completions = runtime.input_model.completion_menu_completions(
                buffer.document
            )
            assert completions is not None
            assert [completion.text for completion in completions] == [
                "$beta "
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_skill_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$zzz")
            await wait_for_input_text(runtime, "$zzz")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.complete_state is None
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", "  no matches"),
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_preserves_selected_skill_without_async_restart() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alph")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            runtime.input_model._select_completion(buffer, 1)
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "

            with patch.object(
                buffer,
                "start_completion",
                wraps=buffer.start_completion,
            ) as start_completion:
                pipe_input.send_text("\x7f\x7f")
                await wait_for_input_text(runtime, "$al")

            start_completion.assert_not_called()
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "
        finally:
            await runtime.close()


def test_dismissed_slash_menu_reopens_after_editing() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.document = Document("/aaa", cursor_position=4)

    runtime.input_model.dismiss_completion_menu(buffer)
    assert not runtime.screen._completion_visible()

    buffer.document = Document("/aaax", cursor_position=5)
    buffer.document = Document("/aaa", cursor_position=4)

    assert runtime.screen._completion_fallback_visible()


@pytest.mark.anyio
async def test_plain_query_tab_does_not_expand_text() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            buffer = runtime.screen.input.buffer
            pipe_input.send_text("h")
            await wait_for_input_text(runtime, "h")
            assert buffer.suggestion is None

            with patch.object(
                buffer,
                "start_completion",
                wraps=buffer.start_completion,
            ) as start_completion:
                pipe_input.send_text("\t")
                for _ in range(100):
                    if start_completion.called:
                        break
                    await asyncio.sleep(0.001)

            start_completion.assert_called_once()
            assert buffer.text == "h"
            assert buffer.suggestion is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_reopens_completion_menu() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sX")
            await wait_for_input_text(runtime, "/sX")

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "/s")
            await wait_for_completion(runtime)

            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_dismissed_skill_menu_stays_closed_with_cursor_motion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alp")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            runtime.input_model.dismiss_completion_menu(buffer)
            await wait_for_no_completion(runtime)

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 3)
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 4)
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("initial", "move", "erase", "target_cursor"),
    (
        pytest.param(" !", "\x1b[D", "\x7f", 1, id="backspace"),
        pytest.param(" !", "\x01", "\x1b[3~", 0, id="delete"),
        pytest.param("x !", "\x1b[D", "\x17", 2, id="ctrl-w"),
    ),
)
async def test_destructive_edit_promotes_revealed_shell_prefix(
    initial: str,
    move: str,
    erase: str,
    target_cursor: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(initial)
            await wait_for_input_text(runtime, initial)

            pipe_input.send_text(move)
            for _ in range(1000):
                if runtime.screen.input.buffer.cursor_position == target_cursor:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("cursor did not move before shell prefix")

            pipe_input.send_text(erase)
            for _ in range(1000):
                if runtime.input_model.shell_mode:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("shell prefix was not promoted")

            buffer = runtime.screen.input.buffer
            assert buffer.text == ""
            assert buffer.cursor_position == 0

            await render_next_frame(runtime)
            assert rendered_input_line(runtime) == "!"
            assert runtime.screen.input.window.get_line_prefix is None
            assert runtime.screen._input_prompt_fragments() == [
                ("class:shell-escape", "!")
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_shell_prefix_promotion_undo_restores_plain_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(" !")
            await wait_for_input_text(runtime, " !")
            pipe_input.send_text("\x1b[D\x7f")

            for _ in range(1000):
                if runtime.input_model.shell_mode:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("shell prefix was not promoted")

            pipe_input.send_text("\x1a")
            await wait_for_input_text(runtime, " !")

            assert not runtime.input_model.shell_mode
            assert runtime.screen.input.buffer.cursor_position == 1

            await render_next_frame(runtime)
            assert rendered_input_line(runtime) == "›  !"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_control_up_restores_structured_shell_history() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            read = asyncio.create_task(
                runtime.read_message(PromptContext(model="test"))
            )
            pipe_input.send_text("!adb devices\r")

            assert await asyncio.wait_for(read, timeout=1.0) == "! adb devices"
            await wait_for_input_text(runtime, "")

            pipe_input.send_text("\x1b[1;5A")
            await wait_for_input_text(runtime, "adb devices")

            assert runtime.input_model.shell_mode
            assert runtime.screen.input.buffer.cursor_position == len(
                "adb devices"
            )

            await render_next_frame(runtime)
            assert rendered_input_line(runtime) == "! adb devices"

            pipe_input.send_text("\x1b[1;5B")
            await wait_for_input_text(runtime, "")

            assert not runtime.input_model.shell_mode
        finally:
            await runtime.close()
