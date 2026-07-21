# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest

from mind_app.tui.core.activity import TuiActivity
from mind_app.tui.core.runtime import TuiRuntime


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


def _block_text(block) -> str:
    return "".join(text for _, text in block.fragments)
