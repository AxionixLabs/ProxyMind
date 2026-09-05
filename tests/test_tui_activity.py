# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.output import DummyOutput

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.ports import (
    AssistantBuffered,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
)
from agent.ports.presentation import ApplicationView
from frontends.runtime import FrontendActivity
from frontends.interaction import PromptContext
from frontends.terminal.color_support import TerminalColorLevel
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.activity import (
    TuiActivity,
    _compact_elapsed_label,
    _download_block,
    _elapsed_label,
    _mcp_activity_block,
    _mcp_final_block,
    _operation_activity_block,
    _status_block,
    _upload_block,
)
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.status_frames import SPINNER_FRAMES
from frontends.tui.core.styles import text_block
from frontends.tui.core.task_state import TuiTaskState
from frontends.tui.features.helix import TuiUpgradeProgress
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.runtime.turn_surface import TuiTurnSurfaceCoordinator
from frontends.terminal.worked import emit_worked_footer
from frontends.terminal.mcp_status import (
    external_mcp_status_view,
    inbuild_status_view,
)


async def _render_next_frame(runtime: TuiRuntime):
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(40):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("TUI frame was not rendered")


def _absolute_window_row(runtime, screen, window, *, rows: int) -> int:
    position = screen.visible_windows_to_write_positions[window]
    return rows - runtime.screen._visible_height() + position.ypos


def test_task_state_aggregates_turn_and_activity_sources() -> None:
    activity_running = [False]
    state = TuiTaskState(activity_running=lambda: activity_running[0])

    state.set_turn_running(True)
    activity_running[0] = True
    state.set_turn_running(False)

    assert state.running

    activity_running[0] = False

    assert not state.running


def test_pending_turn_is_busy_and_defers_submission() -> None:
    runtime = TuiRuntime()

    runtime.set_turn_start_pending(True)

    assert runtime.task_running
    assert runtime.submission_deferred

    runtime.set_turn_start_pending(False)

    assert not runtime.task_running
    assert not runtime.submission_deferred


@pytest.mark.anyio
@pytest.mark.parametrize("animate", (False, True))
async def test_frontend_activity_does_not_restore_wait_for_auxiliary_activity(
    animate: bool,
) -> None:
    runtime = SimpleNamespace(
        active=True,
        execution_active=True,
        turn_start_pending=False,
        end_activity_status=AsyncMock(),
    )
    activity = FrontendActivity(
        runtime,
        SimpleNamespace(stop=AsyncMock()),
        enabled=animate,
    )

    await activity.stop("inbuild", settle=False)

    runtime.end_activity_status.assert_awaited_once_with(
        "inbuild",
        settle=False,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ("inbuild", "external_mcp"))
async def test_query_during_startup_activity_hands_off_to_wait_status(
    kind: str,
) -> None:
    runtime = TuiRuntime()

    if kind == "inbuild":
        await runtime.begin_inbuild_status(
            lambda: {"state": "starting", "label": "Helix MCP"}
        )
    else:
        await runtime.begin_external_mcp_status(
            lambda: {
                "done": False,
                "items": [{"name": "docs", "state": "linking", "tools": 0}],
            }
        )

    read_task = asyncio.create_task(runtime.read_message(PromptContext(
        model="test-model",
    )))
    runtime.screen.input.buffer.text = "continue during startup"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert await read_task == "continue during startup"
    assert runtime.turn_start_pending

    await runtime.begin_wait_status()
    await runtime.end_activity_status(kind, settle=False)

    assert runtime.task_running
    assert "continue during startup" in "".join(
        text for _style, text in runtime.document.fragments(width=80)
    )
    assert runtime.activity.lease(kind) is None
    assert runtime.screen.activity_block is not None
    assert "Thinking" in "".join(
        text for _style, text in runtime.screen.activity_block.fragments
    )

    await runtime.activity.clear()


def test_elapsed_label_keeps_seconds_bucket_width_stable() -> None:
    assert _elapsed_label(9.94) == "9.9s"
    assert _elapsed_label(9.99) == "9.9s"
    assert _elapsed_label(10.0) == " 10s"
    assert _elapsed_label(59.9) == " 59s"
    assert _elapsed_label(60.0) == "1m 00s"


def test_codex_elapsed_label_formats_seconds_minutes_and_hours() -> None:
    assert _compact_elapsed_label(0.9) == "0s"
    assert _compact_elapsed_label(59.9) == "59s"
    assert _compact_elapsed_label(60.0) == "1m 00s"
    assert _compact_elapsed_label(3_661.0) == "1h 01m 01s"


@pytest.mark.anyio
async def test_turn_wait_uses_codex_interrupt_status_copy() -> None:
    runtime = TuiRuntime()

    await runtime.begin_wait_status()

    assert runtime.screen.activity_block is not None
    status = _block_text(runtime.screen.activity_block)
    assert status.startswith("• Thinking (0s • esc to interrupt)")

    await runtime.activity.clear()


