# -*- coding: utf-8 -*-

"""验证终端工具展示的格式、宽度与内容归一化。

这些快照共享同一渲染语法矩阵，维持整体可保证不同工具输出可直接对照。
"""

import difflib
import pytest
from prompt_toolkit.utils import get_cwidth
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
from frontends.terminal.mcp_status import (
    McpStatusDetail,
    McpStatusView,
)

from frontends.terminal.mcp_status import render_mcp_status_block
from frontends.terminal.renderers.tool import (
    render_generic_tool_result_view,
    render_javascript_result_raw_text,
    render_javascript_result_transcript_view,
    render_javascript_result_view,
    render_native_tool_result_view,
    render_tool_start_view,
)
from frontends.terminal.renderers.hook import render_hook_run_view
from agent.ports.presentation import TextStyle
from agent.application.views import HookOutputView, HookRunView
from frontends.terminal.renderers.patch import (
    create_diff_render_style_context,
    render_patch_view,
)
from agent.application.views.builders.batch import (
    build_batch_completed_view,
    build_batch_start_view,
)
from frontends.terminal.renderers.dispatch import (
    render_presentation_raw_view,
    render_presentation_transcript_view,
    render_presentation_view,
)
from frontends.terminal.styles import (
    ACTION_TERMINAL_STYLE,
    ACTION_RUN_STYLE,
    ACTION_TOOL_CALLING_STYLE,
    ACTION_TOOL_INVOKED_STYLE,
    ERROR_DOT_STYLE,
    PREVIEW_STYLE,
    SUCCESS_DOT_STYLE,
    TOOL_CALLING_DOT_STYLE,
)
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)


def _span_style(block, text: str):
    return next(span.style for span in block.spans if span.text == text)


def _containing_span_style(block, text: str):
    return next(span.style for span in block.spans if text in span.text)


def _terminal_capabilities(
    color_level: TerminalColorLevel,
    *,
    background: tuple[int, int, int],
) -> TerminalCapabilities:
    return TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
        color_support=TerminalColorSupport.fixed(color_level),
        theme=TerminalTheme(background=background),
    )


def _patch_line(block, marker: str):
    lines = [[]]
    for span in block.spans:
        chunks = span.text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                lines[-1].append(type(span)(chunk, span.style, span.hyperlink))
            if index < len(chunks) - 1:
                lines.append([])
    return next(
        line for line in lines
        if (
            "".join(span.text for span in line).lstrip()[:1].isdigit()
            and f" {marker}" in "".join(span.text for span in line)
        )
    )


def _patch_line_containing(block, text: str):
    lines = [[]]
    for span in block.spans:
        chunks = span.text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                lines[-1].append(type(span)(chunk, span.style, span.hyperlink))
            if index < len(chunks) - 1:
                lines.append([])
    return next(
        line for line in lines
        if text in "".join(span.text for span in line)
    )


def _rendered_text(block) -> str:
    return "".join(span.text for span in block.spans) or block.plain_text


def _shell_display_title(block, *, preview: bool) -> str:
    text = _rendered_text(block)
    if not preview:
        return text
    title, separator, _preview = text.partition("\n  └ ")
    assert separator
    return title


def test_batch_start_transcript_keeps_full_nested_arguments() -> None:
    command = "echo " + "value-" * 40
    view = build_batch_start_view([(
        "shell_command",
        {
            "command": command,
            "metadata": {"target": "nested-value"},
        },
    )])

    display = render_presentation_view(view)[0].plain_text
    transcript = render_presentation_transcript_view(view)[0].plain_text

    assert command not in display
    assert command in transcript
    assert '"target": "nested-value"' in transcript


def test_batch_views_bound_display_and_keep_complete_transcript() -> None:
    start = build_batch_start_view(
        (
            (
                f"tool_{index}",
                {
                    **{f"argument_{item}": item for item in range(5)},
                    "tail": f"start-tail-{index}",
                },
            )
            for index in range(6)
        )
    )
    completed = build_batch_completed_view(
        (
            (
                f"tool_{index}",
                True,
                "\n".join(
                    f"result-{index}-{line}" for line in range(8)
                ),
            )
            for index in range(6)
        )
    )

    start_display = render_presentation_view(start)[0].plain_text
    start_transcript = render_presentation_transcript_view(start)[0].plain_text
    completed_display = render_presentation_view(completed)[0].plain_text
    completed_transcript = render_presentation_transcript_view(
        completed
    )[0].plain_text

    assert "tool_3" in start_display
    assert "tool_4" not in start_display
    assert "… +2 tools" in start_display
    assert "… +3 args" in start_display
    assert "start-tail-5" in start_transcript

    assert "result-0-4" in completed_display
    assert "result-0-5" not in completed_display
    assert "… +3 lines" in completed_display
    assert "tool_4" not in completed_display
    assert "… +2 tools" in completed_display
    assert "result-5-7" in completed_transcript


@pytest.mark.parametrize("terminal_width", (20, 40))
def test_generic_tool_result_aligns_width_and_skips_empty_branch(
    terminal_width: int,
) -> None:
    view = build_generic_tool_result_view(
        "remote_tool_with_a_long_name",
        "\n   \nfirst value " + ("界🙂" * 40) + "\n\nsecond value",
        ok=True,
    )

    block = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )[0]
    display = _rendered_text(block)

    assert all(
        get_cwidth(line) <= terminal_width
        for line in display.splitlines()
    )
    assert display.splitlines()[1].startswith("  └ first value")
    assert all(
        line.strip() not in {"└", "├", "│"}
        for line in display.splitlines()
    )
    assert "second value" in render_presentation_transcript_view(
        view
    )[0].plain_text


