# -*- coding: utf-8 -*-

"""验证工具、审批与进程结果在 TUI 中的稳定视觉分组。

这些输出共享同一渲染语法，维持整体可防止跨工具视觉规则产生分歧。
"""


import asyncio
import difflib
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    call,
    patch,
)
import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from agent.ports.presentation import ApplicationView
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
from metadata import const
from agent.ports import (
    AssistantBuffered,
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    SourcesOutput,
)
from agent.application.views.builders.approval import build_approval_view
from agent.application.views.builders.batch import (
    build_batch_completed_view,
    build_batch_start_view,
)
from agent.application.views import (
    NativeToolResultView,
    PatchView,
    PlanItemView,
    PlanUpdateView,
    RunCompletedView,
    ToolStartView,
)
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.adapters import output as tui_output_module
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.presentation import TuiPresentationSink
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.keymap import TuiRuntimeKeymap
from frontends.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    sanitize_formatted_text,
    split_formatted_lines,
    wrap_formatted_lines,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import (
    ASSISTANT_PREFIX_CLASS,
    assistant_block,
    failure_parts,
    query_block,
)
from frontends.tui.rendering.separators import final_message_separator
from tests.frontends.tui.rendering.frame_scenarios import (
    block as _block,
    document_text as _document_text,
    render_next_frame as _render_next_frame,
    rendered_screen_text as _rendered_screen_text,
    transcript_text as _transcript_text,
    wait_for_scrollback_advance as _wait_for_scrollback_advance,
    wait_for_scrollback_settlement as _wait_for_scrollback_settlement,
)


RESPONSE_IDENTITY = ResponseIdentity("turn_test", 1, 1, 1)


OUTPUT_SURFACE_CONTEXT = OutputSurfaceContext(
    surface_id="surface_test",
    cid="cid_test",
    sid="sid_test",
    turn_id="turn_test",
    agent_id="root",
)


@pytest.mark.anyio
async def test_presentation_separates_consecutive_tool_groups() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("model output")
    await output.prepare_external_output()
    first_view = build_tool_start_view(
        "shell_command",
        {"command": "echo one"},
        call_id="one",
    )
    await presentation.emit(first_view)
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo two"},
        call_id="two",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert runtime.document.blocks[1].source is first_view
    assert runtime.document.blocks[1].raw_text == "echo one"
    assert runtime.document.active_block is None


@pytest.mark.anyio
@pytest.mark.parametrize("delta", ("after", "after\n", "\r\nafter"))
async def test_completed_work_inserts_exact_separator_newline_layout(
    delta: str,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo done"},
        ok=True,
        data={"command": "echo done", "output_lines": ["done"]},
        call_id="done",
    ))
    await output.append_assistant_delta(delta)
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "system",
        "assistant",
    ]
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=40))
    ]
    assert lines == [
        "• before",
        "",
        "• Ran echo done",
        "  └ done",
        "",
        "─" * 40,
        "",
        "• after",
    ]


@pytest.mark.anyio
async def test_view_image_does_not_insert_work_separator() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_generic_tool_result_view(
        "view_image",
        "C:/tmp/image.png",
        ok=True,
    ))
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "assistant",
    ]
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=40))
    ]
    assert lines == [
        "• Viewed",
        "  └ C:/tmp/image.png",
        "",
        "• after",
    ]


@pytest.mark.anyio
async def test_pure_assistant_turn_does_not_insert_separator() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "assistant",
    ]


@pytest.mark.anyio
async def test_completed_work_adds_finished_label_only_at_turn_tail() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output._turn_started_at = 0.0

    with patch.object(tui_output_module.time, "perf_counter", return_value=61.0):
        output.note_work_activity()
        await presentation.emit(RunCompletedView(usage={}))

    assert [item.kind for item in runtime.document.blocks] == ["system"]
    rendered = fragments_text(runtime.document.fragments(width=60))
    assert "Finished in 1m 01s" in rendered
    assert get_cwidth(rendered) == 60


@pytest.mark.anyio
async def test_completed_work_tail_separator_has_exact_newline_layout() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo done"},
        ok=True,
        data={"command": "echo done", "output_lines": ["done"]},
        call_id="done",
    ))
    await presentation.emit(RunCompletedView(usage={}))

    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=60))
    ]
    assert lines == [
        "• Ran echo done",
        "  └ done",
        "",
        "─" * 60,
    ]


def test_final_separator_uses_dim_style_and_hides_short_elapsed_label() -> None:
    short = final_message_separator(60.0)
    long = final_message_separator(61.0)

    assert fragments_text(short.fragments) == "─"
    assert fragments_text(long.fragments) == "─ Finished in 1m 01s ─"
    assert all("dim" in style for style, _text in long.fragments)


