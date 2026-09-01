# -*- coding: utf-8 -*-

import asyncio

import pytest
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
)
from frontends.tui.core.approval_render import (
    TUI_APPROVAL_STYLE,
    approval_command_pager_lines,
    approval_pager_title,
    tui_approval_content_lines,
)
from frontends.tui.core.runtime import TuiRuntime
from mind_app.approval.coordinator import ApprovalCoordinator
from frontends.tui.core.styles import build_tui_application_style


def test_approval_command_pager_preserves_argv_and_highlighting() -> None:
    lines = approval_command_pager_lines({
        "tool": "exec_command",
        "arguments": {
            "command": ["pwsh", "-Command", "Get-Date"],
        },
    })

    text = "".join(value for line in lines for _style, value in line)
    assert text == "pwsh -Command Get-Date"
    assert any(
        style == "class:approval-command-head"
        for line in lines
        for style, _value in line
    )


def test_patch_approval_pager_uses_patch_body() -> None:
    lines = approval_command_pager_lines({
        "tool": "apply_patch",
        "patch": "*** Begin Patch\n+new line",
    })

    assert [
        "".join(value for _style, value in line)
        for line in lines
    ] == ["*** Begin Patch", "+new line"]


def test_permissions_approval_card_uses_native_fields_and_rule_color() -> None:
    approval = {
        "kind": "request_permissions",
        "approval_id": "approval-permissions",
        "call_id": "call-permissions",
        "environment_id": "workspace-write",
        "reason": "需要读取构建目录",
        "permissions": {
            "network": {"enabled": True},
            "file_system": {
                    "entries": [{"path": "D:/workspace/out", "access": "read"}]
            },
        },
        "available_decisions": [
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
            "decline",
        ],
    }
    lines = tui_approval_content_lines(
        list(approval["available_decisions"]),
        approval=approval,
        width=120,
    )
    text = "\n".join(
        "".join(value for _style, value in line)
        for line in lines
    )
    assert "Would you like to grant these permissions?" in text
    assert "Environment: workspace-write" in text
    assert "Reason: 需要读取构建目录" in text
    assert "Permission rule:" in text
    assert any(
        style == "class:approval-permission-rule"
        for line in lines
        for style, _value in line
    )
    assert TUI_APPROVAL_STYLE.get_attrs_for_style_str(
        "class:approval-permission-value"
    ).bold


def test_patch_approval_uses_dedicated_fullscreen_title_and_preview() -> None:
    approval = {
        "tool": "apply_patch",
        "call_id": "call-patch",
        "arguments": {"patch": "*** Begin Patch"},
        "patch": "*** Begin Patch",
        "preview": {
            "delta": {
                "changes": [{
                    "path": "src/app.py",
                    "action": "modify",
                    "old_content": "old\n",
                    "new_content": "new\n",
                    "source_path": None,
                    "hunks": [{"lines": [
                        {"kind": "remove", "text": "old", "old_line": 1, "new_line": None},
                        {"kind": "add", "text": "new", "old_line": None, "new_line": 1},
                    ]}],
                }],
            },
            "files": [{"path": "src/app.py"}],
        },
    }

    assert approval_pager_title(approval) == "P A T C H"
    lines = approval_command_pager_lines(approval)
    text = ["".join(value for _style, value in line) for line in lines]
    assert text == ["• Edited src/app.py (+1 -1)", "    1 -old", "    1 +new"]
    header_styles = lines[0]
    assert header_styles[0] == ("dim", "• ")
    assert header_styles[1] == ("bold", "Edited")
    assert header_styles[2] == ("", " ")
    assert header_styles[3] == ("", "src/app.py")
    assert header_styles[6] == ("fg:ansigreen", "+1")
    assert header_styles[8] == ("fg:ansired", "-1")

    card_lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval=approval,
        width=80,
    )
    assert card_lines[1][0][0] == "class:approval-question"
    summary = next(line for line in card_lines if "Edited" in _line_texts([line])[0])
    assert summary[0] == ("class:approval-patch-action", "Edited")
    assert summary[2] == ("class:approval-patch-path", "src/app.py")
    assert not TUI_APPROVAL_STYLE.get_attrs_for_style_str(
        "class:approval-patch-path"
    ).bold

    rich_capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
        color_level=TerminalColorLevel.TRUECOLOR,
        theme=TerminalTheme(background=(31, 31, 31)),
    )
    rich_lines = approval_command_pager_lines(
        approval,
        terminal_capabilities=rich_capabilities,
    )
    assert any(
        "bg:#4A221D" in style
        for line in rich_lines
        for style, _value in line
    )
    assert any(
        "bg:#213A2B" in style
        for line in rich_lines
        for style, _value in line
    )


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
    assert lines[1][0][0] == "class:approval-question"
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