@pytest.mark.parametrize("terminal_width", (20, 40))
def test_batch_views_clip_nested_rows_and_skip_outer_blank_lines(
    terminal_width: int,
) -> None:
    views = (
        build_batch_start_view(((
            "remote_tool_with_a_long_name",
            {"argument": "value " + ("界🙂" * 40)},
        ),)),
        build_batch_completed_view(((
            "remote_tool_with_a_long_name",
            True,
            "\n   \nfirst result " + ("界🙂" * 40) + "\n\nsecond result",
        ),)),
    )

    for view in views:
        display = _rendered_text(render_presentation_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
        )[0])
        assert all(
            get_cwidth(line) <= terminal_width
            for line in display.splitlines()
        )
        assert all(
            line.strip() not in {"└", "├", "│"}
            for line in display.splitlines()
        )

    completed = _rendered_text(render_presentation_view(
        views[1],
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )[0])
    assert "  └ first result" in completed


def test_tool_start_uses_calling_copy_and_pending_colors() -> None:
    block = render_tool_start_view(build_tool_start_view(
        "remote_tool",
        {"query": "status"},
    ))

    assert block.plain_text == "• Function Calling remote_tool"
    assert _span_style(block, "•") == TOOL_CALLING_DOT_STYLE
    assert _span_style(block, "Function Calling") == ACTION_TOOL_CALLING_STYLE
    assert _span_style(block, "Function Calling").bold


def test_write_stdin_has_no_start_display() -> None:
    view = build_tool_start_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": "\n"},
    )

    assert render_tool_start_view(view).plain_text == ""
    assert render_presentation_transcript_view(view)[0].plain_text == ""
    assert render_presentation_raw_view(view) == ("",)


def test_write_stdin_uses_codex_wait_and_interaction_titles() -> None:
    wait = build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q",
            "status": "exited",
            "output_lines": ["poll output"],
        },
    )
    interaction = build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": "y\n"},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q",
            "status": "running",
            "output_lines": ["y"],
        },
    )

    assert render_native_tool_result_view(wait)[0].plain_text == (
        "• Waited for background terminal · "
        "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q"
    )
    assert render_native_tool_result_view(interaction)[0].plain_text == (
        "↳ Interacted with background terminal · "
        "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q\n"
        "  └ y"
    )
    assert _span_style(
        render_native_tool_result_view(wait)[0],
        "Waited for background terminal",
    ) == ACTION_TERMINAL_STYLE
    assert _span_style(
        render_native_tool_result_view(wait)[0],
        "•",
    ) == ACTION_TERMINAL_STYLE
    assert _span_style(
        render_native_tool_result_view(interaction)[0],
        "Interacted with background terminal",
    ) == ACTION_TERMINAL_STYLE
    assert _containing_span_style(
        render_native_tool_result_view(wait)[0],
        "python -m pytest",
    ) == PREVIEW_STYLE
    assert not ACTION_TERMINAL_STYLE.dim
    assert ACTION_TERMINAL_STYLE.bold
    assert PREVIEW_STYLE.dim
    assert _span_style(
        render_native_tool_result_view(interaction)[0],
        "y",
    ) == TextStyle()
    assert "poll output" not in render_native_tool_result_view(wait)[0].plain_text
    assert render_presentation_transcript_view(interaction)[0].plain_text.endswith(
        "  └ y"
    )
    assert render_presentation_raw_view(wait) == (
        "Waited for background terminal: "
        "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q",
    )
    assert render_presentation_raw_view(interaction) == (
        "Interacted with background terminal: "
        "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q\ny",
    )

    control_payload = build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q",
            "status": "exited",
            "control": "interrupt",
        },
    )
    assert render_native_tool_result_view(control_payload)[0].plain_text == (
        "↳ Interacted with background terminal · "
        "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q"
    )


def test_write_stdin_uses_full_title_width_budget() -> None:
    view = build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": "yes\n"},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "python -m pytest " + ("very-long-path/" * 20),
            "status": "running",
            "output_lines": ["yes"],
        },
    )

    block = render_presentation_view(
        view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0]
    display = _rendered_text(block)

    assert display.startswith("↳ Interacted")
    assert "\n  └ yes" in display
    assert all(get_cwidth(line) <= 20 for line in display.splitlines())


def test_native_shell_start_and_result_use_running_then_ran_titles() -> None:
    arguments = {"command": "echo ready"}
    start = render_tool_start_view(build_tool_start_view(
        "shell_command",
        arguments,
        call_id="shell-call",
    ))
    result = render_native_tool_result_view(build_native_tool_result_view(
        "shell_command",
        arguments,
        ok=True,
        data={"command": "echo ready", "output_lines": ["ready"]},
        call_id="shell-call",
    ))[0]

    assert start.plain_text == "• Running echo ready"
    assert "".join(span.text for span in start.spans) == "• Running echo ready"
    assert _span_style(start, "Running") == ACTION_RUN_STYLE
    assert result.plain_text.startswith("• Ran echo ready\n")


def test_review_command_uses_native_git_trace_titles() -> None:
    arguments = {"command": "git status --short"}
    start = render_tool_start_view(build_tool_start_view(
        "exec_command",
        arguments,
        call_id="review-read-call",
    ))
    result = render_native_tool_result_view(build_native_tool_result_view(
        "exec_command",
        arguments,
        ok=True,
        data={
            "command": "git status --short",
            "output_lines": [" M source.py"],
            "exit_code": 0,
        },
        call_id="review-read-call",
    ))[0]

    assert start.plain_text == "• Running git status --short"
    assert result.plain_text.startswith("• Ran git status --short\n")


def test_shell_result_aligns_following_output_lines_under_preview() -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "adb devices"},
        ok=True,
        data={
            "command": "adb devices",
            "output_lines": [
                "* daemon not running; starting now at tcp:5037",
                "* daemon started successfully",
                "List of devices attached",
            ],
        },
    )

    assert render_native_tool_result_view(view)[0].plain_text == (
        "• Ran adb devices\n"
        "  └ * daemon not running; starting now at tcp:5037\n"
        "    * daemon started successfully\n"
        "    List of devices attached"
    )


def test_shell_result_preview_keeps_head_and_tail_lines() -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "seq 1 8"},
        ok=True,
        data={
            "command": "seq 1 8",
            "output_lines": [f"output line {index}" for index in range(8)],
        },
    )

    display = "".join(
        span.text
        for span in render_native_tool_result_view(view)[0].spans
    )

    assert "output line 0" in display
    assert "output line 1" in display
    assert "… +4 lines" in display
    assert "output line 6" in display
    assert "output line 7" in display
    assert "output line 2" not in display