@pytest.mark.anyio
async def test_tui_shell_titles_share_one_visual_row_budget() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "echo " + "界🙂" * 80
    arguments = {"command": command}

    await presentation.emit(build_tool_start_view(
        "shell_command",
        arguments,
        call_id="running",
    ))
    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        arguments,
        ok=True,
        data={"command": command, "output_lines": ["done"]},
        call_id="ran",
    ))
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=20))
    ]
    titles = tuple(
        next(line for line in lines if line.startswith(prefix))
        for prefix in ("• Running ", "• Ran ")
    )
    summaries = tuple(
        title.removeprefix(prefix)
        for title, prefix in zip(
            titles,
            ("• Running ", "• Ran "),
            strict=True,
        )
    )

    assert all(get_cwidth(title) <= 20 for title in titles)
    assert all("│" not in title for title in titles)
    assert len(set(summaries)) == 1
    assert command in _transcript_text(runtime.document)


@pytest.mark.anyio
async def test_tui_exec_lifecycle_uses_one_codex_terminal_projection() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    session = create_tui_output_session(
        "",
        context=OUTPUT_SURFACE_CONTEXT,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    presentation = session.presentation
    command = "python -m pytest tests/frontends/tui/features/test_tui_shell.py -q"

    await presentation.emit(build_native_tool_result_view(
        "exec_command",
        {"command": command},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
        call_id="poll-1",
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
            "output_lines": ["first poll output"],
        },
        call_id="poll-1",
    ))
    activity_text = "".join(
        text for _style, text in runtime.screen.activity_block.fragments
    )
    assert activity_text.startswith(
        "• Waiting for background terminal (0s • esc to interrupt)"
    )
    assert f"\n  └ {command}" in activity_text
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
            "output_lines": ["second poll output"],
        },
        call_id="poll-2",
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": "q\n"},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
        call_id="stdin-1",
    ))

    text = _document_text(runtime.document)
    assert "Started" not in text
    assert "Wrote stdin" not in text
    assert text.count("Waited for background terminal") == 1
    assert "↳ Interacted with background terminal" in text
    assert "  └ q" in text
    assert "first poll output" not in text
    assert "second poll output" not in text
    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_flushes_before_assistant_output() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    session = create_tui_output_session(
        "",
        context=OUTPUT_SURFACE_CONTEXT,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    output = session.control
    presentation = session.presentation
    content = session.content
    command = "ping -t 8.8.8.8"

    await presentation.emit(build_native_tool_result_view(
        "exec_command",
        {"command": command},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
        call_id="poll-1",
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
        call_id="wait-poll-1",
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)

    await content.emit(AssistantTextDelta("Streaming response.", RESPONSE_IDENTITY))
    await output.prepare_external_output()

    text = _document_text(runtime.document)
    assert text.index("Waited for background terminal") < text.index(
        "Streaming response."
    )
    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_flushes_when_terminal_session_changes() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    session = create_tui_output_session(
        "",
        context=OUTPUT_SURFACE_CONTEXT,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    output = session.control
    presentation = session.presentation
    content = session.content

    for call_id, session_id, command in (
        ("poll-1", "session-1", "ping -t 8.8.8.8"),
        ("poll-2", "session-2", "python -m pytest -q"),
    ):
        await presentation.emit(build_native_tool_result_view(
            "write_stdin",
            {"session_id": session_id, "stdin": ""},
            ok=True,
            data={
                "session_id": session_id,
                "command": command,
                "status": "running",
            },
            call_id=call_id,
        ))

    text = _document_text(runtime.document)
    assert text.count("Waited for background terminal") == 1
    assert "ping -t 8.8.8.8" in text
    assert "python -m pytest -q" not in text

    await content.emit(AssistantTextDelta("Answer.", RESPONSE_IDENTITY))
    await output.prepare_external_output()

    text = _document_text(runtime.document)
    assert text.count("Waited for background terminal") == 2
    assert text.index("ping -t 8.8.8.8") < text.index("python -m pytest -q")
    assert text.index("python -m pytest -q") < text.index("Answer.")
    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_is_ignored_when_turn_is_not_running() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "ping -t 8.8.8.8",
            "status": "running",
        },
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)
    assert runtime.screen.activity_block is None

    runtime.set_execution_active(True)
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "missing-session", "stdin": ""},
        ok=False,
        data={
            "session_id": "missing-session",
            "status": "failed",
        },
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)
    assert runtime.screen.activity_block is None
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_controlled_write_stdin_does_not_enter_terminal_wait() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {
            "session_id": "session-1",
            "stdin": "",
            "control": "interrupt",
        },
        ok=True,
        data={
            "session_id": "session-1",
            "command": "ping -t 8.8.8.8",
            "status": "exited",
            "control": "interrupt",
            "output_lines": ["stopped"],
        },
    ))

    text = _document_text(runtime.document)
    assert runtime.screen.activity_block is None
    assert "Terminal · " not in text
    assert "Interacted with background terminal" in text
    assert "stopped" not in text


