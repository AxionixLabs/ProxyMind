# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.styles import text_block
from mind_app.tui.session import loop


@pytest.mark.anyio
async def test_helix_link_stream_command_blocks_only_the_next_model_turn(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()
    link_started = asyncio.Event()
    release_link = asyncio.Event()
    second_turn_started = asyncio.Event()
    turn_messages: list[str] = []
    pref_config = {"primary": {"model": "test-model"}}

    mind = SimpleNamespace(
        task_event=task_event,
        stop_runtime_on_exit=False,
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        is_service_mcp_linked=lambda: False,
        require_service_runtime_context=lambda: object(),
        external_mcp=None,
    )

    async def monitor_exec_status(_runtime, _mind) -> None:
        return None

    async def link_helix_runtime(_mind) -> None:
        link_started.set()
        await release_link.wait()

    def run_model_turn(_mind, *, message_text, **_kwargs):
        async def execute() -> None:
            turn_messages.append(message_text)
            if len(turn_messages) == 1:
                first_turn_started.set()
                await release_first_turn.wait()
                return None
            second_turn_started.set()
            task_event.set()

        return execute()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)
    monkeypatch.setattr(loop, "link_helix_runtime", link_helix_runtime)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)
    monkeypatch.setattr(
        loop,
        "service_runtime_asset_missing",
        lambda _context: False,
    )

    runtime.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await first_turn_started.wait()

    runtime.input.buffer.text = "/helix-link"
    runtime._accept_input(runtime.input.buffer)
    await link_started.wait()

    runtime.input.buffer.text = "second"
    runtime._accept_input(runtime.input.buffer)
    release_first_turn.set()

    for _ in range(20):
        if runtime.foreground_active:
            break
        await asyncio.sleep(0)

    assert runtime.foreground_active
    assert not second_turn_started.is_set()

    release_link.set()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert second_turn_started.is_set()
    assert turn_messages == ["first", "second"]


@pytest.mark.anyio
async def test_quit_during_stream_barrier_cancels_background_startup(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()
    link_started = asyncio.Event()
    link_cancelled = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    mind = SimpleNamespace(
        task_event=task_event,
        stop_runtime_on_exit=False,
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        is_service_mcp_linked=lambda: False,
        require_service_runtime_context=lambda: object(),
        external_mcp=None,
    )

    async def link_helix_runtime(_mind) -> None:
        link_started.set()
        try:
            await asyncio.Future()
        finally:
            link_cancelled.set()

    def run_model_turn(_mind, **_kwargs):
        async def execute() -> None:
            turn_started.set()
            await release_turn.wait()

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(loop, "link_helix_runtime", link_helix_runtime)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)
    monkeypatch.setattr(
        loop,
        "service_runtime_asset_missing",
        lambda _context: False,
    )

    runtime.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await turn_started.wait()

    runtime.input.buffer.text = "/helix-link"
    runtime._accept_input(runtime.input.buffer)
    await link_started.wait()
    release_turn.set()

    for _ in range(20):
        if runtime.foreground_active:
            break
        await asyncio.sleep(0)

    runtime.input.buffer.text = "/quit"
    runtime._accept_input(runtime.input.buffer)
    await asyncio.wait_for(run_task, timeout=1.0)

    assert task_event.is_set()
    assert link_cancelled.is_set()
    assert not runtime.foreground_active


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/mcp start", "/mcp force"])
async def test_idle_mcp_start_queues_query_until_result_is_committed(
    monkeypatch,
    command,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    mcp_started = asyncio.Event()
    release_mcp = asyncio.Event()
    model_started = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    mind = SimpleNamespace(
        task_event=task_event,
        stop_runtime_on_exit=False,
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
    )

    async def run_mcp_action(_mind, action) -> None:
        assert action == command.split()[1]
        mcp_started.set()
        await release_mcp.wait()
        runtime.queue_background_block(text_block("External MCP ready"))

    def run_model_turn(_mind, *, message_text, **_kwargs):
        async def execute() -> None:
            assert message_text == "hi"
            model_started.set()
            task_event.set()

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(loop, "run_mcp_action", run_mcp_action)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.message_queue.put_nowait(command)
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await mcp_started.wait()

    assert runtime.foreground_active
    runtime.input.buffer.text = "hi"
    runtime._accept_input(runtime.input.buffer)

    assert runtime.queued_messages.active
    assert runtime.message_queue.empty()
    assert not model_started.is_set()

    release_mcp.set()
    await asyncio.wait_for(run_task, timeout=1.0)

    document = fragments_text(runtime.document.fragments(width=100))
    assert document.index("External MCP ready") < document.index("> hi")
    assert model_started.is_set()