def test_hook_result_uses_shell_aligned_tree_continuations() -> None:
    block = render_hook_run_view(HookRunView(
        id="hook-1",
        hook_key="project:hook",
        event="PreToolUse",
        phase="completed",
        status="completed",
        duration_ms=25,
        entries=(HookOutputView("context", "first line\nsecond line"),),
    ))

    assert block.plain_text == (
        "• Ran PreToolUse hook\n"
        "  └ completed · 25ms\n"
        "    hook context: first line\n"
        "    second line"
    )


def test_hook_result_wraps_all_multiline_fields_with_hanging_indent() -> None:
    view = HookRunView(
        id="hook-width",
        hook_key="project:hook",
        event="PreToolUse",
        phase="completed",
        status="failed",
        status_message="long status " * 8,
        duration_ms=25,
        entries=(HookOutputView("error", "long error " * 12),),
    )

    display = _rendered_text(render_presentation_view(
        view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0])
    transcript = render_presentation_transcript_view(view)[0].plain_text

    assert all(get_cwidth(line) <= 20 for line in display.splitlines())
    assert all(
        index == 0 or line.startswith("  ")
        for index, line in enumerate(display.splitlines())
    )
    assert "long status " * 8 in transcript
    assert "long error " * 12 in transcript


def test_mcp_status_wraps_tree_details_under_their_connectors() -> None:
    block = render_mcp_status_block(
        McpStatusView(
            summary="External MCP failed with a long summary",
            level="failed",
            done=True,
            details=(
                McpStatusDetail("  ├ first: " + ("detail " * 8), "failed"),
                McpStatusDetail("  └ second: " + ("detail " * 8), "failed"),
            ),
        ),
        terminal_width=20,
        measure_width=get_cwidth,
    )
    lines = _rendered_text(block).splitlines()

    assert all(get_cwidth(line) <= 20 for line in lines)
    assert any(line.startswith("  │ ") for line in lines)
    second_index = next(
        index for index, line in enumerate(lines)
        if line.startswith("  └ second:")
    )
    assert lines[second_index + 1].startswith("    ")


def _patch_delta(*changes):
    normalized_changes = []
    for change in changes:
        normalized = dict(change)
        normalized.setdefault("hunks", _canonical_hunks(normalized))
        normalized_changes.append(normalized)
    return {
        "files": [
            {
                "path": change["path"],
                "source_path": change.get("source_path"),
                "action": change["action"],
                "added_lines": 0,
                "removed_lines": 0,
            }
            for change in normalized_changes
        ],
        "delta": {"exact": True, "changes": normalized_changes},
    }


def _canonical_hunks(change):
    """构造测试用的新协议 canonical hunk。"""
    action = change["action"]
    old_lines = str(change.get("old_content") or "").splitlines()
    new_lines = str(change.get("new_content") or "").splitlines()
    if action == "create":
        lines = [
            {"kind": "add", "text": text, "old_line": None, "new_line": index}
            for index, text in enumerate(new_lines, start=1)
        ]
        return [{"lines": lines}] if lines else []
    if action == "delete":
        lines = [
            {"kind": "remove", "text": text, "old_line": index, "new_line": None}
            for index, text in enumerate(old_lines, start=1)
        ]
        return [{"lines": lines}] if lines else []

    hunks = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for group in matcher.get_grouped_opcodes(n=3):
        lines = []
        for tag, old_start, old_end, new_start, new_end in group:
            if tag == "equal":
                lines.extend(
                    {
                        "kind": "context",
                        "text": old_lines[old_index],
                        "old_line": old_index + 1,
                        "new_line": new_index + 1,
                    }
                    for old_index, new_index in zip(
                        range(old_start, old_end),
                        range(new_start, new_end),
                        strict=True,
                    )
                )
            if tag in {"delete", "replace"}:
                lines.extend(
                    {
                        "kind": "remove",
                        "text": old_lines[index],
                        "old_line": index + 1,
                        "new_line": None,
                    }
                    for index in range(old_start, old_end)
                )
            if tag in {"insert", "replace"}:
                lines.extend(
                    {
                        "kind": "add",
                        "text": new_lines[index],
                        "old_line": None,
                        "new_line": index + 1,
                    }
                    for index in range(new_start, new_end)
                )
        if lines:
            hunks.append({"lines": lines})
    return hunks


@pytest.mark.parametrize(
    ("patch", "change", "expected"),
    (
        (
            "*** Begin Patch\n*** Add File: new_file.txt\n+alpha\n+beta\n*** End Patch",
            {
                "path": "new_file.txt",
                "action": "create",
                "old_content": None,
                "new_content": "alpha\nbeta\n",
                "source_path": None,
            },
            "• Added new_file.txt (+2 -0)\n    1 +alpha\n    2 +beta",
        ),
        (
            "*** Begin Patch\n*** Delete File: old_file.txt\n*** End Patch",
            {
                "path": "old_file.txt",
                "action": "delete",
                "old_content": "first\nsecond\nthird\n",
                "new_content": None,
                "source_path": None,
            },
            "• Deleted old_file.txt (+0 -3)\n    1 -first\n    2 -second\n    3 -third",
        ),
        (
            "*** Begin Patch\n*** Update File: example.txt\n@@\n-line two\n+line two changed\n*** End Patch",
            {
                "path": "example.txt",
                "action": "modify",
                "old_content": "line one\nline two\nline three\n",
                "new_content": "line one\nline two changed\nline three\n",
                "source_path": None,
            },
            "• Edited example.txt (+1 -1)\n    1  line one\n    2 -line two\n    2 +line two changed\n    3  line three",
        ),
        (
            "*** Begin Patch\n*** Update File: old_name.rs\n*** Move to: new_name.rs\n@@\n-B\n+B changed\n*** End Patch",
            {
                "path": "new_name.rs",
                "action": "rename",
                "old_content": "A\nB\nC\n",
                "new_content": "A\nB changed\nC\n",
                "source_path": "old_name.rs",
            },
            "• Edited old_name.rs → new_name.rs (+1 -1)\n    1  A\n    2 -B\n    2 +B changed\n    3  C",
        ),
    ),
    ids=("add", "delete", "update", "rename"),
)
def test_patch_result_matches_codex_single_file_snapshots(
    patch: str,
    change: dict,
    expected: str,
) -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data=_patch_delta(change),
        call_id="patch-call",
    )
    block = render_presentation_view(view, terminal_width=120)[0]

    assert block.plain_text == expected
    assert "@@" not in block.plain_text
    assert "*** Begin Patch" not in block.plain_text
    assert render_presentation_raw_view(view) == (patch,)