@pytest.mark.anyio
async def test_tui_shell_preview_keeps_each_output_on_one_visual_row() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output_lines = [
        "value-" * 40,
        "界" * 120,
        "👩\u200d💻" * 60,
    ]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=True,
        data={
            "command": "printf output",
            "output_lines": output_lines,
        },
        call_id="preview-width",
    ))

    display = fragments_text(runtime.document.fragments(width=20))
    display_lines = display.splitlines()

    assert len(display_lines) == 4
    assert all(get_cwidth(line) <= 20 for line in display_lines)
    assert display_line_count(display, width=20) == len(display_lines)
    assert all(line in _transcript_text(runtime.document) for line in output_lines)


@pytest.mark.anyio
async def test_generic_tool_result_starts_on_separate_visual_group() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_tool_start_view(
        "remote_tool",
        {"query": "one"},
        call_id="one",
    ))
    await presentation.emit(build_generic_tool_result_view(
        "remote_tool",
        "result",
        ok=True,
        call_id="one",
    ))
    await presentation.emit(build_tool_start_view(
        "remote_tool",
        {"query": "two"},
        call_id="two",
    ))

    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 2


def _oversized_patch_text() -> str:
    return "\n".join((
        "*** Begin Patch",
        "*** Update File: sample.py",
        "@@ -1 +1,31 @@",
        "-old value",
        *(f"+new value {index}" for index in range(30)),
        "+patch-final-token",
        "*** End Patch",
    ))


def _patch_hunks(old_content: str, new_content: str):
    """构造测试用的新协议 canonical hunk。"""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
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


def _patch_result_view(
    call_id: str,
    *,
    ok: bool = True,
    new_content: str = "new\n",
):
    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: sample.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )
    data = (
        {
            "files": [{
                "path": "sample.py",
                "source_path": None,
                "action": "modify",
                "added_lines": 1,
                "removed_lines": 1,
            }],
            "delta": {
                "exact": True,
                "changes": [{
                    "path": "sample.py",
                    "action": "modify",
                    "old_content": "old\n",
                    "new_content": new_content,
                    "source_path": None,
                    "hunks": _patch_hunks("old\n", new_content),
                }],
            },
        }
        if ok
        else {
            "reason": "patch_context_mismatch",
            "path": "sample.py",
            "target_line": 1,
        }
    )
    return build_native_tool_result_view(
        "apply_patch",
        {"patch": patch_text},
        ok=ok,
        data=data,
        call_id=call_id,
    )


def _patch_preview_data(*, new_content: str = "new\n"):
    """返回测试用的新协议补丁预览。"""
    return {
        "files": [{
            "path": "sample.py",
            "source_path": None,
            "action": "modify",
        }],
        "delta": {
            "exact": True,
            "changes": [{
                "path": "sample.py",
                "action": "modify",
                "old_content": "old\n",
                "new_content": new_content,
                "source_path": None,
                "hunks": _patch_hunks("old\n", new_content),
            }],
        },
    }


@pytest.mark.anyio
async def test_tui_patch_start_and_success_share_one_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-one")
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )

    await presentation.emit(preview)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert "• Edited sample.py" in _document_text(runtime.document)

    await presentation.emit(result)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].source is preview
    assert runtime.document.blocks[0].raw_text == result.raw_patch
    assert _document_text(runtime.document).count("• Edited sample.py") == 1
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_preview_is_stable_and_success_is_not_duplicated() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-preview")
    preview_data = _patch_preview_data()
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=preview_data,
        call_id="patch-preview",
    )

    await presentation.emit(preview)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert _document_text(runtime.document).count("• Edited sample.py") == 1

    await presentation.emit(result)

    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].source is preview
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_failure_appends_after_stable_preview() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-preview-failed", ok=False)
    preview_data = _patch_preview_data()
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=preview_data,
        call_id="patch-preview-failed",
    )

    await presentation.emit(preview)
    await presentation.emit(result)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.blocks[0].source is preview
    assert runtime.document.blocks[1].source is result
    assert _document_text(runtime.document).startswith("• Edited sample.py")
    assert "✘ Failed to apply patch" in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_failure_appends_without_replacing_proposed_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-failed", ok=False)
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-failed",
    )

    await presentation.emit(preview)
    await presentation.emit(result)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.active_block is None
    assert _document_text(runtime.document).startswith("• Edited sample.py")
    assert "✘ Failed to apply patch" in _document_text(runtime.document)
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_new_call_stabilizes_previous_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    first = _patch_result_view("patch-one")
    second = _patch_result_view("patch-two", new_content="second\n")
    first_preview = build_tool_start_view(
        "apply_patch",
        {"patch": first.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )
    second_preview = build_tool_start_view(
        "apply_patch",
        {"patch": second.raw_patch},
        patch_preview=_patch_preview_data(new_content="second\n"),
        call_id="patch-two",
    )

    await presentation.emit(first_preview)
    await presentation.emit(second_preview)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.active_block is None
    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-two",
    ]

    await presentation.emit(second)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-two",
    ]