def test_approval_renders_canonical_reason_without_justification() -> None:
    lines = tui_approval_content_lines(
        ["accept", "decline"],
        approval={
            "tool": "shell_command",
            "command": "pytest -q",
            "environment": "local",
            "reason": "需要运行定向测试",
            "show_timer": False,
        },
        width=80,
    )

    assert "Reason: 需要运行定向测试" in _line_texts(lines)


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
    assert question.color == ""
    assert question.bold


@pytest.mark.parametrize("background", ((0, 0, 0), (255, 255, 255)))
def test_approval_question_uses_terminal_default_foreground(
    background: tuple[int, int, int],
) -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=background),
        ),
    )

    question = style.get_attrs_for_style_str("class:approval-question")
    assert question.color == ""
    assert question.bold


@pytest.mark.parametrize(
    ("kind", "name", "input_background", "card_background"),
    (
        (
            TerminalKind.WINDOWS_TERMINAL,
            "Windows Terminal",
            "1F1F1F",
            "1F1F1F",
        ),
        (
            TerminalKind.APPLE_TERMINAL,
            "Apple Terminal",
            "1F1F1F",
            "1F1F1F",
        ),
    ),
)
def test_surface_background_depends_on_terminal_support(
    kind: TerminalKind,
    name: str,
    input_background: str,
    card_background: str,
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
    ).bgcolor == input_background
    assert style.get_attrs_for_style_str(
        "class:approval-card"
    ).bgcolor == card_background
    assert style.get_attrs_for_style_str(
        "class:menu-card"
    ).bgcolor == card_background


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
            color_level=TerminalColorLevel.UNKNOWN,
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
    ).bgcolor == "default"
    assert style.get_attrs_for_style_str(
        "class:approval-card"
    ).bgcolor == ""


def test_ansi256_surface_uses_quantized_background() -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.ANSI256,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
    )

    assert style.get_attrs_for_style_str(
        "class:input-surface"
    ).bgcolor == "1C1C1C"


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
    assert style.get_attrs_for_style_str(
        "class:transcript.overlay.selection"
    ).color == "20262C"


@pytest.mark.parametrize(
    ("level", "selection", "selection_background"),
    (
        (TerminalColorLevel.TRUECOLOR, "5B8DEF", "1D3969"),
        (TerminalColorLevel.ANSI256, "5F87D7", "5F5F87"),
        (TerminalColorLevel.ANSI16, "ansiblue", "ansiblue"),
    ),
)
def test_dark_terminal_uses_blue_selection_palette(
    level: TerminalColorLevel,
    selection: str,
    selection_background: str,
) -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=level,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
    )

    selected = style.get_attrs_for_style_str(
        "class:approval-option-selected"
    )
    transcript = style.get_attrs_for_style_str(
        "class:transcript.overlay.selection"
    )

    assert selected.color == selection
    assert selected.bold
    assert transcript.bgcolor == selection_background


def test_shell_actions_use_blue_semantics_and_process_footer_is_dim() -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
    )

    process_footer = style.get_attrs_for_style_str(
        "class:process-status.background"
    )
    assert process_footer.dim

    shell_action = style.get_attrs_for_style_str("class:shell.title.action")
    assert shell_action.color == "5B8DEF"
    assert shell_action.bold
    assert not shell_action.dim


