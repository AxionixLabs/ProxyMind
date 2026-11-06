# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from frontends.runtime import (
    FrontendActivity,
    RuntimeDownloadProgress,
)
from frontends.terminal.renderers.download import download_progress_block


@pytest.mark.anyio
async def test_frontend_activity_routes_enabled_status_to_active_runtime() -> None:
    runtime = SimpleNamespace(
        active=True,
        begin_wait_status=AsyncMock(),
        begin_upload_status=AsyncMock(),
        begin_inbuild_status=AsyncMock(),
        begin_external_mcp_status=AsyncMock(),
        begin_compact_status=AsyncMock(),
        end_activity_status=AsyncMock(),
        freeze_activity_status=AsyncMock(),
    )
    fallback = SimpleNamespace(stop=AsyncMock())
    activity = FrontendActivity(runtime, fallback, enabled=True)
    snapshot = Mock()

    await activity.start_wait()
    await activity.start_upload(snapshot)
    await activity.start_inbuild(snapshot)
    await activity.start_external_mcp(snapshot)
    await activity.start_compact(snapshot)
    await activity.freeze("compact")
    await activity.stop("wait", settle=False)

    runtime.begin_wait_status.assert_awaited_once_with()
    runtime.begin_upload_status.assert_awaited_once_with(snapshot)
    runtime.begin_inbuild_status.assert_awaited_once_with(snapshot)
    runtime.begin_external_mcp_status.assert_awaited_once_with(snapshot)
    runtime.begin_compact_status.assert_awaited_once_with(snapshot)
    runtime.freeze_activity_status.assert_awaited_once_with("compact")
    runtime.end_activity_status.assert_awaited_once_with(
        "wait",
        settle=False,
    )
    fallback.stop.assert_not_awaited()


@pytest.mark.anyio
async def test_frontend_activity_uses_fallback_for_passive_runtime() -> None:
    runtime = SimpleNamespace(
        active=False,
        begin_wait_status=AsyncMock(),
    )
    fallback = SimpleNamespace(stop=AsyncMock())
    activity = FrontendActivity(runtime, fallback, enabled=False)

    await activity.start_wait()
    await activity.stop("wait")
    await activity.freeze("wait")

    runtime.begin_wait_status.assert_not_awaited()
    assert fallback.stop.await_count == 2


@pytest.mark.anyio
async def test_runtime_download_uses_live_state_and_releases_only_download_slot() -> None:
    runtime = SimpleNamespace(active=True, begin_download_status=AsyncMock(), end_activity_status=AsyncMock())
    progress = RuntimeDownloadProgress(runtime)
    state = {"name": "CUA", "stage": "downloading", "done": 1, "total": 2, "phase": 0.5}
    await progress.start(state)
    snapshot = runtime.begin_download_status.call_args.args[0]
    assert "CUA downloading" in download_progress_block(snapshot(), indicator="*").plain_text
    state["stage"] = "cancelled"
    assert snapshot()["stage"] == "cancelled"
    await progress.stop()
    runtime.end_activity_status.assert_awaited_once_with("download", settle=True)


@pytest.mark.anyio
async def test_runtime_download_is_silent_for_machine_output() -> None:
    runtime = SimpleNamespace(active=False, begin_download_status=AsyncMock(), end_activity_status=AsyncMock())
    progress = RuntimeDownloadProgress(runtime)
    await progress.start({"stage": "downloading"})
    await progress.stop()
    runtime.begin_download_status.assert_not_awaited()
    runtime.end_activity_status.assert_not_awaited()