@pytest.mark.anyio
async def test_tui_unmatched_patch_result_does_not_overwrite_active_call() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    first = _patch_result_view("patch-one")
    other = _patch_result_view("patch-other", new_content="other\n")
    first_preview = build_tool_start_view(
        "apply_patch",
        {"patch": first.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )

    await presentation.emit(first_preview)
    await presentation.emit(other)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-other",
    ]

    await presentation.emit(first)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-other",
    ]


@pytest.mark.anyio
async def test_tui_patch_reflows_from_structured_view_after_resize() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    long_content = "界🙂value-" * 30 + "\n"
    result = _patch_result_view("patch-resize", new_content=long_content)

    await presentation.emit(result)

    narrow = fragments_text(runtime.document.fragments(width=40))
    wide = fragments_text(runtime.document.fragments(width=120))

    assert len(narrow.splitlines()) > len(wide.splitlines())
    assert all(get_cwidth(line) <= 40 for line in narrow.splitlines())
    assert all(get_cwidth(line) <= 120 for line in wide.splitlines())
    assert runtime.document.blocks[0].source is result
    assert runtime.document.blocks[0].raw_text == result.raw_patch


@pytest.mark.anyio
async def test_tui_patch_background_fills_each_wrapped_row_after_resize() -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
        color_support=TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        theme=TerminalTheme(background=(0, 0, 0)),
    )
    runtime = TuiRuntime(terminal_capabilities=capabilities)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view(
        "patch-background-resize",
        new_content=("value-" * 30) + "\n",
    )

    await presentation.emit(result)

    for width in (40, 72):
        lines = split_formatted_lines(runtime.document.fragments(width=width))
        changed = [
            line for line in lines
            if any("bg:#213A2B" in style for style, _text in line)
        ]

        assert len(changed) > 1
        assert all(get_cwidth(fragments_text(line)) == width for line in changed)
        assert all("bg:#213A2B" in line[-1][0] for line in changed)


@pytest.mark.anyio
async def test_assistant_output_sees_stable_patch_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-active")
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-active",
    )

    await presentation.emit(preview)

    await output.prepare_external_output()

    assert runtime.document.active_block is None
    assert runtime.document.blocks[0].source is preview