@pytest.mark.anyio
async def test_execution_deactivation_clears_wait_without_touching_auxiliary(
) -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    await runtime.begin_external_mcp_status(
        lambda: {
            "done": False,
            "items": [{"name": "docs", "state": "linking", "tools": 0}],
        }
    )

    runtime.set_execution_active(False)

    assert runtime.activity.lease("wait") is None
    assert runtime.activity.lease("external_mcp") is not None
    await runtime.activity.clear()

    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    await runtime.freeze_activity_status("wait")
    runtime.set_execution_active(False)
    assert runtime.activity.lease("wait") is None

@pytest.mark.anyio
async def test_worked_view_does_not_release_wait_lease() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    lease = runtime.activity.lease("wait")
    assert lease is not None

    TuiApplicationSink(runtime)._emit_active(ApplicationView(type="run.worked"))

    assert runtime.activity.lease("wait") == lease
    runtime.set_execution_active(False)
    assert runtime.activity.lease("wait") is None


@pytest.mark.anyio
async def test_stale_external_mcp_lease_cannot_clear_new_startup_activity() -> None:
    rendered = []
    activity = TuiActivity(
        set_renderable=lambda block: rendered.__setitem__(slice(None), [block]),
        clear_renderable=lambda: rendered.clear(),
    )

    await activity.begin_external_mcp(lambda: {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    })
    stale_lease = activity.lease("external_mcp")
    assert stale_lease is not None

    await activity.begin_external_mcp(lambda: {
        "done": False,
        "items": [{"name": "docs", "state": "ready", "tools": 1}],
    })
    replacement_lease = activity.lease("external_mcp")
    assert replacement_lease is not None
    assert replacement_lease != stale_lease

    assert not activity.release(stale_lease)
    assert "External MCP" in "".join(
        text for _style, text in rendered[-1].fragments
    )
    await activity.clear()


