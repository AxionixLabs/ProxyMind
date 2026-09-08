# -*- coding: utf-8 -*-

"""验证 slash、skill 与 plugin 补全源及菜单行投影。"""


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
from agent.application.approvals.coordinator import ApprovalCoordinator
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
from infrastructure.skills import SkillSpec
from frontends.interaction.contracts import PromptContext
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


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


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
        color_support=TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
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
    assert command_selected.color == "ansicyan"
    assert command_selected.bgcolor == ""
    assert command_selected.bold
    assert not command_selected.dim
    assert command_meta.color == "default"
    assert command_meta.bgcolor == ""
    assert command_meta.dim
    assert command_meta_selected.color == "ansicyan"
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