@pytest.mark.anyio
async def test_tui_bounds_every_tool_block_family_and_keeps_transcript() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (32, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    patch_text = _oversized_patch_text()
    views_and_hidden_markers = (
        (build_tool_start_view(
            "remote_tool",
            {
                **{f"argument_{index}": index for index in range(8)},
                "nested": {"value": "generic-start-final-token"},
            },
        ), "generic-start-final-token"),
        (build_generic_tool_result_view(
            "remote_tool",
            "\n".join(f"generic result {index}" for index in range(12)),
            ok=True,
        ), "generic result 11"),
        (build_tool_start_view(
            "shell_command",
            {"command": "printf " + "value-" * 40 + "shell-final-token"},
        ), "shell-final-token"),
        (build_native_tool_result_view(
            "shell_command",
            {"command": "printf output"},
            ok=True,
            data={
                "command": "printf output",
                "output_lines": [
                    f"shell output {index}" for index in range(12)
                ],
            },
        ), "shell output 5"),
        (build_native_tool_result_view(
            "exec_command",
            {"command": "run background"},
            ok=True,
            data={
                "command": "run background",
                "status": "exited",
                "output_lines": [
                    f"exec output {index}" for index in range(12)
                ],
            },
        ), "exec output 5"),
        (build_native_tool_result_view(
            "apply_patch",
            {"patch": patch_text},
            ok=True,
            data={
                "files": [{
                    "path": "sample.py",
                    "source_path": None,
                    "action": "modify",
                    "added_lines": 31,
                    "removed_lines": 1,
                }],
                "delta": {
                    "exact": True,
                    "changes": [{
                            "path": "sample.py",
                            "action": "modify",
                            "old_content": "old value\n",
                            "new_content": "\n".join((
                            *(f"new value {index}" for index in range(30)),
                            "patch-final-token",
                                "",
                            )),
                            "source_path": None,
                            "hunks": _patch_hunks(
                                "old value\n",
                                "\n".join((
                                    *(f"new value {index}" for index in range(30)),
                                    "patch-final-token",
                                    "",
                                )),
                            ),
                        }],
                },
            },
            call_id="patch-bounds",
        ), "patch-final-token"),
        (build_tool_start_view(
            "js_repl",
            {"code": "\n".join(
                f"const value{index} = {index};" for index in range(30)
            )},
        ), "const value29 = 29;"),
        (build_native_tool_result_view(
            "js_repl",
            {"code": "const value = 1;"},
            ok=True,
            data={
                "output": "\n".join(
                    f"javascript output {index}" for index in range(30)
                ),
            },
        ), "javascript output 29"),
        (build_batch_start_view((
            ("remote_tool", {
                **{f"argument_{index}": index for index in range(8)},
                "nested": {"value": "batch-start-final-token"},
            }),
        )), "batch-start-final-token"),
        (build_batch_completed_view((
            (
                "remote_tool",
                True,
                "\n".join(f"batch output {index}" for index in range(12)),
            ),
        )), "batch output 11"),
    )

    for view, _marker in views_and_hidden_markers:
        await presentation.emit(view)

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)

    assert all(
        marker not in display
        for view, marker in views_and_hidden_markers
        if not isinstance(view, PatchView)
    )
    assert "patch-final-token" in display
    assert all(
        marker in transcript
        for _view, marker in views_and_hidden_markers
    )
    assert "… +" in display
    assert "to view transcript" in display


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rows", "columns"),
    ((8, 20), (12, 40), (24, 80)),
)
@pytest.mark.parametrize(
    ("view", "visible_marker", "hidden_marker", "omission_marker"),
    (
        (
            build_generic_tool_result_view(
                "remote_tool",
                "\n".join(f"generic result {index}" for index in range(20)),
                ok=True,
            ),
            "generic result 0",
            "generic result 19",
            "… +15 lines",
        ),
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "printf output"},
                ok=True,
                data={
                    "command": "printf output",
                    "output_lines": [
                        f"shell output {index}" for index in range(20)
                    ],
                },
            ),
            "shell output 19",
            "shell output 2",
            "… +16 lines",
        ),
        (
            build_tool_start_view(
                "js_repl",
                {"code": "\n".join(
                    f"const value{index} = {index};" for index in range(30)
                )},
            ),
            "const value0 =",
            "const value29 = 29;",
            "… +12 lines",
        ),
        (
            build_native_tool_result_view(
                "js_repl",
                {"code": "const value = 1;"},
                ok=True,
                data={
                    "output": "\n".join(
                        f"javascript output {index}" for index in range(30)
                    ),
                },
            ),
                "javascript outp",
            "javascript output 29",
            "… +25 lines",
        ),
        (
            build_batch_start_view(
                (
                    (
                        f"batch_tool_{index}",
                        {
                            **{
                                f"argument_{item}": item
                                for item in range(5)
                            },
                            "tail": f"batch-start-tail-{index}",
                        },
                    )
                    for index in range(6)
                )
            ),
            "batch_tool_0",
            "batch-start-tail-5",
            "… +2 tools",
        ),
        (
            build_batch_completed_view(
                (
                    (
                        f"batch_tool_{index}",
                        True,
                        "\n".join(
                            f"batch result {index}-{line}"
                            for line in range(8)
                        ),
                    )
                    for index in range(6)
                )
            ),
            "batch result",
            "batch result 5-7",
            "… +2 tools",
        ),
    ),
    ids=(
        "generic-result",
        "shell-result",
        "javascript-start",
        "javascript-result",
        "batch-start",
        "batch-result",
    ),
)
async def test_bounded_tool_blocks_keep_real_terminal_layout_stable(
    rows: int,
    columns: int,
    view,
    visible_marker: str,
    hidden_marker: str,
    omission_marker: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)
        application = TuiApplicationSink(runtime)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=rows, columns=columns),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    application.emit(ApplicationView(type="intro"))
                    runtime.append_submitted_query(
                        "exercise bounded tool layout",
                        "turn-tool-layout",
                    )
                    runtime.append_block(
                        _block("\n".join(
                            f"assistant line {index}" for index in range(24)
                        )),
                        kind="assistant",
                    )
                    runtime.set_execution_active(True)
                    runtime.screen.set_activity_renderable(_block("Thinking"))
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)

                    await presentation.emit(view)
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_settlement(runtime)
                    screen = await _render_next_frame(runtime)

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                visible = f"{printed}\n{_rendered_screen_text(screen)}"
                transcript = _transcript_text(runtime.document)
                positions = screen.visible_windows_to_write_positions
                bottom_windows = (
                    runtime.screen.input_top_padding,
                    runtime.screen.input.window,
                    runtime.screen.input_bottom_padding,
                    runtime.screen.footer_window,
                )
                bottom_positions = [
                    positions[window]
                    for window in bottom_windows
                    if window in positions
                ]
                status_position = positions[runtime.screen.status_window]

                assert f">_ {const.APP_DESC}" in printed
                assert "exercise bounded" in printed
                assert "tool layout" in printed
                assert "assistant line 0" in printed
                assert "assistant line 23" in printed
                assert visible_marker in visible
                assert hidden_marker not in visible
                assert hidden_marker in transcript
                assert omission_marker in visible
                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen.canvas_spacer not in positions
                assert (
                    status_position.ypos + status_position.height
                    <= bottom_positions[0].ypos
                )
                assert all(
                    current.ypos + current.height == following.ypos
                    for current, following in zip(
                        bottom_positions,
                        bottom_positions[1:],
                    )
                )
                assert (
                    bottom_positions[-1].ypos
                    + bottom_positions[-1].height
                    <= rows
                )
                assert all(
                    0 <= row < rows
                    and all(0 <= column < columns for column in cells)
                    for row, cells in screen.data_buffer.items()
                )
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