@pytest.mark.anyio
async def test_model_submission_marks_pending_before_inline_process_detach(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    detach_started = asyncio.Event()
    release_detach = asyncio.Event()

    async def detach() -> None:
        detach_started.set()
        await release_detach.wait()

    monkeypatch.setattr(runtime, "detach_inline_process", detach)

    read_task = asyncio.create_task(runtime.read_message(PromptContext(
        model="test-model",
    )))
    runtime.screen.input.buffer.text = "continue the task"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    await detach_started.wait()
    assert runtime.turn_start_pending
    assert runtime.submission_deferred

    release_detach.set()
    assert await read_task == "continue the task"

    runtime.set_turn_start_pending(False)
    assert not runtime.turn_start_pending


def test_visual_update_merges_nested_invalidation_requests() -> None:
    runtime = TuiRuntime()

    with patch.object(runtime.screen, "_invalidate_now") as invalidate:
        with runtime.screen.visual_update():
            runtime.invalidate()
            with runtime.screen.visual_update():
                runtime.invalidate()

    invalidate.assert_called_once_with()


@pytest.mark.anyio
async def test_final_separator_preserves_input_after_assistant_wait_handoff() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                await runtime.begin_wait_status()
                await output.append_assistant_delta("answer\n")
                screen = await _render_next_frame(runtime)
                assert runtime.activity.lease("wait") is None

                baseline_transcript = _absolute_window_row(
                    runtime,
                    screen,
                    runtime.screen.transcript_window,
                    rows=16,
                )
                baseline_input = _absolute_window_row(
                    runtime,
                    screen,
                    runtime.screen.input.window,
                    rows=16,
                )
                observed: list[tuple[int, int]] = []

                def capture_frame(_application) -> None:
                    rendered = runtime.screen.application.renderer.last_rendered_screen
                    positions = rendered.visible_windows_to_write_positions
                    if (
                        runtime.screen.transcript_window not in positions
                        or runtime.screen.input.window not in positions
                    ):
                        return None
                    observed.append((
                        _absolute_window_row(
                            runtime,
                            rendered,
                            runtime.screen.transcript_window,
                            rows=16,
                        ),
                        _absolute_window_row(
                            runtime,
                            rendered,
                            runtime.screen.input.window,
                            rows=16,
                        ),
                    ))

                runtime.screen.application.after_render += capture_frame

                await output._commit_current()
                await _render_next_frame(runtime)

                output.note_work_activity()
                await output.complete_turn()
                emit_worked_footer(TuiApplicationSink(runtime), 1.2)
                await _render_next_frame(runtime)

                runtime.set_execution_active(False)
                await _render_next_frame(runtime)

                assert observed
                assert all(
                    input_row == baseline_input
                    for _transcript_row, input_row in observed
                )
                assert all(
                    transcript_row <= baseline_transcript
                    for transcript_row, _input_row in observed
                )
                assert runtime.screen.activity_block is None
                assert [item.kind for item in runtime.document.blocks] == [
                    "assistant",
                    "system",
                ]
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_visible_assistant_atomically_replaces_animated_wait() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        context = OutputSurfaceContext(
            surface_id="surface_frame_test",
            cid="cid_frame_test",
            sid="sid_frame_test",
            turn_id="turn_frame_test",
            agent_id="root",
        )
        session = create_tui_output_session(
            "",
            context=context,
            runtime=runtime,
            animate=True,
        )
        output = session.control
        coordinator = session.activity
        assert isinstance(output, TuiOutputControl)
        assert isinstance(coordinator, TuiTurnSurfaceCoordinator)
        identity = ResponseIdentity("turn_frame_test", 1, 1, 1)
        frames: list[tuple[str, int]] = []
        handler_registered = False

        def capture_frame(_application) -> None:
            screen = runtime.screen.application.renderer.last_rendered_screen
            positions = screen.visible_windows_to_write_positions
            if runtime.screen.input.window not in positions:
                return None
            text = "\n".join(
                "".join(
                    cells[column].char
                    for column in sorted(cells)
                ).rstrip()
                for _row, cells in sorted(screen.data_buffer.items())
            )
            frames.append((
                text,
                _absolute_window_row(
                    runtime,
                    screen,
                    runtime.screen.input.window,
                    rows=16,
                ),
            ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                await runtime.begin_wait_status()
                await session.open()
                await coordinator.emit(ModelWaitRequested(
                    surface_id=context.surface_id,
                    turn_id=context.turn_id,
                    revision=1,
                    reason="initial",
                ))
                await coordinator.emit(AssistantBuffered(
                    surface_id=context.surface_id,
                    turn_id=context.turn_id,
                    identity=identity,
                    item_id="item_frame_test",
                ))
                waiting_screen = await _render_next_frame(runtime)
                waiting_input_row = _absolute_window_row(
                    runtime,
                    waiting_screen,
                    runtime.screen.input.window,
                    rows=16,
                )

                runtime.screen.application.after_render += capture_frame
                handler_registered = True

                await session.content.emit(AssistantTextDelta(
                    "final answer",
                    identity,
                    item_id="item_frame_test",
                ))
                assert runtime.activity.lease("wait") is not None
                assert runtime.document.active_block is None

                await session.content.emit(AssistantSegmentCompleted(
                    identity,
                    item_id="item_frame_test",
                ))
                await _render_next_frame(runtime)

                assert frames
                assert all(
                    ("Thinking" in text) != ("final answer" in text)
                    for text, _input_row in frames
                )
                assert all(
                    input_row == waiting_input_row
                    for _text, input_row in frames
                )
                assert "final answer" in frames[-1][0]
                assert "Thinking" not in frames[-1][0]
                assert runtime.activity.lease("wait") is None
                assert runtime.task_state.turn_running

                await session.close()
                runtime.set_execution_active(False)
                final_screen = await _render_next_frame(runtime)
                final_text = "\n".join(
                    "".join(
                        cells[column].char
                        for column in sorted(cells)
                    ).rstrip()
                    for _row, cells in sorted(final_screen.data_buffer.items())
                )
                assert "final answer" in final_text
                assert "Thinking" not in final_text
            finally:
                if handler_registered:
                    runtime.screen.application.after_render -= capture_frame
                runtime.set_execution_active(False)
                await session.close()
                await runtime.close()


@pytest.mark.anyio
async def test_stale_activity_lease_does_not_clear_replacement() -> None:
    rendered = []
    activity = TuiActivity(
        set_renderable=lambda block: rendered.__setitem__(slice(None), [block]),
        clear_renderable=lambda: rendered.clear(),
    )

    await activity.begin_compact(lambda: {"summary": "first"})
    lease = activity.lease("compact")
    assert lease is not None

    await activity.begin_compact(lambda: {"summary": "second"})

    assert not activity.release(lease)
    assert "second" in _block_text(rendered[-1])
    await activity.clear()

def test_tui_output_session_exposes_one_typed_activity_channel() -> None:
    runtime = TuiRuntime()
    session = create_tui_output_session(
        "",
        context=OutputSurfaceContext(
            surface_id="surface_test",
            cid="cid_test",
            sid="sid_test",
            turn_id="turn_test",
            agent_id="root",
        ),
        runtime=runtime,
        animate=False,
    )

    assert isinstance(session.control, TuiOutputControl)
    assert not hasattr(session, "status")
    assert not hasattr(session.control, "begin_reply_wait_status")


def test_infrastructure_activities_keep_rotating_spinner() -> None:
    inbuild = {"state": "starting", "label": "Helix MCP"}
    external = {
        "done": False,
        "items": [{"name": "github", "state": "linking", "tools": 0}],
    }
    download = {
        "stage": "downloading",
        "filename": "helix.zip",
        "phase": 0.5,
        "done": 5,
        "total": 10,
        "speed": 2,
    }
    upload = {
        "event": None,
        "item_total": 1,
        "total_bytes": 10,
    }

    indicators = (
        {
            _block_text(_mcp_activity_block(
                inbuild_status_view(inbuild),
                phase=phase,
                width=80,
            ))[0]
            for phase in (0.0, 0.1, 0.2)
        },
        {
            _block_text(_mcp_activity_block(
                external_mcp_status_view(external, detail_limit=0),
                phase=phase,
                width=80,
            ))[0]
            for phase in (0.0, 0.1, 0.2)
        },
        {
            _block_text(_download_block(download, phase=phase))[0]
            for phase in (0.0, 0.1, 0.2)
        },
        {
            _block_text(_upload_block(upload, phase=phase))[0]
            for phase in (0.0, 0.1, 0.2)
        },
    )

    assert all(len(values) == 3 for values in indicators)
    assert all(values <= set(SPINNER_FRAMES) for values in indicators)


def test_activity_progress_uses_regular_weight() -> None:
    blocks = (
        _mcp_activity_block(
            inbuild_status_view({"state": "starting", "label": "Helix MCP"}),
            phase=0.1,
            width=80,
        ),
        _download_block({
            "stage": "downloading",
            "filename": "helix.zip",
            "phase": 0.5,
            "done": 5,
            "total": 10,
            "speed": 2,
        }, phase=0.1),
        _upload_block({
            "event": None,
            "item_total": 1,
            "total_bytes": 10,
        }, phase=0.1),
    )

    assert all(
        "bold" not in style
        for block in blocks
        for style, text in block.fragments
        if text.strip()
    )


def test_operation_activity_requires_explicit_summary() -> None:
    with pytest.raises(ValueError, match="summary is required"):
        _operation_activity_block({}, phase=0.1, width=80)


def test_status_spinner_preserves_no_color_boundary() -> None:
    block = _status_block(
        "Thinking",
        family="wait",
        phase=0.1,
        spinner=True,
        sweep=False,
        color_level=TerminalColorLevel.NONE,
    )

    assert block.fragments[0][0] == ""


def test_mcp_activity_animates_spinner_without_sweeping_text() -> None:
    view = external_mcp_status_view({
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }, detail_limit=0)
    frames = [
        _mcp_activity_block(view, phase=phase, width=80)
        for phase in (0.0, 0.1, 0.2)
    ]

    assert len({_block_text(block)[0] for block in frames}) == 3
    assert frames[0].fragments[1:] == frames[1].fragments[1:]
    assert frames[1].fragments[1:] == frames[2].fragments[1:]


def test_external_mcp_failure_uses_color_without_bold() -> None:
    view = external_mcp_status_view({
        "done": True,
        "items": [
            {"name": "github", "state": "failed", "tools": 0, "detail": "timeout"},
        ],
    })

    block = _mcp_final_block(view, width=80)

    assert block is not None
    assert "External MCP failed" in _block_text(block)
    assert all(
        "bold" not in style
        for style, text in block.fragments
        if text.strip()
    )


@pytest.mark.anyio
async def test_consecutive_approvals_do_not_restore_finished_turn_wait() -> None:
    runtime = TuiRuntime()
    coordinator = ApprovalCoordinator(runtime)

    await runtime.begin_wait_status()
    runtime.begin_terminal_progress()

    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))

    await _wait_for_approval(runtime, "first")
    await runtime.end_activity_status("wait", settle=False)
    runtime.end_terminal_progress()
    runtime.screen.approval.finish("accept")

    await _wait_for_approval(runtime, "second")
    runtime.screen.approval.finish("accept")

    assert await asyncio.gather(first, second) == ["accept", "accept"]
    assert not runtime.activity.active
    assert runtime.screen._status_fragments() == []


@pytest.mark.anyio
async def test_consecutive_approvals_share_one_interaction_surface() -> None:
    runtime = TuiRuntime()
    coordinator = ApprovalCoordinator(runtime)

    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))

    await _wait_for_approval(runtime, "first")
    assert runtime.screen.approval.pending_count == 1

    runtime.screen.approval.finish("accept")

    assert await first == "accept"
    await _wait_for_approval(runtime, "second")
    assert runtime.screen.bottom_pane.active_surface == "approval"

    runtime.screen.approval.finish("accept")

    assert await second == "accept"
    assert runtime.screen.bottom_pane.active_surface is None
    assert runtime.activity.lease("wait") is None


