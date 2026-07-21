# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from mind_app.tui.core.activity import TuiActivity
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.download import TuiUpgradeProgress


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
    assert "External MCP linking · 1 servers" in text
    assert "\n" in text

    inbuild["state"] = "ready"
    final_blocks = await activity.stop("inbuild")

    assert _block_text(final_blocks[0]) == "■ Helix MCP ready"
    assert "External MCP linking" in _block_text(rendered[-1])
    assert "Helix MCP" not in _block_text(rendered[-1])

    await activity.stop("external_mcp")


@pytest.mark.anyio
async def test_external_mcp_final_status_is_committed_to_document() -> None:
    runtime = TuiRuntime()
    snapshot = {
        "done": False,
        "items": [
            {"name": "github", "state": "linking", "tools": 0, "detail": ""},
            {"name": "docs", "state": "linking", "tools": 0, "detail": ""},
        ],
    }

    await runtime.begin_external_mcp_status(
        lambda: dict(snapshot),
        persist_final=True,
    )
    snapshot["done"] = True
    snapshot["items"] = [
        {"name": "github", "state": "ready", "tools": 7, "detail": ""},
        {"name": "docs", "state": "failed", "tools": 0, "detail": "timeout"},
    ]
    await runtime.end_activity_status("external_mcp")

    assert runtime.activity_block is None
    assert len(runtime.document.blocks) == 1
    final_text = _block_text(runtime.document.blocks[0].block)
    assert "External MCP ready · 1/2 servers · 7 tools" in final_text
    assert "docs: timeout" in final_text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("final_stage", "final_label"),
    [
        ("done", "Download complete"),
        ("failed", "Download failed"),
    ],
)
async def test_runtime_download_uses_progress_and_commits_final_status(
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

    state["stage"] = final_stage
    await progress.stop()

    assert runtime.activity_block is None
    final_text = _block_text(runtime.document.blocks[-1].block)
    assert final_label in final_text
    assert "helix-runtime.zip" in final_text


def test_activity_completion_keeps_bottom_layout_height_stable() -> None:
    runtime = TuiRuntime()
    line = FragmentBlock((("", "one line"),))

    runtime.set_activity_renderable(line)
    animated_height = runtime._visible_height()

    runtime.clear_activity_renderable()
    runtime.append_block(line)

    assert runtime._content_input_gap_height() == 2
    assert runtime._visible_height() == animated_height


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