@pytest.mark.anyio
async def test_generic_tool_result_has_compact_hint_and_full_transcript() -> None:
    runtime = TuiRuntime(keymap=TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "f12"},
            }
        }
    }))
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = "\n".join(f"result line {index}" for index in range(80))

    await presentation.emit(build_generic_tool_result_view(
        "remote_tool",
        result,
        ok=True,
        call_id="long-result",
    ))

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)
    assert "(F12 to view transcript)" in display
    assert "result line 79" not in display
    assert "result line 0" in transcript
    assert "result line 79" in transcript
    assert "(F12 to view transcript)" not in transcript


@pytest.mark.parametrize(
    ("width", "expected_hint"),
    (
        (20, "… +4 lines"),
        (39, "… +4 lines Ctrl+T"),
        (40, "… +4 lines Ctrl+T"),
    ),
)
@pytest.mark.anyio
async def test_shell_transcript_hint_uses_one_visual_row(
    width: int,
    expected_hint: str,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (width, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output_lines = [f"output line {index}" for index in range(8)]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=True,
        data={
            "command": "printf output",
            "output_lines": output_lines,
        },
        call_id="transcript-hint-width",
    ))

    display = fragments_text(runtime.document.fragments(width=width))
    display_lines = display.splitlines()
    transcript = _transcript_text(runtime.document)

    assert f"    {expected_hint}" in display_lines
    assert display_lines[-1] == "    output line 7"
    assert all(get_cwidth(line) <= width for line in display_lines)
    assert display_line_count(display, width=width) == len(display_lines)
    assert output_lines[-1] in transcript
    assert expected_hint not in transcript


@pytest.mark.anyio
async def test_stable_shell_display_reflows_only_after_resize_settles() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "python3 -c \"print('" + "界" * 80 + "')\""
    output_lines = [
        f"output {index} " + "👩\u200d💻" * 30
        for index in range(8)
    ]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={
            "command": command,
            "output_lines": output_lines,
        },
        call_id="resize-stable-shell",
    ))

    def display_at(width: int, *, reflow_sources: bool) -> str:
        return fragments_text(runtime.document.fragments(
            width=width,
            reflow_sources=reflow_sources,
        ))

    narrow = display_at(20, reflow_sources=True)
    transcript = _transcript_text(runtime.document)
    raw_texts = tuple(item.raw_text for item in runtime.document.blocks)
    gaps = tuple(item.gap_before for item in runtime.document.blocks)

    runtime.document.set_display_width(80, reflow_sources=False)
    assert display_at(80, reflow_sources=False) == narrow

    runtime.document.set_display_width(80, reflow_sources=True)
    wide = display_at(80, reflow_sources=False)

    assert wide != narrow
    assert "… +4 lines" in narrow
    assert "… +4 lines (Ctrl+T to view transcript)" in wide
    assert get_cwidth(narrow.splitlines()[0]) <= 20
    assert get_cwidth(wide.splitlines()[0]) <= 80
    assert get_cwidth(wide.splitlines()[0]) > get_cwidth(
        narrow.splitlines()[0]
    )
    assert all(get_cwidth(line) <= 80 for line in wide.splitlines())
    assert _transcript_text(runtime.document) == transcript
    assert output_lines[-1] in transcript
    assert tuple(item.raw_text for item in runtime.document.blocks) == raw_texts
    assert tuple(item.gap_before for item in runtime.document.blocks) == gaps

    runtime.document.set_display_width(20, reflow_sources=False)
    assert display_at(20, reflow_sources=False) == wide

    runtime.document.set_display_width(20, reflow_sources=True)
    assert display_at(20, reflow_sources=False) == narrow
    assert _transcript_text(runtime.document) == transcript