@pytest.mark.anyio
async def test_request_approval_restores_terminal_progress_after_failure() -> None:
    calls = []

    class ApprovalStub(object):
        def snapshot_changed(self, snapshot):
            _ = snapshot

        def begin_session(self):
            calls.append("approval.begin")

        async def request(self, approval):
            calls.append("approval.request")
            raise RuntimeError("approval failed")

        async def end_session(self):
            calls.append("approval.end")

    runtime = TuiRuntime.__new__(TuiRuntime)
    runtime.screen = SimpleNamespace(approval=ApprovalStub())
    runtime._approval_session_lock = asyncio.Lock()
    runtime._approval_session_active = False
    runtime._closing = False
    runtime._turn_progress_active = True
    runtime.terminal_progress = SimpleNamespace(
        warning=lambda: calls.append("warning"),
        begin=lambda: calls.append("progress"),
        clear=lambda: calls.append("clear"),
    )

    with pytest.raises(RuntimeError, match="approval failed"):
        await ApprovalCoordinator(runtime).request({})

    assert calls == [
        "approval.begin",
        "warning",
        "approval.request",
        "approval.end",
        "progress",
    ]


@pytest.mark.anyio
async def test_runtime_close_settles_active_approval_batch() -> None:
    runtime = TuiRuntime()
    coordinator = ApprovalCoordinator(runtime)
    first = asyncio.create_task(coordinator.request({
        "id": "first",
        "tool": "shell_command",
        "command": "echo first",
        "show_timer": False,
    }))
    second = asyncio.create_task(coordinator.request({
        "id": "second",
        "tool": "shell_command",
        "command": "echo second",
        "show_timer": False,
    }))
    await _wait_for_approval(runtime, "first")

    await coordinator.close()
    await runtime.close()

    assert await asyncio.gather(first, second) == ["decline", "decline"]
    assert not runtime.screen.approval.active
    assert runtime.screen.approval.pending_count == 0
    assert not runtime._approval_session_active


