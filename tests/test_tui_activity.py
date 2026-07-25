# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from mind_app.tui.adapters.output import TuiOutputControl
from mind_app.tui.adapters.session import create_tui_output_session
from mind_app.tui.adapters.status import TuiStreamStatusControl
from mind_app.tui.core.activity import (
    TuiActivity,
    _download_block,
    _mcp_activity_block,
    _mcp_final_block,
    _upload_block,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.status_frames import SPINNER_FRAMES
from mind_app.tui.core.task_state import TuiTaskState
from mind_app.tui.features.helix import TuiUpgradeProgress
from mind_app.runtime.support.calling import run_mode_lifecycle
from mind_core.mcp_status import (
    external_mcp_status_view,
    inbuild_status_view,
)


def test_task_state_aggregates_turn_and_activity_sources() -> None:
    activity_running = [False]
    state = TuiTaskState(activity_running=lambda: activity_running[0])

    state.set_turn_running(True)
    activity_running[0] = True
    state.set_turn_running(False)

    assert state.running

    activity_running[0] = False

    assert not state.running

def test_tui_output_session_separates_content_and_event_status() -> None:
    runtime = TuiRuntime()
    session = create_tui_output_session("", runtime=runtime, animate=False)

    assert isinstance(session.control, TuiOutputControl)
    assert isinstance(session.status, TuiStreamStatusControl)
    assert session.status is not session.control
    assert not hasattr(session.control, "begin_reply_wait_status")


@pytest.mark.anyio
async def test_tui_turn_keeps_one_wait_until_runner_finishes() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    status = TuiStreamStatusControl()

    class MindStub(object):
        animate = False

        async def start_anim(self, mode: str) -> None:
            _ = mode
            await runtime.begin_wait_status()

        async def stop_anim(self, kind: str | None = None) -> None:
            await runtime.end_activity_status(kind)

        async def await_cleanup(self, awaitable):
            return await awaitable

    async def runner(*, mode: str) -> None:
        _ = mode
        assert runtime.activity.active

        await status.begin_reply_wait_status(delay_sec=0.0)
        await status.begin_tool_status()
        await output.append_assistant_delta("answer")
        await status.end_status()

        status_text = _block_text(
            FragmentBlock(tuple(runtime.screen._status_fragments()))
        )
        assert status_text.count("Thinking") == 1
        assert "\n" not in status_text

    mind = MindStub()
    mind.frontend = SimpleNamespace(runtime=runtime)

    await run_mode_lifecycle(mind, runner)

    assert not runtime.activity.active
    assert runtime.screen._status_fragments() == []


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
async def test_pause_wait_excludes_approval_time_from_elapsed() -> None:
    clock = [0.0]
    rendered = []
    activity = TuiActivity(
        set_renderable=rendered.append,
        clear_renderable=lambda: rendered.clear(),
    )

    with (
        patch("mind_app.tui.core.activity.time.perf_counter", side_effect=lambda: clock[0]),
        patch("mind_app.tui.core.activity.status_interval", return_value=0.001),
    ):
        await activity.begin_wait()
        await asyncio.sleep(0.005)
        clock[0] = 0.8
        await asyncio.sleep(0.005)
        assert "0.8s" in _block_text(rendered[-1])

        assert await activity.pause_wait()
        assert not rendered
        clock[0] = 10.0
        await activity.resume_wait()
        await asyncio.sleep(0.005)
        assert "0.8s" in _block_text(rendered[-1])

        clock[0] = 10.2
        await asyncio.sleep(0.005)
        assert "1.0s" in _block_text(rendered[-1])
        await activity.stop()


@pytest.mark.anyio
async def test_request_approval_resumes_wait_after_failure() -> None:
    calls = []

    class ActivityStub(object):
        async def pause_wait(self) -> bool:
            calls.append("pause")
            return True

        async def resume_wait(self) -> None:
            calls.append("resume")

    class ApprovalStub(object):
        def begin(self, approval):
            calls.append("approval.begin")
            return True

        async def wait(self):
            calls.append("approval.wait")
            raise RuntimeError("approval failed")

        async def dismiss(self):
            calls.append("approval.dismiss")

    runtime = TuiRuntime.__new__(TuiRuntime)
    runtime.activity = ActivityStub()
    runtime.screen = SimpleNamespace(approval=ApprovalStub())
    runtime.terminal_progress = SimpleNamespace(
        warning=lambda: calls.append("warning"),
        begin=lambda: calls.append("progress"),
    )

    with pytest.raises(RuntimeError, match="approval failed"):
        await runtime.request_approval({})

    assert calls == [
        "approval.begin",
        "warning",
        "pause",
        "approval.wait",
        "progress",
        "resume",
        "approval.dismiss",
    ]


@pytest.mark.anyio
async def test_request_approval_dismisses_card_when_pause_fails() -> None:
    calls = []

    class ActivityStub(object):
        async def pause_wait(self) -> bool:
            calls.append("pause")
            raise RuntimeError("pause failed")

        async def resume_wait(self) -> None:
            calls.append("resume")

    class ApprovalStub(object):
        def begin(self, approval):
            calls.append("approval.begin")
            return True

        async def wait(self):
            calls.append("approval.wait")
            return "accept"

        async def dismiss(self):
            calls.append("approval.dismiss")

    runtime = TuiRuntime.__new__(TuiRuntime)
    runtime.activity = ActivityStub()
    runtime.screen = SimpleNamespace(approval=ApprovalStub())
    runtime.terminal_progress = SimpleNamespace(
        warning=lambda: calls.append("warning"),
        begin=lambda: calls.append("progress"),
    )

    with pytest.raises(RuntimeError, match="pause failed"):
        await runtime.request_approval({})

    assert calls == [
        "approval.begin",
        "warning",
        "pause",
        "progress",
        "approval.dismiss",
    ]


@pytest.mark.anyio
async def test_request_approval_keeps_card_active_during_activity_handoff() -> None:
    runtime = TuiRuntime()
    active_during_handoff = []

    class ActivityStub(object):
        async def pause_wait(self) -> bool:
            active_during_handoff.append(runtime.screen.approval.active)
            await asyncio.sleep(0)
            return True

        async def resume_wait(self) -> None:
            active_during_handoff.append(runtime.screen.approval.active)
            await asyncio.sleep(0)

    runtime.activity = ActivityStub()
    task = asyncio.create_task(runtime.request_approval({
        "tool": "shell_command",
        "command": "pytest -q",
        "show_timer": False,
    }))
    await asyncio.sleep(0)

    assert runtime.screen.approval.active
    runtime.screen.approval.finish("accept")

    assert await task == "accept"
    assert active_during_handoff == [True, True]
    assert not runtime.screen.approval.active


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
    assert "\n" in text

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
    with patch("mind_app.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
        await runtime.end_activity_status("external_mcp")

    assert runtime.screen.activity_block is not None
    assert not runtime.task_running
    assert not runtime.document.blocks
    final_text = _block_text(runtime.screen.activity_block)
    assert "External MCP ready · 1/2 servers · 7 tools" in final_text
    assert "docs: timeout" not in final_text

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

    with patch("mind_app.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
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
        "filename": "helix-runtime.zip",
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
    assert expected in _block_text(runtime.screen.activity_block)
    assert not runtime.document.blocks


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
    with patch("mind_app.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
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
        "class:queue.marker",
        "class:input.notice",
        "class:shell.title.command",
        "class:ps.error",
    ):
        assert not style.get_attrs_for_style_str(name).bold

    assert style.get_attrs_for_style_str("class:queue.label").bold
    assert style.get_attrs_for_style_str("class:ps.title").bold


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


def _block_text(block) -> str:
    return "".join(text for _, text in block.fragments)
