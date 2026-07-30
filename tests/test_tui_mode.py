# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.mode import render_mode_status
from mind_app.tui.session import loop
from mind_core.permissions import preset_permissions


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        ("chat", "Chat"),
        ("fast", "Fast"),
        ("xtra", "Xtra"),
    ],
)
def test_mode_status_matches_other_command_statuses(mode, label) -> None:
    application = SimpleNamespace(emit=Mock())

    render_mode_status(application, mode)

    status, gap = (
        call.args[0]
        for call in application.emit.call_args_list
    )
    text = "".join(value for _style, value in status.renderable.fragments)

    assert status.type == "tui.mode.status"
    assert text == f"/{mode} · Mode: {label}"
    assert gap.type == "tui.gap"


@pytest.mark.anyio
async def test_mode_command_renders_status_and_updates_prompt_context(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    task_event = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        task_event=task_event,
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=application,
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
    )

    async def monitor_exec_status(_runtime, _mind) -> None:
        return None

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)

    runtime.submissions.message_queue.put_nowait("/fast")
    runtime.submissions.message_queue.put_nowait("/quit")
    await loop.run_tui_loop(mind)

    statuses = [
        call.args[0]
        for call in application.emit.call_args_list
        if call.args[0].type == "tui.mode.status"
    ]
    text = "".join(
        value
        for _style, value in statuses[0].renderable.fragments
    )

    assert text == "/fast · Mode: Fast"
    assert runtime.context.mode == "fast"


@pytest.mark.anyio
async def test_attachment_only_submission_starts_model_turn(monkeypatch) -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    task_event = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    turn_messages: list[str] = []
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        task_event=task_event,
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=application,
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        attach=SimpleNamespace(has_pending_attachments=lambda: True),
    )

    async def monitor_exec_status(_runtime, _mind) -> None:
        return None

    def run_model_turn(_mind, *, message_text, **_kwargs):
        async def execute() -> None:
            turn_messages.append(message_text)
            task_event.set()

        return execute()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait("")
    await loop.run_tui_loop(mind)

    assert turn_messages == [""]
    assert not runtime.document.blocks