@pytest.mark.anyio
async def test_mcp_activities_render_together_and_finish_independently() -> None:
    rendered = []
    inbuild = {"state": "starting", "label": "Helix MCP"}
    external = {
        "done": False,
        "items": [{"name": "github", "state": "linking", "tools": 0}],
    }
    activity = TuiActivity(
        set_renderable=lambda block: rendered.__setitem__(slice(None), [block]),
        clear_renderable=lambda: rendered.clear(),
    )

    await activity.begin_inbuild(lambda: dict(inbuild))
    await activity.begin_external_mcp(lambda: dict(external))

    text = _block_text(rendered[-1])
    assert "Helix MCP starting" in text
    assert "External MCP linking · 0/1 servers" in text
    assert "\n\n" in text
    assert rendered[-1].preserve_newlines

    inbuild["state"] = "ready"
    await activity.stop("inbuild")

    assert "■ Helix MCP ready" in _block_text(rendered[-1])
    assert "External MCP linking" in _block_text(rendered[-1])
    assert activity.active

    await activity.stop("external_mcp")
    assert not activity.active
    await activity.clear()


@pytest.mark.anyio
async def test_external_mcp_final_status_settles_without_entering_document() -> None:
    runtime = TuiRuntime()
    snapshot = {
        "done": False,
        "items": [
            {"name": "github", "state": "linking", "tools": 0, "detail": ""},
            {"name": "docs", "state": "linking", "tools": 0, "detail": ""},
        ],
    }

    await runtime.begin_external_mcp_status(lambda: dict(snapshot))
    assert runtime.task_running
    snapshot["done"] = True
    snapshot["items"] = [
        {"name": "github", "state": "ready", "tools": 7, "detail": ""},
        {"name": "docs", "state": "failed", "tools": 0, "detail": "timeout"},
    ]
    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
        await runtime.end_activity_status("external_mcp")

    assert runtime.screen.activity_block is not None
    assert not runtime.task_running
    assert not runtime.document.blocks
    final_text = _block_text(runtime.screen.activity_block)
    assert "External MCP ready · 1/2 servers · 7 tools" in final_text
    assert "docs: timeout" not in final_text
    assert all(
        "bold" not in style
        for style, text in runtime.screen.activity_block.fragments
        if text.strip()
    )

    await asyncio.sleep(0.1)
    assert runtime.screen.activity_block is None


@pytest.mark.anyio
async def test_new_activity_replaces_settling_status_without_old_expiration() -> None:
    runtime = TuiRuntime()
    snapshot = {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }

    await runtime.begin_external_mcp_status(lambda: dict(snapshot))
    snapshot["done"] = True
    snapshot["items"] = [
        {"name": "docs", "state": "ready", "tools": 4},
    ]

    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
        await runtime.end_activity_status("external_mcp")

    snapshot["done"] = False
    snapshot["items"] = [
        {"name": "github", "state": "linking", "tools": 0},
    ]
    await runtime.begin_external_mcp_status(lambda: dict(snapshot))
    await asyncio.sleep(0.1)

    assert runtime.activity.active
    assert "External MCP linking" in _block_text(runtime.screen.activity_block)
    await runtime.activity.clear()