def test_patch_preview_start_is_already_a_stable_codex_cell() -> None:
    patch = "*** Begin Patch\n*** Update File: example.txt\n@@\n-line one\n+line two\n*** End Patch"
    change = {
        "path": "example.txt",
        "action": "modify",
        "old_content": "line one\n",
        "new_content": "line two\n",
        "source_path": None,
    }

    start = build_tool_start_view(
        "apply_patch",
        {"patch": patch},
        patch_preview=_patch_delta(change),
        call_id="patch-preview",
    )
    result = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data=_patch_delta(change),
        call_id="patch-preview",
    )

    assert start.phase == "proposed"
    assert render_presentation_view(start, terminal_width=120)[0].plain_text == (
        render_presentation_view(result, terminal_width=120)[0].plain_text
    )


def test_patch_start_rejects_missing_structured_preview() -> None:
    with pytest.raises(ValueError, match="requires a structured preview"):
        build_tool_start_view(
            "apply_patch",
            {"patch": "*** Begin Patch\n*** End Patch"},
            call_id="patch-without-preview",
        )


def test_patch_delta_rejects_content_only_hunk_reconstruction() -> None:
    with pytest.raises(ValueError, match="canonical hunks"):
        build_native_tool_result_view(
            "apply_patch",
            {"patch": "patch"},
            ok=True,
            data={
                "files": [{"path": "file.txt", "action": "modify"}],
                "delta": {
                    "exact": True,
                    "changes": [{
                        "path": "file.txt",
                        "action": "modify",
                        "old_content": "old\n",
                        "new_content": "new\n",
                        "source_path": None,
                    }],
                },
            },
            call_id="patch-without-hunks",
        )


def test_patch_result_sorts_multiple_files_and_uses_file_nodes() -> None:
    patch = "*** Begin Patch\n*** End Patch"
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data=_patch_delta(
            {
                "path": "b.txt",
                "action": "create",
                "old_content": None,
                "new_content": "new\n",
                "source_path": None,
            },
            {
                "path": "a.txt",
                "action": "modify",
                "old_content": "one\n",
                "new_content": "one changed\n",
                "source_path": None,
            },
        ),
        call_id="patch-multiple",
    )

    assert render_presentation_view(view, terminal_width=120)[0].plain_text == (
        "• Edited 2 files (+2 -1)\n"
        "  └ a.txt (+1 -1)\n"
        "    1 -one\n"
        "    1 +one changed\n\n"
        "  └ b.txt (+1 -0)\n"
        "    1 +new"
    )


def test_patch_result_separates_distinct_hunks_like_codex() -> None:
    old_lines = [f"line {index}" for index in range(1, 13)]
    new_lines = list(old_lines)
    new_lines[1] = "line two changed"
    new_lines[10] = "line eleven changed"
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** End Patch"},
        ok=True,
        data=_patch_delta({
            "path": "example.txt",
            "action": "modify",
            "old_content": "\n".join(old_lines) + "\n",
            "new_content": "\n".join(new_lines) + "\n",
            "source_path": None,
        }),
        call_id="patch-hunks",
    )

    block = render_presentation_view(view, terminal_width=120)[0]

    assert block.plain_text.count("\n       ⋮\n") == 1
    assert "@@" not in block.plain_text


@pytest.mark.parametrize("terminal_width", (40, 60, 80, 120))
def test_patch_long_lines_use_display_width_and_empty_continuation_gutter(
    terminal_width: int,
) -> None:
    line = "界🙂value-" * 30
    patch = "*** Begin Patch\n*** Add File: long.txt\n+value\n*** End Patch"
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data=_patch_delta({
            "path": "long.txt",
            "action": "create",
            "old_content": None,
            "new_content": f"{line}\n",
            "source_path": None,
        }),
        call_id="patch-long",
    )
    block = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )[0]
    lines = block.plain_text.splitlines()

    assert all(get_cwidth(item) <= terminal_width for item in lines)
    assert lines[1].startswith("    1 +")
    assert all(item.startswith("      ") for item in lines[2:])
    assert all("1 +" not in item for item in lines[2:])


def test_patch_failure_matches_codex_title_and_diagnostics() -> None:
    patch = "*** Begin Patch\n*** Update File: sample.py\n@@\n-old\n+new\n*** End Patch"
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=False,
        data={
            "reason": "patch_context_mismatch",
            "path": "sample.py",
            "hunk_header": "@@ -10 +10 @@",
            "target_line": 12,
            "expected_sequence": ["old"],
            "actual_sequence": ["other"],
            "ignored": {"large": "payload"},
        },
        call_id="patch-failed",
    )
    block = render_presentation_view(view, terminal_width=80)[0]

    assert block.plain_text.startswith("✘ Failed to apply patch\n")
    assert "reason: patch_context_mismatch" in block.plain_text
    assert "file: sample.py" in block.plain_text
    assert "actual: other" in block.plain_text
    assert _containing_span_style(block, "✘ ") == TextStyle(foreground="ansimagenta", bold=True)


