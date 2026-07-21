# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from mind_app.tui.adapters.output import TuiOutputControl
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
from mind_app.tui.features.download import TuiUpgradeProgress
from mind_app.tui.session.turn import upload_pending_tui_attachments
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method_name", "argument"),
    (
        ("begin_tool_status", None),
        ("begin_custom_tool_status", "running shell_command"),
    ),
)
async def test_tool_call_clears_wait_without_starting_tool_animation(
    method_name: str,
    argument: str | None,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output.status_state.set_status("thinking", family="wait")
    runtime.set_status_renderable(output.status_state.render_block())
    await output.status_driver.start(reset_phase=True)

    method = getattr(output, method_name)
    if argument is None:
        await method()
    else:
        await method(argument)

    assert not output.status_state.visible
    assert output.status_driver.task is None
    assert output._pending_status_task is None
    assert runtime.status_block is None


@pytest.mark.anyio
async def test_default_reply_wait_does_not_enter_layout_immediately() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output.assistant.text = "plain response"
    output._render_active(cursor=False)
    initial_height = runtime._visible_height()

    try:
        await output.begin_reply_wait_status()
        await asyncio.sleep(0.01)

        assert output._pending_status_task is not None
        assert runtime.status_block is None
        assert runtime._visible_height() == initial_height
    finally:
        await output.end_status()


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
        async def request(self, approval):
            calls.append("approval")
            raise RuntimeError("approval failed")

    runtime = TuiRuntime.__new__(TuiRuntime)
    runtime.activity = ActivityStub()
    runtime.approval = ApprovalStub()

    with pytest.raises(RuntimeError, match="approval failed"):
        await runtime.request_approval({})

    assert calls == ["pause", "approval", "resume"]


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

    assert runtime.activity_block is not None
    assert not runtime.task_running
    assert not runtime.document.blocks
    final_text = _block_text(runtime.activity_block)
    assert "External MCP ready · 1/2 servers · 7 tools" in final_text
    assert "docs: timeout" not in final_text

    await asyncio.sleep(0.1)
    assert runtime.activity_block is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("final_stage", "final_label"),
    [
        ("done", "Download complete"),
        ("failed", "Download failed"),
    ],
)
async def test_runtime_download_uses_progress_and_settles_in_activity_region(
    final_stage: str,
    final_label: str,
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

    active_text = _block_text(runtime.activity_block)
    assert "downloading" in active_text
    assert "50.0%" in active_text
    assert "5.0 MB / 10.0 MB · 2.0 MB/s" in active_text
    assert "Internal MCP" not in active_text
    assert all(
        "bold" not in style
        for style, text in runtime.activity_block.fragments
        if text.strip()
    )

    state["stage"] = final_stage
    with patch("mind_app.tui.core.activity.ACTIVITY_SETTLE_SEC", 0.001):
        await progress.stop()

    assert runtime.activity_block is not None
    assert not runtime.document.blocks
    final_text = _block_text(runtime.activity_block)
    assert final_label in final_text
    assert "helix-runtime.zip" in final_text
    assert any(
        "bold" in style and text == final_label.removeprefix("Download ")
        for style, text in runtime.activity_block.fragments
    )

    await asyncio.sleep(0.1)
    assert runtime.activity_block is None


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

    assert runtime.activity_block is not None
    assert "Attach done" in _block_text(runtime.activity_block)
    assert not runtime.document.blocks

    await asyncio.sleep(0.1)
    assert runtime.activity_block is None


@pytest.mark.anyio
async def test_successful_tui_upload_does_not_emit_transcript_summary() -> None:
    emitted = []
    snapshots = []

    class AttachStub(object):
        def pending_attachments_snapshot(self):
            return [{"name": "report.pdf", "size": 10}]

        async def upload_pending_attachments(self, *, progress_callback):
            await progress_callback({
                "phase": "uploading",
                "done": True,
                "item_index": 1,
                "item_total": 1,
                "filename": "report.pdf",
                "aggregate_total_bytes": 10,
            })
            return [{"id": "attachment-1"}]

    class ApplicationStub(object):
        def emit(self, view):
            emitted.append(view)

    class MindStub(object):
        attach = AttachStub()
        frontend = type("FrontendStub", (), {"application": ApplicationStub()})()

        async def start_upload_anim(self, snapshot):
            snapshots.append(snapshot)

        async def stop_anim(self, kind=None):
            snapshots.append((kind, snapshots[0]()))

        async def await_cleanup(self, awaitable):
            await awaitable

    uploaded = await upload_pending_tui_attachments(MindStub())

    assert uploaded == [{"id": "attachment-1"}]
    assert emitted == []
    assert snapshots[-1][0] == "upload"
    assert snapshots[-1][1]["event"]["done"] is True


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
    assert "External MCP linking" in _block_text(runtime.activity_block)

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


def test_activity_and_tool_status_share_the_status_region() -> None:
    runtime = TuiRuntime()

    runtime.set_activity_renderable(FragmentBlock((("", "External MCP linking"),)))
    runtime.set_status_renderable(FragmentBlock((("", "Running shell command"),)))

    text = _block_text(FragmentBlock(tuple(runtime._status_fragments())))
    assert text == "External MCP linking\nRunning shell command"
    assert runtime._status_height() == 2


def test_runtime_body_styles_do_not_use_bold() -> None:
    style = TuiRuntime()._style()

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

    input_height = runtime._input_stack_height()
    runtime.set_activity_renderable(line)
    runtime.clear_activity_renderable()

    assert runtime._input_stack_height() == input_height


def test_footer_reserves_one_blank_row() -> None:
    runtime = TuiRuntime()

    assert runtime.FOOTER_GAP_HEIGHT == 1
    assert runtime._footer_height() == 2
    assert runtime._input_stack_height() == runtime._input_height() + 2


def test_tiny_window_uses_neutral_fallback_without_warning() -> None:
    runtime = TuiRuntime()
    screen = Screen()

    with patch.object(
        runtime.application.output,
        "get_size",
        return_value=Size(rows=2, columns=12),
    ):
        assert runtime.terminal_height == 2

    runtime.canvas.write_to_screen(
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