@pytest.mark.anyio
@pytest.mark.parametrize("view", (
    build_batch_completed_view(((
        "remote_tool_with_a_long_name",
        True,
        "batch result " + ("界🙂" * 40),
    ),)),
    PlanUpdateView(
        explanation="explanation " * 12,
        items=(PlanItemView(
            step="implement a long plan step " * 8,
            status="in_progress",
        ),),
    ),
), ids=("batch", "plan"))
async def test_tree_views_reflow_from_structured_source_after_resize(view) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(view)

    narrow = fragments_text(runtime.document.fragments(
        width=20,
        reflow_sources=True,
    ))
    transcript = _transcript_text(runtime.document)
    assert runtime.document.blocks[0].display_renderer is not None
    assert all(get_cwidth(line) <= 20 for line in narrow.splitlines())

    runtime.document.set_display_width(60, reflow_sources=True)
    wide = fragments_text(runtime.document.fragments(
        width=60,
        reflow_sources=False,
    ))

    assert wide != narrow
    assert all(get_cwidth(line) <= 60 for line in wide.splitlines())
    assert _transcript_text(runtime.document) == transcript


@pytest.mark.anyio
async def test_shell_resize_replays_new_width_through_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=20)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                output = TuiOutputControl("", runtime=runtime, animate=False)
                presentation = TuiPresentationSink(output)
                command = "python3 -c \"print('" + "界" * 80 + "')\""
                output_lines = [
                    f"output {index} " + "👩\u200d💻" * 30
                    for index in range(8)
                ]

                await presentation.emit(build_native_tool_result_view(
                    "shell_command",
                    {"command": command},
                    ok=True,
                    data={
                        "command": command,
                        "output_lines": output_lines,
                    },
                    call_id="resize-scrollback-shell",
                ))
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 0
                transcript = _transcript_text(runtime.document)

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=8, columns=80)
                    runtime.viewport.observe_terminal_geometry(80, 8)
                    await asyncio.sleep(0.12)

                replayed = "".join(
                    text
                    for call in print_text.call_args_list
                    for _style, text in call.args[0]
                )

                clear.assert_called_once_with()
                assert print_text.called
                assert replayed.count("• Ran ") == 1
                assert "… +4 lines (Ctrl+T to view transcript)" in replayed
                assert all(
                    get_cwidth(line) <= 80
                    for line in replayed.splitlines()
                )
                assert _transcript_text(runtime.document) == transcript
                assert command in transcript
                assert output_lines[-1] in transcript
                assert runtime.viewport._reflowed_geometry == (80, 8)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_native_shell_result_transcript_keeps_command_and_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "Get-ChildItem\n| Select-Object -First 1"
    output_lines = [f"output line {index}" for index in range(80)]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={
            "command": command,
            "output_lines": output_lines,
        },
        call_id="long-shell",
    ))

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)
    assert "(Ctrl+T to view transcript)" in display
    assert output_lines[-1] in display
    assert output_lines[2] not in display
    assert f"$ {command}" in transcript
    assert output_lines[0] in transcript
    assert output_lines[-1] in transcript


@pytest.mark.anyio
async def test_plan_update_is_separated_from_preceding_tool() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "git status --short"},
        ok=True,
        data={"command": "git status --short", "output_lines": ["M file.py"]},
        call_id="status",
    ))
    await presentation.emit(PlanUpdateView(
        explanation="",
        items=(PlanItemView(step="检查结果", status="completed"),),
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "plan",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert "\n\n• Updated Plan" in _document_text(runtime.document)


@pytest.mark.anyio
async def test_native_tool_results_start_separate_tool_groups() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    for call_id, command in (("one", "echo one"), ("two", "echo two")):
        await presentation.emit(build_native_tool_result_view(
            "shell_command",
            {"command": command},
            ok=True,
            data={"command": command, "output_lines": [call_id]},
            call_id=call_id,
        ))

    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 1


@pytest.mark.anyio
async def test_js_repl_start_and_result_are_two_separated_blocks() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    arguments = {
        "code": "await host.tool('shell_command', {command: 'echo ready'});",
        "timeout_ms": 30000,
    }

    await presentation.emit(build_tool_start_view(
        "js_repl",
        arguments,
        call_id="call-js",
    ))

    assert len(runtime.document.blocks) == 1
    assert "host.tool('shell_command'" in _document_text(runtime.document)

    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        arguments,
        ok=True,
        data={"output": "ready"},
        call_id="call-js",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "operation",
    ]
    assert "host.tool('shell_command'" in runtime.document.blocks[0].raw_text
    assert "ready" in runtime.document.blocks[1].raw_text
    document_text = _document_text(runtime.document)
    transcript_text = _transcript_text(runtime.document)
    assert document_text.count("• JavaScript") == 2
    assert "host.tool('shell_command'" in document_text
    assert "\n  └ ready" in document_text
    assert transcript_text.count("• JavaScript") == 2
    assert "host.tool('shell_command'" in transcript_text
    assert "\nready" in transcript_text
    assert "\n└ ready" not in transcript_text
    assert "\n\n• JavaScript\n  └ ready" in document_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert isinstance(runtime.document.blocks[0].source, ToolStartView)
    assert isinstance(runtime.document.blocks[1].source, NativeToolResultView)

    await presentation.emit(build_tool_start_view(
        "js_repl",
        {"code": "console.log('next');", "timeout_ms": 30000},
        call_id="call-js-next",
    ))
    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        {"code": "console.log('next');", "timeout_ms": 30000},
        ok=True,
        data={"output": "next"},
        call_id="call-js-next",
    ))

    document_text = _document_text(runtime.document)
    assert "ready\n\n• JavaScript" in document_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
        True,
    ]


