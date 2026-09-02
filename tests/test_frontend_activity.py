# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from frontends.runtime import FrontendActivity


@pytest.mark.anyio
async def test_frontend_activity_routes_enabled_status_to_active_runtime() -> None:
    runtime = SimpleNamespace(
        active=True,
        begin_wait_status=AsyncMock(),
        begin_upload_status=AsyncMock(),
        begin_inbuild_status=AsyncMock(),
        begin_external_mcp_status=AsyncMock(),
        begin_compact_status=AsyncMock(),
        finish_turn_wait=Mock(),
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
    runtime.finish_turn_wait.assert_not_called()
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