def test_patch_degraded_context_preserves_basic_diff_colors() -> None:
    patch = "*** Begin Patch\n*** Update File: file.txt\n@@\n-old\n+new\n*** End Patch"
    start = render_presentation_view(build_tool_start_view(
        "apply_patch",
        {"patch": patch},
        patch_preview=_patch_delta({
            "path": "file.txt",
            "action": "modify",
            "old_content": "old\n",
            "new_content": "new\n",
            "source_path": None,
        }),
        call_id="patch-style",
    ))[0]
    result = render_presentation_view(build_native_tool_result_view(
        "apply_patch",
        {"patch": patch},
        ok=True,
        data=_patch_delta({
            "path": "file.txt",
            "action": "modify",
            "old_content": "old\n",
            "new_content": "new\n",
            "source_path": None,
        }),
        call_id="patch-style",
    ))[0]

    assert "• Edited file.txt" in start.plain_text
    for block in (start, result):
        assert all(span.style.background is None for span in block.spans)
        assert _containing_span_style(block, "old").foreground == "ansired"
        assert _containing_span_style(block, "new").foreground == "ansigreen"


def test_patch_dark_truecolor_uses_full_line_backgrounds() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.unknownxyz",
            "action": "modify",
            "old_content": "before\nkeep\n",
            "new_content": "after\nkeep\n",
            "source_path": None,
        }),
        call_id="patch-dark",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.TRUECOLOR,
            background=(0, 0, 0),
        ),
    )[0]

    remove_line = _patch_line(block, "-")
    add_line = _patch_line(block, "+")
    assert {span.style.background for span in remove_line} == {"#4A221D"}
    assert {span.style.background for span in add_line} == {"#213A2B"}
    assert block.line_fill_styles[1].background == "#4A221D"
    assert block.line_fill_styles[2].background == "#213A2B"
    assert block.line_fill_styles[3] is None
    assert all(
        span.style.background is None
        for span in _patch_line_containing(block, "keep")
    )


def test_patch_theme_scope_backgrounds_override_codex_fallbacks() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.unknownxyz",
            "action": "modify",
            "old_content": "before\n",
            "new_content": "after\n",
            "source_path": None,
        }),
        call_id="patch-scope",
    )
    capabilities = _terminal_capabilities(
        TerminalColorLevel.TRUECOLOR,
        background=(0, 0, 0),
    )
    context = create_diff_render_style_context(
        capabilities,
        scope_backgrounds=(
            ("markup.inserted", (12, 34, 56)),
            ("markup.deleted", (74, 34, 29)),
        ),
    )

    block = render_patch_view(
        view,
        style_context=context,
    )

    assert {span.style.background for span in _patch_line(block, "+")} == {"#0C2238"}
    assert {span.style.background for span in _patch_line(block, "-")} == {"#4A221D"}


def test_patch_light_truecolor_uses_distinct_gutter_backgrounds() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.unknownxyz",
            "action": "modify",
            "old_content": "before\n",
            "new_content": "after\n",
            "source_path": None,
        }),
        call_id="patch-light",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.TRUECOLOR,
            background=(255, 255, 255),
        ),
    )[0]

    remove_line = _patch_line(block, "-")
    add_line = _patch_line(block, "+")
    assert remove_line[1].style.foreground == "#1F2328"
    assert remove_line[1].style.background == "#FFCECB"
    assert add_line[1].style.foreground == "#1F2328"
    assert add_line[1].style.background == "#ACEEBB"
    assert remove_line[-1].style.background == "#FFEBE9"
    assert add_line[-1].style.background == "#DAFBE1"


def test_patch_ansi256_uses_codex_palette_indices() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.unknownxyz",
            "action": "modify",
            "old_content": "before\n",
            "new_content": "after\n",
            "source_path": None,
        }),
        call_id="patch-256",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.ANSI256,
            background=(255, 255, 255),
        ),
    )[0]

    remove_line = _patch_line(block, "-")
    add_line = _patch_line(block, "+")
    assert remove_line[0].style.background == "#FFD7D7"
    assert remove_line[1].style.background == "#FFAFAF"
    assert add_line[0].style.background == "#D7FFD7"
    assert add_line[1].style.background == "#AFFFAF"
    assert add_line[1].style.foreground == "#303030"


@pytest.mark.parametrize("level", (TerminalColorLevel.ANSI16, TerminalColorLevel.UNKNOWN))
def test_patch_basic_colors_use_foregrounds_without_backgrounds(level) -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.py",
            "action": "modify",
            "old_content": "before\n",
            "new_content": "after\n",
            "source_path": None,
        }),
        call_id="patch-16",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            level,
            background=(0, 0, 0),
        ),
    )[0]

    remove_line = _patch_line(block, "-")
    add_line = _patch_line(block, "+")
    assert all(span.style.background is None for span in (*remove_line, *add_line))
    assert next(span for span in remove_line if span.text.startswith("-")).style.foreground == "ansired"
    assert next(span for span in add_line if span.text.startswith("+")).style.foreground == "ansigreen"
    assert all(style is None for style in block.line_fill_styles)
    assert {
        span.style.foreground
        for span in block.spans
    } <= {None, "ansigreen", "ansired"}


def test_patch_no_color_suppresses_diff_and_syntax_colors() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.py",
            "action": "modify",
            "old_content": "def before():\n    return 1\n",
            "new_content": "def after():\n    return 2\n",
            "source_path": None,
        }),
        call_id="patch-no-color",
    )
    block = render_presentation_view(
        view,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.NONE,
            background=(255, 255, 255),
        ),
    )[0]

    assert all(
        span.style.foreground is None and span.style.background is None
        for span in block.spans
    )
    assert all(style is None for style in block.line_fill_styles)


def test_patch_highlights_each_hunk_and_dims_deleted_tokens() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.py",
            "action": "modify",
            "old_content": "def old():\n    return 1\n",
            "new_content": "def new():\n    return 2\n",
            "source_path": None,
        }),
        call_id="patch-syntax",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.TRUECOLOR,
            background=(0, 0, 0),
        ),
    )[0]

    keyword_spans = [span for span in block.spans if span.text in {"def", "return"}]
    assert keyword_spans
    assert any(span.style.dim for span in keyword_spans)
    assert any(not span.style.dim for span in keyword_spans)
    assert all(span.style.bold for span in keyword_spans)