@pytest.mark.anyio
async def test_js_repl_query_padding_and_two_stage_display() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    javascript_arguments = {
        "code": "await host.tool('shell_command', {command: 'echo ready'});\n\n",
        "timeout_ms": 30000,
    }
    runtime.append_block(
        query_block(
            "js repl执行\n"
            + javascript_arguments["code"].strip("\n")
        ),
        kind="user",
    )

    await presentation.emit(build_tool_start_view(
        "js_repl",
        javascript_arguments,
        call_id="outer-js",
    ))

    running_text = _document_text(runtime.document)
    assert len(runtime.document.blocks) == 2
    assert running_text.count("• JavaScript") == 1
    assert "Running" not in running_text
    assert "Ran" not in running_text
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=80))
    ]
    javascript_line = lines.index("• JavaScript")
    assert lines[javascript_line - 2:javascript_line] == [" ", ""]
    assert lines[javascript_line - 3] != " "
    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        javascript_arguments,
        ok=True,
        data={"output": ""},
        call_id="outer-js",
    ))

    completed_text = _document_text(runtime.document)
    assert len(runtime.document.blocks) == 3
    assert completed_text.count("• JavaScript") == 2
    assert "Running" not in completed_text
    assert "Ran" not in completed_text
    assert "JavaScript cell completed." in completed_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert [type(item.source) for item in runtime.document.blocks[1:]] == [
        ToolStartView,
        NativeToolResultView,
    ]

    await output.append_assistant_delta("已执行完成。")
    await output.prepare_external_output()

    final_text = _document_text(runtime.document)
    assert "JavaScript cell completed.\n\n─" in final_text
    assert "─\n\n• 已执行完成。" in final_text


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_rows", (8, 12, 24))
async def test_oversized_javascript_preview_pushes_title_to_native_scrollback(
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)
        application = TuiApplicationSink(runtime)
        arguments = {
            "code": "\n".join(
                f"const value{index} = {index};" for index in range(30)
            ),
            "timeout_ms": 30000,
        }

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=40),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    application.emit(ApplicationView(type="intro"))
                    runtime.append_submitted_query(
                        "run javascript",
                        "turn-javascript",
                    )
                    await _render_next_frame(runtime)
                    before_tool = runtime.document.scrollback_line_count

                    runtime.set_execution_active(True)
                    await presentation.emit(build_tool_start_view(
                        "js_repl",
                        arguments,
                        call_id="call-javascript",
                    ))
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(
                        runtime,
                        after=before_tool,
                    )
                    await presentation.emit(build_native_tool_result_view(
                        "js_repl",
                        arguments,
                        ok=True,
                        data={
                            "output": "\n".join(
                                f"result {index}" for index in range(30)
                            ),
                        },
                        call_id="call-javascript",
                    ))
                    completed_screen = await _render_next_frame(runtime)
                    await asyncio.sleep(0.02)

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                visible = f"{printed}\n{_rendered_screen_text(completed_screen)}"
                transcript = _transcript_text(runtime.document)

                assert f">_ {const.APP_DESC}" in printed
                assert "run javascript" in printed
                assert visible.count("• JavaScript") >= 2
                assert "const value0 = 0;" in visible
                assert "const value29 = 29;" not in visible
                assert "result 0" in visible
                assert "result 29" not in visible
                assert "… +12 lines" in visible
                assert "… +25 lines" in visible
                assert "const value29 = 29;" in transcript
                assert "result 29" in transcript
            finally:
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


@pytest.mark.anyio
async def test_native_shell_result_strips_ansi_from_ordered_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "Get-Item video.mp4 | Format-List"},
        ok=True,
        data={
            "command": "Get-Item video.mp4 | Format-List",
            "output_lines": ["\x1b[32;1mFullName : \x1b[0mvideo.mp4"],
        },
        call_id="ansi-output",
    ))

    document_text = _document_text(runtime.document)
    assert "\x1b" not in document_text
    assert "FullName : video.mp4" in document_text


@pytest.mark.anyio
async def test_approval_sequence_separates_resumed_operation() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_approval_view(approval, decision="acceptForSession"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        approval["arguments"],
        call_id="approved",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "approval",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 2


@pytest.mark.anyio
async def test_tool_approval_tool_sequence_separates_human_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo before"},
        call_id="before",
    ))
    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo after"},
        call_id="after",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
