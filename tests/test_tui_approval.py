# -*- coding: utf-8 -*-

import asyncio

import pytest
from prompt_toolkit.keys import Keys
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

    assert text_lines[0] == ""
    assert text_lines[1] == "Would you like to run the following command?"
    assert text_lines[command_index - 1] == ""
    assert text_lines[command_index + 1] == ""
    assert text_lines[-2] == ""
    assert text_lines[-1] == (
        "Press enter to confirm or ctrl + c to cancel"
    )
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

    assert text_lines[0] == ""
    assert text_lines[1] == "Would you like to run the"
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
        "Would you like to run"
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


def test_approval_displays_environment_and_justification_only() -> None:
    lines = tui_approval_content_lines(
        ["accept", "acceptForSession", "decline"],
        approval={
            "tool": "shell_command",
            "command": "git clone https://example.test/repo.git",
            "environment": "local",
            "justification": "需要下载官方仓库以检查源码",
            "reason": "legacy request reason must not be displayed",
            "show_timer": False,
        },
        width=80,
    )
    text_lines = _line_texts(lines)

    assert text_lines[0] == ""
    assert "Environment: local" in text_lines
    assert "Reason: 需要下载官方仓库以检查源码" in text_lines
    assert "legacy request reason" not in "\n".join(text_lines)
    assert text_lines[-2] == ""
    assert text_lines[-1] == (
        "Press enter to confirm or ctrl + c to cancel"
    )


def test_approval_does_not_render_legacy_reason_without_justification() -> None:
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": "pytest -q",
            "environment": "local",
            "reason": "legacy reason",
            "show_timer": False,
        },
        width=80,
    )

    assert not any(
        line.startswith("Reason:")
        for line in _line_texts(lines)
    )


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
        max_height=13,
    )
    text_lines = _line_texts(lines)

    assert len(lines) == 13
    command_index = text_lines.index("$ echo line-0")
    omitted_index = next(
        index for index, line in enumerate(text_lines)
        if "display lines omitted" in line
    )
    assert omitted_index > command_index
    assert text_lines[omitted_index + 1] == "  echo line-11"
    option_index = next(
        index for index, line in enumerate(text_lines)
        if line.startswith("› 1. ")
    )
    assert text_lines[option_index + 1].startswith("  2. ")
    assert text_lines[option_index + 2].startswith("  3. ")


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
    assert any(line.startswith("› 1. ") for line in text_lines)


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
    ("kind", "name", "background"),
    (
        (TerminalKind.WINDOWS_TERMINAL, "Windows Terminal", "1F1F1F"),
        (TerminalKind.APPLE_TERMINAL, "Apple Terminal", "default"),
    ),
)
def test_surface_background_depends_on_terminal_support(
    kind: TerminalKind,
    name: str,
    background: str,
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
    ).bgcolor == background
    assert style.get_attrs_for_style_str(
        "class:approval-card"
    ).bgcolor == background
    assert style.get_attrs_for_style_str(
        "class:menu-card"
    ).bgcolor == background


def test_light_menu_surface_uses_dark_cyan_selection_without_row_background() -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(
                TerminalKind.WINDOWS_TERMINAL,
                "Windows Terminal",
            ),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(255, 255, 255)),
        ),
    )

    for style_class in (
        "tui-menu.index.active",
        "tui-menu.label.active",
        "tui-menu.detail-selected",
    ):
        attrs = style.get_attrs_for_style_str(f"class:{style_class}")
        assert attrs.color == "005F87"
        assert attrs.bgcolor == ""


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
def test_incomplete_terminal_capability_keeps_non_input_surfaces_transparent(
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
    ).bgcolor == "default"
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
    model = style.get_attrs_for_style_str("class:footer.model")
    assert selected.color == "005F87"
    assert selected.bold
    assert shortcut.color == "26323C"
    assert model.color == "005F87"


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
    assert fragment_text.startswith("  \n")
    assert fragment_text.endswith("\n  ")
    assert not fragment_text.endswith("\n")
    footer_text = "".join(
        text for _, text in runtime.screen.approval.footer_fragments()
    )
    assert footer_text == (
        "  Press enter to confirm or ctrl + c to cancel"
    )
    assert runtime.screen.approval_footer_window.style == ""
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
    assert runtime.screen._active_view_layout().total_height > 14

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


@pytest.mark.anyio
async def test_approval_amendment_replaces_session_choice() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    request = {
        "tool": "shell_command",
        "command": "git clone https://example.test/repo.git",
        "show_timer": False,
        "proposed_execpolicy_amendment": {
            "id": "amendment_1",
            "command_prefix": ["git", "clone"],
            "display": "git clone",
        },
    }

    assert approval.begin(request)
    assert approval.state is not None
    assert approval.state.decisions == [
        "accept", "acceptWithExecpolicyAmendment", "decline"
    ]
    assert "commands that start with `git clone`" in "".join(
        text for _style, text in approval.fragments()
    )

    approval.finish("acceptForSession")
    assert not approval.state.future.done()
    _invoke_approval_binding(approval, ("p",))
    assert await approval.wait() == "acceptWithExecpolicyAmendment"
    await approval.dismiss()


@pytest.mark.anyio
async def test_approval_ctrl_c_returns_hidden_cancel() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval

    assert approval.begin({
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    })
    assert approval.state is not None
    assert approval.state.decisions == [
        "accept", "acceptForSession", "decline"
    ]

    _invoke_approval_binding(approval, (Keys.ControlC,))

    assert await approval.wait() == "cancel"
    await approval.dismiss()


@pytest.mark.anyio
@pytest.mark.parametrize("keys", ((Keys.Escape,), ("n",)))
async def test_approval_decline_shortcuts_do_not_cancel_turn(keys) -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval

    assert approval.begin({
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    })

    _invoke_approval_binding(approval, keys)

    assert await approval.wait() == "decline"
    await approval.dismiss()


def _invoke_approval_binding(approval, keys) -> None:
    binding = next(
        item
        for item in approval.key_bindings.bindings
        if item.keys == keys
    )
    binding.handler(None)


def _line_texts(lines) -> list[str]:
    return ["".join(text for _, text in line) for line in lines]
