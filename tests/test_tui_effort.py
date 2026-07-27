# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.session import dispatch
from mind_app.tui.session import loop
from mind_core.permissions import preset_permissions


@pytest.mark.anyio
async def test_effort_command_updates_footer_context_immediately(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    stale_config = {
        "primary": {
            "model": "test-model",
            "reasoning_effort": "medium",
        },
    }
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        task_event=task_event,
        pref=SimpleNamespace(to_config=lambda: stale_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=stale_config),
    )

    async def monitor_exec_status(_runtime, _mind) -> None:
        return None

    def render_status(_application, _effort) -> None:
        task_event.set()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)
    monkeypatch.setattr(
        dispatch,
        "choose_model_effort",
        AsyncMock(return_value="high"),
    )
    monkeypatch.setattr(
        dispatch,
        "persist_primary_pref",
        AsyncMock(return_value={
            "model": "test-model",
            "reasoning_effort": "high",
        }),
    )
    monkeypatch.setattr(dispatch, "render_model_effort_status", render_status)

    runtime.submissions.message_queue.put_nowait("/effort")
    await loop.run_tui_loop(mind)

    assert runtime.context.model == "test-model high"
