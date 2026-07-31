# -*- coding: utf-8 -*-

import asyncio

import pytest
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from mind_core.design.terminal_capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
)
from mind_app.tui.core.approval_render import (
    TUI_APPROVAL_STYLE,
    tui_approval_content_lines,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.styles import build_tui_application_style


def test_approval_content_keeps_question_without_card_title() -> None:
    approval = {
        "tool": "shell_command",
        "command": "pytest -q",
        "prompt": "Approve this command?",
        "show_timer": False,
    }

    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval=approval,
    )
    text_lines = ["".join(text for _, text in line) for line in lines]
    command_index = text_lines.index("$ pytest -q")

    assert text_lines[0] == "Would you like to approve the following command?"
    assert text_lines[command_index - 1] == ""
    assert text_lines[command_index + 1] == ""
    assert "Review command" not in "\n".join(text_lines)


def test_subagent_approval_displays_trusted_source_before_question() -> None:
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": "pytest -q",
            "agent_id": "agent_review",
            "agent_type": "review",
            "show_timer": False,
        },
        width=32,
    )
    text_lines = _line_texts(lines)

    assert text_lines[0] == "Would you like to approve the"
    assert "Agent review · agent_review" in text_lines
    assert all(get_cwidth(line) <= 32 for line in text_lines)

    constrained = tui_approval_content_lines(
        ["accept", "acceptForSession", "decline"],
        approval={
            "tool": "shell_command",
            "command": "pytest -q",
            "agent_id": "agent_review",
            "agent_type": "review",
            "show_timer": False,
        },
        width=32,
        max_height=5,
    )
    assert _line_texts(constrained)[0].startswith(
        "Would you like to approve"
    )


def test_multiline_command_preserves_lines_and_prefix_alignment() -> None:
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": "echo first\necho second\necho third",
            "prompt": "Approve?",
            "show_timer": False,
        },
        width=40,
    )
    text_lines = _line_texts(lines)
    command_index = text_lines.index("$ echo first")

    assert text_lines[command_index:command_index + 3] == [
        "$ echo first",
        "  echo second",
        "  echo third",
    ]
    assert text_lines[command_index + 3] == ""


def test_long_command_wraps_within_content_width() -> None:
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": f"echo {'路' * 18}",
            "prompt": "Approve?",
            "show_timer": False,
        },
        width=16,
    )
    text_lines = _line_texts(lines)
    command_index = next(
        index for index, line in enumerate(text_lines)
        if line.startswith("$ ")
    )
    command_lines = text_lines[command_index:text_lines.index("", command_index)]

    assert len(command_lines) > 1
    assert command_lines[0].startswith("$ ")
    assert all(line.startswith(("$ ", "  ")) for line in command_lines)
    assert all(get_cwidth(line) <= 16 for line in text_lines)


def test_approval_filters_controls_before_wrapping() -> None:
    width = 24
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": "adb devices\x1b]52;c;payload\x1b\\\tid",
            "prompt": "Approve?\x1bPprivate\x1b\\",
            "show_timer": False,
        },
        width=width,
    )
    text_lines = _line_texts(lines)
    text = "\n".join(text_lines)

    assert "\x1b" not in text
    assert "payload" not in text
    assert "private" not in text
    assert all(get_cwidth(line) <= width for line in text_lines)


def test_command_truncation_keeps_head_tail_and_all_options() -> None:
    lines = tui_approval_content_lines(
        ["accept", "acceptForSession", "decline"],
        approval={
            "tool": "shell_command",
            "command": "\n".join(f"echo line-{index}" for index in range(12)),
            "prompt": "Approve?",
            "show_timer": False,
        },
        width=60,
        max_height=9,
    )
    text_lines = _line_texts(lines)

    assert len(lines) == 9
    assert text_lines[2] == "$ echo line-0"
    assert "display lines omitted" in text_lines[3]
    assert text_lines[4] == "  echo line-11"
    assert text_lines[-3].startswith("> 1. ")
    assert text_lines[-2].startswith("  2. ")
    assert text_lines[-1].startswith("  3. ")


def test_single_command_row_budget_marks_truncation() -> None:
    lines = tui_approval_content_lines(
        ["accept", "acceptForSession", "decline"],
        approval={
            "tool": "shell_command",
            "command": "\n".join(f"echo line-{index}" for index in range(12)),
            "prompt": "Approve?",
            "show_timer": False,
        },
        width=40,
        max_height=7,
    )
    text_lines = _line_texts(lines)

    assert len(lines) == 7
    assert text_lines[2].startswith("$ echo line-0")
    assert text_lines[2].endswith(" …")
    assert text_lines[-3].startswith("> 1. ")


def test_approval_layout_respects_width_and_height_budgets() -> None:
    approval = {
        "tool": "shell_command",
        "command": "\n".join(
            f"echo 第{index}行 {'x' * 24}"
            for index in range(10)
        ),
        "prompt": "Would you like to approve this command?",
        "show_timer": False,
    }

    for width in (8, 12, 20, 40):
        for height in range(5, 13):
            lines = tui_approval_content_lines(
                ["accept", "acceptForSession", "decline"],
                approval=approval,
                width=width,
                max_height=height,
            )

            assert len(lines) <= height
            assert all(
                get_cwidth(text) <= width
                for text in _line_texts(lines)
            )