@pytest.mark.anyio
async def test_frozen_only_activity_waits_without_repeated_rendering() -> None:
    rendered = []
    cleared = []
    snapshot = {
        "stage": "downloading",
        "received_bytes": 5,
        "total_bytes": 10,
        "elapsed_sec": 0.1,
        "speed_bytes_per_sec": 50,
    }
    activity = TuiActivity(
        set_renderable=rendered.append,
        clear_renderable=lambda: cleared.append(True),
    )

    await activity.begin_download(lambda: dict(snapshot))
    snapshot["stage"] = "done"
    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.04):
        await activity.stop("download")

    final = rendered[-1]
    render_count = len(rendered)
    settle_task = activity._settle_task

    assert activity.task is None
    assert settle_task is not None
    await asyncio.sleep(0.02)

    assert rendered[-1] == final
    assert len(rendered) == render_count
    assert not cleared

    await asyncio.sleep(0.04)
    assert len(rendered) == render_count
    assert cleared == [True]
    assert settle_task.done()
    assert activity._settle_task is None
    await activity.clear()


@pytest.mark.anyio
async def test_animated_activity_continues_while_frozen_slot_settles() -> None:
    rendered = []
    download = {
        "stage": "downloading",
        "received_bytes": 5,
        "total_bytes": 10,
        "elapsed_sec": 0.1,
        "speed_bytes_per_sec": 50,
    }
    external = {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }
    activity = TuiActivity(
        set_renderable=rendered.append,
        clear_renderable=lambda: None,
    )

    await activity.begin_download(lambda: dict(download))
    await activity.begin_external_mcp(lambda: dict(external))
    download["stage"] = "done"
    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.12):
        await activity.stop("download")

    render_count = len(rendered)
    await asyncio.sleep(0.05)
    assert len(rendered) > render_count
    assert "Download complete" in _block_text(rendered[-1])
    assert "External MCP linking" in _block_text(rendered[-1])

    await asyncio.sleep(0.09)
    assert "Download complete" not in _block_text(rendered[-1])
    assert "External MCP linking" in _block_text(rendered[-1])
    assert activity.active
    await activity.clear()


@pytest.mark.anyio
async def test_clear_cancels_pending_frozen_activity_expiry() -> None:
    rendered = []
    cleared = []
    snapshot = {
        "stage": "downloading",
        "received_bytes": 5,
        "total_bytes": 10,
        "elapsed_sec": 0.1,
        "speed_bytes_per_sec": 50,
    }
    activity = TuiActivity(
        set_renderable=rendered.append,
        clear_renderable=lambda: cleared.append(True),
    )

    await activity.begin_download(lambda: dict(snapshot))
    snapshot["stage"] = "done"
    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.04):
        await activity.stop("download")

    settle_task = activity._settle_task
    assert settle_task is not None

    await activity.clear()
    assert cleared == [True]
    assert settle_task.done()
    assert activity._settle_task is None

    await asyncio.sleep(0.06)
    assert cleared == [True]


@pytest.mark.anyio
async def test_external_mcp_status_can_clear_without_settling() -> None:
    runtime = TuiRuntime()
    snapshot = {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }

    await runtime.begin_external_mcp_status(lambda: dict(snapshot))
    snapshot["done"] = True
    snapshot["items"] = [
        {"name": "docs", "state": "ready", "tools": 4},
    ]

    await runtime.end_activity_status("external_mcp", settle=False)

    assert runtime.screen.activity_block is None


@pytest.mark.anyio
async def test_compact_activity_does_not_replace_external_mcp_status() -> None:
    runtime = TuiRuntime()
    external = {
        "done": False,
        "items": [{"name": "docs", "state": "linking"}],
    }
    compact = {
        "summary": "Context compacting...",
        "done": False,
    }

    await runtime.begin_external_mcp_status(lambda: dict(external))
    await runtime.begin_compact_status(lambda: dict(compact))

    active = _block_text(runtime.screen.activity_block)
    assert "External MCP linking" in active
    assert "Context compacting..." in active

    await runtime.end_activity_status("compact", settle=False)

    remaining = _block_text(runtime.screen.activity_block)
    assert "External MCP linking" in remaining
    assert "Context compacting..." not in remaining

    await runtime.end_activity_status("external_mcp", settle=False)
    assert not runtime.task_running
    assert not runtime.document.blocks


@pytest.mark.anyio
@pytest.mark.parametrize(
    "final_stage",
    ["done", "failed", "cancelled"],
)
async def test_runtime_download_holds_final_status_for_inbuild_handoff(
    final_stage: str,
) -> None:
    runtime = TuiRuntime()
    progress = TuiUpgradeProgress(runtime)
    state = {
        "stage": "downloading",
        "filename": "helix.dist",
        "phase": 0.5,
        "done": 5 * 1024 * 1024,
        "total": 10 * 1024 * 1024,
        "speed": 2 * 1024 * 1024,
    }

    await progress.start(state)

    active_text = _block_text(runtime.screen.activity_block)
    assert "downloading" in active_text
    assert "50.0%" in active_text
    assert "5.0 MB / 10.0 MB · 2.0 MB/s" in active_text
    assert "helix.dist" not in active_text
    assert "Internal MCP" not in active_text
    assert all(
        "bold" not in style
        for style, text in runtime.screen.activity_block.fragments
        if text.strip()
    )

    state["stage"] = final_stage
    await progress.stop()

    assert runtime.screen.activity_block is not None
    expected = "complete" if final_stage == "done" else final_stage
    final_text = _block_text(runtime.screen.activity_block)
    assert expected in final_text
    if final_stage == "failed":
        assert final_text.startswith("■ Download failed")
        assert all(
            "bold" not in style
            for style, text in runtime.screen.activity_block.fragments
            if text.strip()
        )
    assert "helix.dist" not in final_text
    assert not runtime.document.blocks