def test_patch_highlighting_preserves_multiline_hunk_state() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "sample.py",
            "action": "modify",
            "old_content": '"""start\nold\nend"""\n',
            "new_content": '"""start\nnew\nend"""\n',
            "source_path": None,
        }),
        call_id="patch-multiline-syntax",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.TRUECOLOR,
            background=(0, 0, 0),
        ),
    )[0]

    changed = [span for span in block.spans if span.text in {"old", "new"}]
    assert len(changed) == 2
    assert all(span.style.foreground == "#A9CDBB" for span in changed)


def test_patch_rename_highlighting_uses_destination_extension() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=True,
        data=_patch_delta({
            "path": "renamed.py",
            "action": "rename",
            "old_content": "plain text\n",
            "new_content": "def renamed():\n    return True\n",
            "source_path": "original.txt",
        }),
        call_id="patch-rename-syntax",
    )
    block = render_presentation_view(
        view,
        terminal_width=80,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.TRUECOLOR,
            background=(0, 0, 0),
        ),
    )[0]

    assert _containing_span_style(block, "def").bold


def test_patch_failure_title_matches_codex_magenta() -> None:
    view = build_native_tool_result_view(
        "apply_patch",
        {"patch": "patch"},
        ok=False,
        data={"error": "failed"},
        call_id="patch-magenta",
    )

    block = render_presentation_view(
        view,
        terminal_capabilities=_terminal_capabilities(
            TerminalColorLevel.ANSI16,
            background=(0, 0, 0),
        ),
    )[0]

    assert _containing_span_style(block, "Failed to apply patch").foreground == "ansimagenta"


@pytest.mark.parametrize("builder", ("start", "result"))
def test_patch_requires_stable_call_id(builder: str) -> None:
    patch = "*** Begin Patch\n*** Add File: file.txt\n+new\n*** End Patch"

    with pytest.raises(ValueError, match="requires call_id"):
        if builder == "start":
            build_tool_start_view(
                "apply_patch",
                {"patch": patch},
                patch_preview=_patch_delta({
                    "path": "file.txt",
                    "action": "create",
                    "old_content": None,
                    "new_content": "new\n",
                    "source_path": None,
                }),
            )
        else:
            build_native_tool_result_view(
                "apply_patch",
                {"patch": patch},
                ok=True,
                data=_patch_delta({
                    "path": "file.txt",
                    "action": "create",
                    "old_content": None,
                    "new_content": "new\n",
                    "source_path": None,
                }),
            )


def test_successful_patch_requires_structured_delta() -> None:
    patch = "*** Begin Patch\n*** Add File: file.txt\n+new\n*** End Patch"

    with pytest.raises(ValueError, match="requires data.delta"):
        build_native_tool_result_view(
            "apply_patch",
            {"patch": patch},
            ok=True,
            data={"output": "Done!"},
            call_id="patch-missing-delta",
        )


def test_successful_patch_requires_result_file_contract() -> None:
    patch = "*** Begin Patch\n*** Add File: file.txt\n+new\n*** End Patch"

    with pytest.raises(ValueError, match="requires data.files"):
        build_native_tool_result_view(
            "apply_patch",
            {"patch": patch},
            ok=True,
            data={
                "delta": {
                    "exact": True,
                    "changes": [{
                        "path": "file.txt",
                        "action": "create",
                        "old_content": None,
                        "new_content": "new\n",
                        "source_path": None,
                        "hunks": [{"lines": [{
                            "kind": "add",
                            "text": "new",
                            "old_line": None,
                            "new_line": 1,
                        }]}],
                    }],
                },
            },
            call_id="patch-missing-files",
        )


def test_width_aware_short_shell_titles_keep_text_and_action_style() -> None:
    arguments = {"command": "echo ready"}
    start = render_presentation_view(
        build_tool_start_view("shell_command", arguments),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]
    ran = render_presentation_view(
        build_native_tool_result_view(
            "shell_command",
            arguments,
            ok=True,
            data={"command": "echo ready", "output_lines": ["ready"]},
        ),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]
    started = render_presentation_view(
        build_native_tool_result_view(
            "exec_command",
            arguments,
            ok=True,
            data={
                "command": "echo ready",
                "status": "running",
                "output_lines": ["pending"],
            },
        ),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]

    assert _shell_display_title(start, preview=False) == "• Running echo ready"
    assert _shell_display_title(ran, preview=True) == "• Ran echo ready"
    assert _shell_display_title(started, preview=True) == "• Running echo ready"
    assert _containing_span_style(start, "Running") == ACTION_RUN_STYLE
    assert _containing_span_style(ran, "Ran") == ACTION_RUN_STYLE
    assert _containing_span_style(started, "Running") == ACTION_RUN_STYLE