def test_theme_foreground_drives_separator_contrast() -> None:
    style = build_tui_application_style(
        Style.from_dict({}),
        TUI_APPROVAL_STYLE,
        Style.from_dict({}),
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(
                foreground=(200, 200, 200),
                background=(0, 0, 0),
            ),
        ),
    )

    separator = style.get_attrs_for_style_str("class:footer.separator")

    assert separator.color == "default"
    assert separator.dim


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


@pytest.mark.anyio
async def test_approval_queue_advances_fifo_without_restoring_input() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 1)

    assert approval.state is not None
    assert approval.state.presentation.context.approval_id == "first"
    assert approval.pending_count == 1
    assert runtime.screen.bottom_pane.active_surface == "approval"
    assert "1 approval waiting" in "".join(
        text for _style, text in approval.footer_fragments()
    )

    approval.finish("accept")

    assert await first == "accept"
    await _wait_for_presented_approval(approval, "second")
    assert approval.state is not None
    assert approval.state.presentation.context.approval_id == "second"
    assert approval.pending_count == 0
    assert runtime.screen.bottom_pane.active_surface == "approval"

    approval.finish("decline")

    assert await second == "decline"
    assert not approval.active
    assert runtime.screen.bottom_pane.active_surface is None


@pytest.mark.anyio
async def test_approval_ctrl_c_cancels_current_and_pending_requests() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 1)

    _invoke_approval_binding(approval, (Keys.ControlC,))

    assert await asyncio.gather(first, second) == ["cancel", "cancel"]
    assert not approval.active
    assert approval.pending_count == 0
    assert runtime.screen.bottom_pane.active_surface is None


@pytest.mark.anyio
async def test_approval_close_settles_current_and_pending_requests() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 1)

    await coordinator.close()

    assert await asyncio.gather(first, second) == ["decline", "decline"]
    assert not approval.active
    assert approval.pending_count == 0


@pytest.mark.anyio
async def test_cancelling_current_approval_advances_to_pending_request() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 1)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    await _wait_for_presented_approval(approval, "second")
    assert approval.state is not None
    assert approval.state.presentation.context.approval_id == "second"
    assert approval.pending_count == 0
    assert runtime.screen.bottom_pane.active_surface == "approval"

    approval.finish("accept")
    assert await second == "accept"


@pytest.mark.anyio
async def test_cancelling_pending_approval_keeps_current_request() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 1)

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second

    assert approval.state is not None
    assert approval.state.presentation.context.approval_id == "first"
    assert approval.pending_count == 0

    approval.finish("decline")
    assert await first == "decline"


@pytest.mark.anyio
async def test_queued_approval_is_presented_after_current_resolution() -> None:
    runtime = TuiRuntime()
    approval = runtime.screen.approval
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    await _wait_for_presented_approval(approval, "first")
    queued = asyncio.create_task(coordinator.request({
        "id": "queued",
        "tool": "shell_command",
        "command": "echo queued",
    }))
    following = asyncio.create_task(coordinator.request({
        "id": "following",
        "tool": "shell_command",
        "command": "echo following",
        "show_timer": False,
    }))
    await _wait_for_pending_count(approval, 2)
    approval.finish("accept")

    assert await first == "accept"
    await _wait_for_presented_approval(approval, "queued")
    approval.finish("decline")
    assert await queued == "decline"
    await _wait_for_presented_approval(approval, "following")
    approval.finish("decline")
    assert await following == "decline"


async def _wait_for_presented_approval(approval, approval_id: str) -> None:
    for _ in range(40):
        state = approval.state
        if state is not None and state.presentation.context.approval_id == approval_id:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"approval was not presented: {approval_id}")


async def _wait_for_pending_count(approval, expected: int) -> None:
    for _ in range(40):
        if approval.pending_count == expected:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"approval pending count did not reach {expected}")


def _invoke_approval_binding(approval, keys) -> None:
    binding = next(
        item
        for item in approval.key_bindings.bindings
        if item.keys == keys
    )
    binding.handler(None)


def _line_texts(lines) -> list[str]:
    return ["".join(text for _, text in line) for line in lines]