def test_approval_surface_uses_no_background() -> None:
    classes = (
        "approval-card",
        "approval-question",
        "approval-context",
        "approval-option",
        "approval-option-selected",
        "approval-command",
    )

    assert all(
        TUI_APPROVAL_STYLE.get_attrs_for_style_str(
            f"class:{style_class}"
        ).bgcolor == ""
        for style_class in classes
    )
    question = TUI_APPROVAL_STYLE.get_attrs_for_style_str(
        "class:approval-question"
    )
    assert question.color == "4DE3FF"


@pytest.mark.parametrize(
    ("kind", "name"),
    (
        (TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
        (TerminalKind.APPLE_TERMINAL, "Apple Terminal"),
    ),
)
def test_truecolor_terminal_uses_background_derived_surface_color(
    kind: TerminalKind,
    name: str,
) -> None:
    empty = Style.from_dict({})
    style = build_tui_application_style(
        empty,
        TUI_APPROVAL_STYLE,
        empty,
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(
                kind,
                name,
            ),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
    )

    assert style.get_attrs_for_style_str(
        "class:input-surface"
    ).bgcolor == "1F1F1F"
    assert style.get_attrs_for_style_str(
        "class:approval-card"
    ).bgcolor == "1F1F1F"


@pytest.mark.parametrize(
    "capabilities",
    (
        TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
        TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.ANSI256,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
        TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.WEZTERM, "WezTerm"),
            color_level=TerminalColorLevel.TRUECOLOR,
        ),
    ),
)
def test_incomplete_terminal_capability_keeps_surfaces_transparent(
    capabilities,
) -> None:
    empty = Style.from_dict({})
    style = build_tui_application_style(
        empty,
        TUI_APPROVAL_STYLE,
        empty,
        capabilities=capabilities,
    )

    assert style.get_attrs_for_style_str(
        "class:input-surface"
    ).bgcolor == ""
    assert style.get_attrs_for_style_str(
        "class:approval-card"
    ).bgcolor == ""


def test_light_terminal_uses_darkened_surface_and_readable_selection() -> None:
    empty = Style.from_dict({})
    style = build_tui_application_style(
        empty,
        TUI_APPROVAL_STYLE,
        empty,
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(255, 255, 255)),
        ),
    )

    assert style.get_attrs_for_style_str(
        "class:input-surface"
    ).bgcolor == "F5F5F5"
    selected = style.get_attrs_for_style_str(
        "class:approval-option-selected"
    )
    shortcut = style.get_attrs_for_style_str("class:approval-shortcut")
    assert selected.color == "005F87"
    assert selected.bold
    assert shortcut.color == "26323C"


def test_selected_session_shortcut_uses_118_style() -> None:
    lines = tui_approval_content_lines(
        ["accept", "acceptForSession", "decline"],
        approval={
            "tool": "shell_command",
            "command": "pytest -q",
            "show_timer": False,
        },
        selected_index=1,
        width=80,
    )
    session_line = next(
        line
        for line in lines
        if "Yes, for this session" in "".join(text for _, text in line)
    )

    assert session_line[-4:] == [
        ("class:approval-option-selected", " "),
        ("class:approval-option-selected", "("),
        ("class:approval-shortcut-selected", "s"),
        ("class:approval-option-selected", ")"),
    ]
    shortcut = TUI_APPROVAL_STYLE.get_attrs_for_style_str(
        "class:approval-shortcut-selected"
    )
    assert shortcut.color == "C7F7FF"
    assert shortcut.bold


def test_unselected_shortcuts_remain_visible_on_filled_surface() -> None:
    shortcut = TUI_APPROVAL_STYLE.get_attrs_for_style_str(
        "class:approval-shortcut"
    )

    assert shortcut.color == "C4CED8"
    assert shortcut.bold
    assert not shortcut.dim


@pytest.mark.anyio
async def test_approval_fills_width_and_is_not_limited_to_fourteen_rows() -> None:
    runtime = TuiRuntime()
    command = "\n".join(f"echo line-{index}" for index in range(20))
    task = asyncio.create_task(runtime.screen.approval.request({
        "tool": "shell_command",
        "command": command,
        "show_timer": False,
    }))
    await asyncio.sleep(0)

    fragments = runtime.screen.approval.fragments()
    fragment_text = "".join(text for _, text in fragments)

    assert fragments[0] == ("class:approval-card", "  ")
    assert fragment_text.count("\n  \n") == 2
    assert not fragment_text.endswith("\n")
    assert runtime.screen.approval_window.width is None
    assert not runtime.screen.approval_window.dont_extend_width()
    assert "class:input-surface" in runtime.screen.input.window.style
    assert runtime.screen.input.window.width is None
    assert not runtime.screen.input.window.dont_extend_width()
    for padding in (
        runtime.screen.input_top_padding,
        runtime.screen.input_bottom_padding,
    ):
        assert padding.style == "class:input-surface"
        assert padding.width is None
        assert not padding.dont_extend_width()
    assert runtime.screen._approval_height() > 14

    runtime.screen.approval.finish("decline")
    await task


@pytest.mark.anyio
async def test_approval_selection_resets_between_requests() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    request = {
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    }

    assert approval.begin(request)
    approval._move(2)
    selected_fragments = approval.fragments()
    assert any(
        style == "class:approval-option-selected" and text.startswith("No,")
        for style, text in selected_fragments
    )
    approval.finish("decline")
    assert await approval.wait() == "decline"
    await approval.dismiss()

    assert approval.begin(request)
    reset_fragments = approval.fragments()
    assert any(
        style == "class:approval-option-selected" and text.startswith("Yes,")
        for style, text in reset_fragments
    )
    approval.finish("decline")
    await approval.dismiss()


def _line_texts(lines) -> list[str]:
    return ["".join(text for _, text in line) for line in lines]