@pytest.mark.parametrize("width", (20, 40, 80, 160))
@pytest.mark.parametrize(
    "command",
    (
        "echo " + "value-" * 40,
        "echo " + "界" * 120,
        "echo " + "🙂" * 120,
        "echo " + "👨\u200d👩\u200d👧\u200d👦" * 40,
        "echo " + "e\u0301" * 240,
    ),
    ids=("ascii", "cjk", "emoji", "zwj", "combining"),
)
def test_shell_titles_use_one_shared_display_width_budget(
    width: int,
    command: str,
) -> None:
    arguments = {"command": command}
    start = render_presentation_view(
        build_tool_start_view("shell_command", arguments),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    ran = render_presentation_view(
        build_native_tool_result_view(
            "shell_command",
            arguments,
            ok=True,
            data={"command": command, "output_lines": ["done"]},
        ),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    started = render_presentation_view(
        build_native_tool_result_view(
            "exec_command",
            arguments,
            ok=True,
            data={
                "command": command,
                "status": "running",
                "output_lines": ["pending"],
            },
        ),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]

    titles = (
        _shell_display_title(start, preview=False),
        _shell_display_title(ran, preview=True),
        _shell_display_title(started, preview=True),
    )
    prefixes = ("• Running ", "• Ran ", "• Running ")
    summaries = tuple(
        title.removeprefix(prefix)
        for title, prefix in zip(titles, prefixes, strict=True)
    )

    assert all("\n" not in title and "│" not in title for title in titles)
    assert all(get_cwidth(title) <= width for title in titles)
    assert len(set(summaries)) == 1
    assert summaries[0].endswith("…")
    assert not summaries[0][:-1].endswith("\u200d")


def test_width_limited_shell_title_keeps_full_command_projections() -> None:
    command = "echo " + "value-" * 40 + "\n| jq .result"
    start_view = build_tool_start_view(
        "shell_command",
        {"command": command},
    )
    result_view = build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={"command": command, "output_lines": ["done"]},
    )

    start_display = render_presentation_view(
        start_view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0]
    result_display = render_presentation_view(
        result_view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0]

    assert command not in _rendered_text(start_display)
    assert command not in _rendered_text(result_display)
    assert command in render_presentation_transcript_view(start_view)[0].plain_text
    assert command in render_presentation_transcript_view(result_view)[0].plain_text
    assert render_presentation_raw_view(start_view) == (command,)
    assert command in render_presentation_raw_view(result_view)[0]


@pytest.mark.parametrize("width", (20, 40, 80, 160))
@pytest.mark.parametrize(
    "output_line",
    (
        "value-" * 80,
        "界" * 240,
        "👩\u200d💻" * 120,
    ),
    ids=("ascii", "cjk", "zwj"),
)
@pytest.mark.parametrize("ok", (True, False), ids=("success", "failure"))
def test_shell_preview_lines_use_terminal_display_width(
    width: int,
    output_line: str,
    ok: bool,
) -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=ok,
        data={
            "command": "printf output",
            "output_lines": [output_line],
            "exit_code": 0 if ok else 1,
        },
    )

    block = render_presentation_view(
        view,
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    display_lines = _rendered_text(block).splitlines()

    assert len(display_lines) == 2
    assert display_lines[1].startswith("  └ ")
    assert all(get_cwidth(line) <= width for line in display_lines)
    assert output_line in render_presentation_transcript_view(view)[0].plain_text
    assert output_line in render_presentation_raw_view(view)[0]


def test_tool_result_uses_invoked_copy_and_result_colors() -> None:
    success = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "complete",
        ok=True,
    ))
    failure = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "failed",
        ok=False,
    ))

    assert success.plain_text.startswith("• Function Invoked remote_tool")
    assert _span_style(success, "•") == SUCCESS_DOT_STYLE
    assert _span_style(success, "Function Invoked") == ACTION_TOOL_INVOKED_STYLE
    assert _span_style(success, "Function Invoked").bold
    assert _span_style(failure, "•") == ERROR_DOT_STYLE
    assert _span_style(failure, "Function Invoked") == ACTION_TOOL_INVOKED_STYLE


@pytest.mark.parametrize(
    ("ok", "result_text", "detail", "dot_style"),
    (
        (
            True,
            "tool=view_image source=client ok=True "
            "Loaded image: screenshots/result.png",
            "screenshots/result.png",
            SUCCESS_DOT_STYLE,
        ),
        (
            False,
            "tool=view_image source=client ok=False Image file was not found.",
            "Image file was not found.",
            ERROR_DOT_STYLE,
        ),
    ),
    ids=("success", "failure"),
)
def test_view_image_result_uses_single_viewed_block(
    ok: bool,
    result_text: str,
    detail: str,
    dot_style,
) -> None:
    view = build_generic_tool_result_view("view_image", result_text, ok=ok)
    block = render_generic_tool_result_view(view)

    assert block.plain_text == f"• Viewed\n  └ {detail}"
    assert _span_style(block, "•") == dot_style
    assert render_presentation_transcript_view(view)[0].plain_text == (
        f"• Viewed\n{detail}"
    )
    assert render_presentation_raw_view(view) == (detail,)
    assert "tool=view_image" not in block.plain_text
    assert "Loaded image:" not in block.plain_text


@pytest.mark.parametrize(
    ("view", "expected"),
    (
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "echo ready"},
                ok=True,
                data={"command": "echo ready", "output_lines": ["ready"]},
                cost_ms=0,
            ),
            "✓ • 0ms",
        ),
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "exit 1"},
                ok=False,
                data={
                    "command": "exit 1",
                    "exit_code": 1,
                    "output_lines": ["failed"],
                },
                cost_ms=800,
            ),
            "✗ (1) • 800ms",
        ),
        (
            build_native_tool_result_view(
                "exec_command",
                {"command": "sleep 3.5"},
                ok=True,
                data={"command": "sleep 3.5"},
                cost_ms=3500,
            ),
            "✓ • 3.50s",
        ),
    ),
)
def test_command_transcript_appends_codex_status_summary(
    view,
    expected: str,
) -> None:
    display = render_presentation_view(view)[0].plain_text
    transcript = render_presentation_transcript_view(view)[0]

    assert transcript.plain_text.splitlines()[-1] == expected
    assert transcript.spans[-2].text in {"✓", "✗", " (1)"}
    assert transcript.spans[-1].style.dim
    icon = next(span for span in transcript.spans if span.text in {"✓", "✗"})
    assert icon.style.bold
    assert icon.style.foreground == (
        "ansigreen" if icon.text == "✓" else "ansired"
    )
    assert "✓ •" not in display
    assert "✗ •" not in display


def test_non_command_tools_do_not_render_command_status_summary() -> None:
    generic = build_generic_tool_result_view(
        "remote_tool",
        "complete",
        ok=True,
    )
    javascript = build_native_tool_result_view(
        "js_repl",
        {"code": "1 + 1"},
        ok=True,
        data={"output": "2"},
        cost_ms=25,
    )

    assert "✓" not in render_presentation_transcript_view(generic)[0].plain_text
    assert "✓" not in render_presentation_transcript_view(
        javascript,
    )[0].plain_text


def test_command_transcript_without_duration_does_not_invent_status() -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "echo ready"},
        ok=True,
        data={"command": "echo ready", "output_lines": ["ready"]},
    )

    transcript = render_presentation_transcript_view(view)[0]

    assert transcript.plain_text.splitlines()[-1] == "ready"
    assert "✓" not in transcript.plain_text