@pytest.mark.parametrize(
    "stage",
    ["warming", "connecting", "verifying", "extracting", "installing", "cleaning"],
)
def test_runtime_download_stages_hide_package_name(stage: str) -> None:
    block = _download_block({
        "stage": stage,
        "filename": "helix.app",
        "phase": 0.5,
        "done": 5,
        "total": 10,
        "speed": 2,
    }, phase=0.1)

    assert "helix.app" not in _block_text(block)


@pytest.mark.anyio
async def test_download_and_inbuild_share_runtime_slot_without_blank_frame() -> None:
    rendered = []
    download = {
        "stage": "downloading",
        "filename": "helix.zip",
        "phase": 0.5,
        "done": 5,
        "total": 10,
        "speed": 2,
    }
    inbuild = {"state": "starting", "label": "Helix MCP"}
    activity = TuiActivity(
        set_renderable=lambda block: rendered.__setitem__(slice(None), [block]),
        clear_renderable=lambda: rendered.clear(),
    )

    await activity.begin_download(lambda: dict(download))
    download["stage"] = "done"
    await activity.stop("download")
    assert "Download complete" in _block_text(rendered[-1])

    await activity.begin_inbuild(lambda: dict(inbuild))
    text = _block_text(rendered[-1])
    assert "Helix MCP starting" in text
    assert "Download" not in text
    assert "\n" not in text

    await activity.clear()