def test_shell_result_expands_tabs_without_changing_raw_data() -> None:
    raw_line = "47031FDAQ001MK\tdevice"
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "adb devices"},
        ok=True,
        data={"command": "adb devices", "output_lines": [raw_line]},
    )

    block = render_native_tool_result_view(view)[0]
    rendered_text = "".join(span.text for span in block.spans)

    assert view.data["output_lines"] == [raw_line]
    assert "\t" not in block.plain_text
    assert "\t" not in rendered_text
    assert "47031FDAQ001MK  device" in rendered_text


def test_generic_tool_result_filters_terminal_sequences_from_preview() -> None:
    raw = (
        "47031FDAQ001MK\tdevice"
        "\x1b]52;c;SGVsbG8=\x1b\\"
        "\x1bPprivate\x1b\\"
    )
    view = build_generic_tool_result_view("device_tool", raw, ok=True)

    block = render_generic_tool_result_view(view)
    rendered_text = "".join(span.text for span in block.spans)

    assert view.text == raw
    assert "47031FDAQ001MK  device" in rendered_text
    assert "\t" not in rendered_text
    assert "\x1b" not in rendered_text
    assert "SGVsbG8=" not in rendered_text
    assert "private" not in rendered_text


def test_shell_title_filters_controls_before_shortening() -> None:
    raw_command = "adb\x1b]52;c;payload\x1b\\ devices"
    view = build_native_tool_result_view(
        "shell_command",
        {"command": raw_command},
        ok=True,
        data={"command": raw_command, "output_lines": ["done"]},
    )

    block = render_native_tool_result_view(view, terminal_width=24)[0]
    rendered_text = "".join(span.text for span in block.spans)

    assert view.arguments["command"] == raw_command
    assert "adb devices" in rendered_text
    assert "\x1b" not in rendered_text
    assert "payload" not in rendered_text


def test_js_repl_renders_source_then_result_as_two_card_states() -> None:
    source = (
        "await host.tool(\"shell_command\", {\n"
        "  command: 'Start-Process \"https://example.com\"'\n"
        "});"
    )
    start = build_tool_start_view(
        "js_repl",
        {"code": source, "timeout_ms": 30000},
        call_id="call-js",
    )
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source, "timeout_ms": 30000},
        ok=True,
        data={"output": ""},
    )

    start_block = render_tool_start_view(start)
    result_block = render_native_tool_result_view(result)[0]
    transcript = render_presentation_transcript_view(start)[0]
    completed = render_javascript_result_view(result)
    completed_transcript = render_javascript_result_transcript_view(result)

    rendered_start = "".join(span.text for span in start_block.spans)
    assert "Start-Process" in rendered_start
    assert "Running" not in rendered_start
    assert transcript.plain_text == f"• JavaScript\n{source}"
    assert result_block.plain_text.startswith("• JavaScript\n")
    assert "Ran" not in result_block.plain_text
    assert "Start-Process" not in result_block.plain_text
    assert "JavaScript cell completed." in result_block.plain_text
    assert completed.plain_text.count("• JavaScript") == 1
    assert "\n  └ JavaScript cell completed." in completed.plain_text
    assert completed_transcript.plain_text == (
        "• JavaScript\nJavaScript cell completed."
    )
    assert "await host.tool" not in completed_transcript.plain_text
    assert "Start-Process" not in completed_transcript.plain_text
    assert "JavaScript cell completed." in completed_transcript.plain_text


def test_js_repl_result_renders_explicit_json_output() -> None:
    source = "await host.tool('probe', {});"
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": '{\n  "output": "nested-ok"\n}'},
    )

    block = render_javascript_result_view(result)
    transcript = render_javascript_result_transcript_view(result)
    raw_text = render_javascript_result_raw_text(result)

    assert block.plain_text.startswith("• JavaScript\n")
    assert source not in block.plain_text
    assert '"output": "nested-ok"' in block.plain_text
    assert "JavaScript cell completed." not in block.plain_text
    assert transcript.plain_text.count("• JavaScript") == 1
    assert source not in transcript.plain_text
    assert '"output": "nested-ok"' in transcript.plain_text
    assert raw_text == '{\n  "output": "nested-ok"\n}'


@pytest.mark.parametrize("terminal_width", (20, 32, 48))
def test_js_repl_result_preview_uses_terminal_width(terminal_width: int) -> None:
    view = build_native_tool_result_view(
        "js_repl",
        {"code": "console.log('ready');"},
        ok=True,
        data={"output": "result-" + ("x" * 120)},
    )

    block = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )[0]
    rendered = "".join(span.text for span in block.spans)

    assert rendered.startswith("• JavaScript\n  └ ")
    assert "result-" in rendered
    assert all(
        get_cwidth(line) <= terminal_width
        for line in rendered.splitlines()
    )
    assert "x" * 120 not in rendered


def test_js_repl_completed_transcript_keeps_omitted_source_and_output() -> None:
    source = "\n".join(f"console.log({index});" for index in range(24))
    output = "\n".join(f"result-{index}" for index in range(12))
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": output},
    )

    start = render_tool_start_view(build_tool_start_view(
        "js_repl",
        {"code": source},
    ))
    display = render_javascript_result_view(result)
    transcript = render_javascript_result_transcript_view(result)

    assert "… +6 lines" in "".join(span.text for span in start.spans)
    assert "… +4 lines" in display.plain_text
    assert "console.log(23);" not in display.plain_text
    assert "result-11" not in display.plain_text
    assert "console.log(23);" not in transcript.plain_text
    assert "result-11" in transcript.plain_text
    assert "\n└ " not in transcript.plain_text


def test_js_repl_display_preserves_source_indent_after_tree_prefix() -> None:
    source = (
        'await host.tool("shell_command", {\n'
        '      command: \'Start-Process "https://example.com"\'\n'
        '    });'
    )
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": ""},
    )

    start = render_tool_start_view(build_tool_start_view(
        "js_repl",
        {"code": source},
    ))
    display = "".join(span.text for span in start.spans)
    transcript = render_javascript_result_transcript_view(result).plain_text

    assert "\n      command:" in display
    assert "\n    });" in display
    assert "\n        command:" not in display
    assert "\n        command:" not in transcript
    assert transcript == "• JavaScript\nJavaScript cell completed."