@pytest.mark.anyio
async def test_upload_success_settles_without_entering_document() -> None:
    runtime = TuiRuntime()
    snapshot = {
        "event": {
            "phase": "uploading",
            "done": True,
            "item_index": 1,
            "item_total": 1,
            "filename": "report.pdf",
            "aggregate_total_bytes": 10,
            "aggregate_elapsed_sec": 0.2,
            "aggregate_speed_bytes_per_sec": 50,
        },
        "failed": False,
    }

    await runtime.begin_upload_status(lambda: dict(snapshot))
    with patch("frontends.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
        await runtime.end_activity_status("upload")

    assert runtime.screen.activity_block is not None
    assert "Attach done" in _block_text(runtime.screen.activity_block)
    assert not runtime.document.blocks

    await asyncio.sleep(0.1)
    assert runtime.screen.activity_block is None


@pytest.mark.anyio
async def test_external_mcp_activity_does_not_replace_streaming_content() -> None:
    runtime = TuiRuntime()
    stream = FragmentBlock((("", "streaming answer"),))
    snapshot = {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }

    runtime.set_active_renderable(stream)
    await runtime.begin_external_mcp_status(lambda: dict(snapshot))

    assert runtime.document.active_block is not None
    assert _block_text(runtime.document.active_block) == "streaming answer"
    assert "External MCP linking" in _block_text(runtime.screen.activity_block)

    await runtime.activity.clear()


@pytest.mark.anyio
async def test_interrupted_presentation_keeps_turn_lifecycle_active() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.track_pending_steer(TuiSubmission(
        value="next query",
        editable_text="next query",
        paste_store={},
    ))
    runtime.set_active_renderable(FragmentBlock((("", "partial answer"),)))
    await runtime.activity.begin_wait()

    runtime.finish_interrupted_presentation()

    assert runtime.execution_active
    assert runtime.document.active_block is None
    assert runtime.activity.lease("wait") is None
    assert "Queued while interrupted turn settles" in fragments_text(
        runtime.screen._queued_fragments(width=80)
    )

    runtime.set_execution_active(False)
    await runtime.close()


@pytest.mark.anyio
async def test_activity_rows_are_clipped_with_ellipsis() -> None:
    rendered = []
    activity = TuiActivity(
        set_renderable=lambda block: rendered.__setitem__(slice(None), [block]),
        clear_renderable=lambda: rendered.clear(),
        get_width=lambda: 20,
    )

    await activity.begin_upload(lambda: {
        "event": {
            "phase": "uploading",
            "item_index": 1,
            "item_total": 1,
            "filename": "a-very-long-report-name.pdf",
        },
    })

    text = _block_text(rendered[-1])
    assert text.endswith("…")
    assert len(text) <= 20

    await activity.clear()


def test_download_and_upload_activity_rows_are_single_line() -> None:
    download = _download_block({
        "stage": "downloading",
        "filename": "helix.zip",
        "phase": 0.5,
        "done": 5,
        "total": 10,
        "speed": 2,
    }, phase=0.1)
    upload = _upload_block({
        "event": {
            "phase": "uploading",
            "item_index": 1,
            "item_total": 2,
            "filename": "report.pdf",
            "aggregate_uploaded_bytes": 5,
            "aggregate_total_bytes": 10,
            "aggregate_speed_bytes_per_sec": 2,
        },
    }, phase=0.1)

    assert "\n" not in _block_text(download)
    assert "\n" not in _block_text(upload)


def test_tui_application_body_styles_do_not_use_bold() -> None:
    style = TuiRuntime().screen.application.style

    for name in (
        "class:queue.label",
        "class:queue.marker",
        "class:input.notice",
        "class:shell.title.command",
        "class:ps.error",
    ):
        assert not style.get_attrs_for_style_str(name).bold

    assert style.get_attrs_for_style_str("class:queue.marker").dim
    assert style.get_attrs_for_style_str("class:ps.title").bold
    assert style.get_attrs_for_style_str("class:shell.title.action").bold
    assert style.get_attrs_for_style_str("class:ps.output").dim


def test_activity_completion_does_not_change_input_stack_height() -> None:
    runtime = TuiRuntime()
    line = FragmentBlock((("", "one line"),))

    input_height = runtime.screen._input_stack_height()
    runtime.screen.set_activity_renderable(line)
    runtime.screen.clear_activity_renderable()

    assert runtime.screen._input_stack_height() == input_height


def test_input_surface_reserves_padding_around_dynamic_input() -> None:
    runtime = TuiRuntime()

    assert runtime.screen.INPUT_SURFACE_PADDING_HEIGHT == 1
    assert runtime.screen._input_surface_height() == 3
    assert runtime.screen._footer_height() == 1
    assert (
        runtime.screen._input_stack_height()
        == runtime.screen._input_surface_height() + 1
    )


@pytest.mark.anyio
async def test_foreground_command_keeps_footer_visible_while_running() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        release = asyncio.Event()
        started = asyncio.Event()
        host = SimpleNamespace(
            lifecycle=SimpleNamespace(
                await_cleanup=lambda awaitable: awaitable,
            ),
        )
        foreground = TuiForegroundTasks(runtime, host)

        async def operation() -> str:
            await runtime.begin_operation_status(
                lambda: {"summary": "Helix MCP starting"},
            )
            started.set()
            await release.wait()
            return "ready"

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            wait_task = None
            try:
                context = PromptContext(
                    model="test-model",
                    permissions_label="Auto",
                    workspace_label="repo",
                )
                runtime.submissions.message_queue.put_nowait("/helix-link")
                assert await runtime.read_message(context) == "/helix-link"

                foreground.start(
                    "Helix MCP",
                    operation,
                    activity_kind="operation",
                    on_succeeded=lambda result: runtime.append_block(
                        text_block(f"Helix MCP {result}"),
                        kind="operation",
                    ),
                )
                wait_task = asyncio.create_task(foreground.wait())
                await started.wait()
                screen = await _render_next_frame(runtime)

                footer_text = "".join(
                    text for _style, text in runtime.screen._footer_fragments()
                )
                running_input_row = _absolute_window_row(
                    runtime,
                    screen,
                    runtime.screen.input.window,
                    rows=12,
                )

                assert "test-model" in footer_text
                assert "repo" in footer_text

                release.set()
                await wait_task
                runtime.discard_pending_submission()
                screen = await _render_next_frame(runtime)

                assert _absolute_window_row(
                    runtime,
                    screen,
                    runtime.screen.input.window,
                    rows=12,
                ) == running_input_row
            finally:
                release.set()
                if wait_task is not None:
                    await wait_task
                await runtime.close()


def test_tiny_window_uses_neutral_fallback_without_warning() -> None:
    runtime = TuiRuntime()
    screen = Screen()

    with patch.object(
        runtime.screen.application.output,
        "get_size",
        return_value=Size(rows=2, columns=12),
    ):
        assert runtime.terminal_height == 2

    runtime.screen.canvas.write_to_screen(
        screen,
        MouseHandlers(),
        WritePosition(xpos=0, ypos=0, width=12, height=2),
        parent_style="",
        erase_bg=False,
        z_index=None,
    )

    cells = [
        screen.data_buffer[row][column]
        for row in range(2)
        for column in range(12)
    ]
    assert "Window too small" not in "".join(cell.char for cell in cells)
    assert all("window-too-small" not in cell.style for cell in cells)


async def _wait_for_approval(runtime: TuiRuntime, approval_id: str) -> None:
    for _ in range(100):
        state = runtime.screen.approval.state
        if state is not None and state.presentation.context.approval_id == approval_id:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"approval was not shown: {approval_id}")


def _block_text(block) -> str:
    return "".join(text for _, text in block.fragments)
