# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from engine.errors import AppError
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.styles import text_block
from mind_app.tui.session import barriers
from mind_app.tui.session import dispatch
from mind_app.tui.session import loop
from mind_core.permissions import preset_permissions


@pytest.mark.anyio
async def test_foreground_result_is_rendered_before_barrier_release() -> None:
    runtime = TuiRuntime()
    events = []
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        await_cleanup=lambda awaitable: awaitable,
    )
    foreground = barriers.TuiForegroundTasks(runtime, mind)
    await runtime.begin_operation_status(
        lambda: {"summary": "Operation running"},
    )
    runtime.begin_command_layout()

    async def operation() -> str:
        events.append("business")
        return "ready"

    def render(result: str) -> None:
        assert result == "ready"
        assert runtime.foreground_active
        runtime.append_block(text_block("Operation ready"))
        assert runtime.screen.activity_block is None
        events.append("render")

    foreground.start(
        "operation",
        operation,
        activity_kind="operation",
        on_succeeded=render,
    )
    await foreground.wait()

    assert events == ["business", "render"]
    assert not runtime.command_layout_pending
    runtime.finish_command_layout()
    assert not runtime.foreground_active


@pytest.mark.anyio
async def test_foreground_wait_drains_tasks_started_by_tracked_operation() -> None:
    runtime = TuiRuntime()
    inner_started = asyncio.Event()
    release_inner = asyncio.Event()
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        await_cleanup=lambda awaitable: awaitable,
    )
    foreground = barriers.TuiForegroundTasks(runtime, mind)

    async def inner() -> None:
        inner_started.set()
        await release_inner.wait()

    async def outer() -> None:
        foreground.start("inner", inner)
        await asyncio.sleep(0)

    foreground.start("outer", outer)
    wait_task = asyncio.create_task(foreground.wait())
    await inner_started.wait()
    await asyncio.sleep(0)

    assert not wait_task.done()
    assert runtime.foreground_active

    release_inner.set()
    await wait_task

    assert not runtime.foreground_active


@pytest.mark.anyio
async def test_existing_background_task_cannot_consume_command_layout() -> None:
    runtime = TuiRuntime()
    started = asyncio.Event()
    release = asyncio.Event()

    async def background() -> None:
        started.set()
        await release.wait()
        runtime.append_block(text_block("background"))

    task = asyncio.create_task(background())
    await started.wait()

    runtime.begin_command_layout()
    release.set()
    await task

    assert runtime.command_layout_pending

    runtime.append_block(text_block("command result"))

    assert not runtime.command_layout_pending
    runtime.finish_command_layout()


@pytest.mark.anyio
async def test_stream_foreground_result_replaces_frozen_activity_at_flush(
) -> None:
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        await_cleanup=lambda awaitable: awaitable,
    )
    foreground = barriers.TuiForegroundTasks(runtime, mind)
    snapshot = {
        "done": False,
        "items": [{"name": "docs", "state": "linking", "tools": 0}],
    }

    runtime.set_execution_active(True)
    runtime.set_active_renderable(text_block("streaming answer"))
    await runtime.begin_external_mcp_status(lambda: dict(snapshot))

    async def operation() -> str:
        snapshot["done"] = True
        snapshot["items"] = [
            {"name": "docs", "state": "ready", "tools": 4},
        ]
        return "ready"

    foreground.start(
        "external",
        operation,
        activity_kind="external_mcp",
        on_succeeded=lambda _result: runtime.queue_background_block(
            text_block("External MCP ready"),
        ),
    )
    await foreground.wait()

    assert runtime.screen.activity_block is not None
    assert "External MCP ready" in fragments_text(
        runtime.screen.activity_block.fragments,
    )
    assert len(runtime._background_blocks) == 1

    runtime.commit_active_renderable(text_block("streaming answer"))
    runtime.set_execution_active(False)

    assert runtime.screen.activity_block is None
    assert not runtime._background_blocks
    assert "External MCP ready" in fragments_text(
        runtime.document.fragments(width=80),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ("failed", "cancelled"))
async def test_stream_foreground_terminal_outcome_keeps_activity_until_flush(
    outcome: str,
) -> None:
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        await_cleanup=lambda awaitable: awaitable,
    )
    foreground = barriers.TuiForegroundTasks(runtime, mind)
    started = asyncio.Event()

    runtime.set_execution_active(True)
    runtime.set_active_renderable(text_block("streaming answer"))
    await runtime.begin_operation_status(lambda: {"summary": "working"})

    async def operation() -> None:
        started.set()
        if outcome == "failed":
            raise AppError("failed")
        await asyncio.Future()

    foreground.start(
        outcome,
        operation,
        activity_kind="operation",
        on_failed=lambda _error: runtime.queue_background_block(
            text_block("Operation failed"),
        ),
        on_cancelled=lambda: runtime.queue_background_block(
            text_block("Operation cancelled"),
        ),
    )
    wait_task = asyncio.create_task(foreground.wait())
    await started.wait()
    if outcome == "cancelled":
        foreground.cancel()
    await wait_task

    assert runtime.screen.activity_block is not None
    assert len(runtime._background_blocks) == 1

    runtime.commit_active_renderable(text_block("streaming answer"))
    runtime.set_execution_active(False)

    assert runtime.screen.activity_block is None
    assert f"Operation {outcome}" in fragments_text(
        runtime.document.fragments(width=80),
    )


@pytest.mark.anyio
async def test_replace_transcript_releases_deferred_activity_handoff() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.set_active_renderable(text_block("streaming answer"))
    await runtime.begin_operation_status(lambda: {"summary": "working"})

    with runtime.activity_handoff("operation"):
        runtime.queue_background_block(text_block("Operation ready"))

    assert runtime.screen.activity_block is not None
    assert len(runtime._background_blocks) == 1

    runtime.replace_transcript(())

    assert runtime.screen.activity_block is None
    assert not runtime._background_blocks
    await runtime.activity.clear()


@pytest.mark.anyio
async def test_cancel_cleanup_failure_still_releases_activity_handoff() -> None:
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        await_cleanup=lambda awaitable: awaitable,
    )
    foreground = barriers.TuiForegroundTasks(runtime, mind)
    started = asyncio.Event()
    cancelled = Mock()

    await runtime.begin_operation_status(lambda: {"summary": "working"})

    async def operation() -> None:
        started.set()
        await asyncio.Future()

    async def failed_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    def render_cancelled() -> None:
        cancelled()
        runtime.append_block(text_block("Operation cancelled"))

    foreground.start(
        "cancel-cleanup",
        operation,
        cancel_cleanup=failed_cleanup,
        activity_kind="operation",
        on_cancelled=render_cancelled,
    )
    wait_task = asyncio.create_task(foreground.wait())
    await started.wait()
    foreground.cancel()
    await wait_task

    cancelled.assert_called_once_with()
    assert runtime.screen.activity_block is None
    assert "Operation cancelled" in fragments_text(
        runtime.document.fragments(width=80),
    )


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
        subscription=SimpleNamespace(current=None),
        permissions=preset_permissions("auto"),
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            require_context=lambda: object(),
            cancel_startup=AsyncMock(),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
            replace_pending_attachments=lambda _items: None,
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        is_service_mcp_linked=lambda: False,
        external_mcp=SimpleNamespace(current=None),
        stop_anim=AsyncMock(),
        await_cleanup=lambda awaitable: awaitable,
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
    monkeypatch.setattr(barriers, "link_helix_runtime", link_helix_runtime)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)
    monkeypatch.setattr(
        barriers,
        "service_runtime_asset_missing",
        lambda _context: False,
    )

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await first_turn_started.wait()

    runtime.screen.input.buffer.text = "/helix-link"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await link_started.wait()

    runtime.screen.input.buffer.text = "second"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
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
async def test_stream_settings_settle_before_queued_model_turn(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()
    settings_started = asyncio.Event()
    release_settings = asyncio.Event()
    second_turn_started = asyncio.Event()
    initial_permissions = preset_permissions("auto")
    updated_permissions = preset_permissions("full-access")
    turn_permissions = []
    pref_config = {"primary": {"model": "test-model"}}

    mind = SimpleNamespace(
        subscription=SimpleNamespace(current=None),
        permissions=initial_permissions,
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
            replace_pending_attachments=lambda _items: None,
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        apply_permissions=Mock(return_value=updated_permissions),
        external_mcp=SimpleNamespace(current=None),
        stop_anim=AsyncMock(),
    )

    async def choose_permissions(_runtime, current):
        assert current is initial_permissions
        settings_started.set()
        await release_settings.wait()
        return updated_permissions

    def run_model_turn(_mind, *, permissions, **_kwargs):
        async def execute() -> None:
            turn_permissions.append(permissions)
            if len(turn_permissions) == 1:
                first_turn_started.set()
                await release_first_turn.wait()
                return None
            second_turn_started.set()
            task_event.set()

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(dispatch, "choose_permissions_mode", choose_permissions)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await first_turn_started.wait()

    runtime.screen.input.buffer.text = "/permissions"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await settings_started.wait()

    runtime.screen.input.buffer.text = "second"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    release_first_turn.set()

    for _ in range(20):
        if runtime.foreground_active:
            break
        await asyncio.sleep(0)

    assert runtime.foreground_active
    assert not second_turn_started.is_set()

    release_settings.set()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert second_turn_started.is_set()
    assert turn_permissions == [initial_permissions, updated_permissions]
    mind.apply_permissions.assert_called_once_with(updated_permissions)


@pytest.mark.anyio
async def test_stream_interactive_panel_closes_before_queued_model_turn(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()
    panel_started = asyncio.Event()
    release_panel = asyncio.Event()
    second_turn_started = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    turn_messages = []

    mind = SimpleNamespace(
        subscription=SimpleNamespace(current=None),
        permissions=preset_permissions("auto"),
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
            replace_pending_attachments=lambda _items: None,
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        external_mcp=SimpleNamespace(current=None),
        stop_anim=AsyncMock(),
    )

    async def manage_agents(_runtime, _mind) -> None:
        panel_started.set()
        await release_panel.wait()

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

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(dispatch, "manage_agents", manage_agents)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await first_turn_started.wait()

    runtime.screen.input.buffer.text = "/agent"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await panel_started.wait()

    runtime.screen.input.buffer.text = "second"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    release_first_turn.set()

    for _ in range(20):
        if runtime.foreground_active:
            break
        await asyncio.sleep(0)

    assert runtime.foreground_active
    assert not second_turn_started.is_set()

    release_panel.set()
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
        subscription=SimpleNamespace(current=None),
        permissions=preset_permissions("auto"),
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            require_context=lambda: object(),
            cancel_startup=AsyncMock(),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        is_service_mcp_linked=lambda: False,
        external_mcp=SimpleNamespace(current=None),
        stop_anim=AsyncMock(),
        await_cleanup=lambda awaitable: awaitable,
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
    monkeypatch.setattr(barriers, "link_helix_runtime", link_helix_runtime)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)
    monkeypatch.setattr(
        barriers,
        "service_runtime_asset_missing",
        lambda _context: False,
    )

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await turn_started.wait()

    runtime.screen.input.buffer.text = "/helix-link"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await link_started.wait()
    release_turn.set()

    for _ in range(20):
        if runtime.foreground_active:
            break
        await asyncio.sleep(0)

    runtime.screen.input.buffer.text = "/quit"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await asyncio.wait_for(run_task, timeout=1.0)

    assert task_event.is_set()
    assert link_cancelled.is_set()
    assert not runtime.foreground_active


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    ["/mcp start", "/mcp force", "/mcp restart"],
)
async def test_idle_mcp_start_commits_result_before_next_query(
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
        subscription=SimpleNamespace(current=None),
        permissions=preset_permissions("auto"),
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        external_mcp=SimpleNamespace(current=None),
        stop_anim=AsyncMock(),
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
    monkeypatch.setattr(barriers, "run_mcp_action", run_mcp_action)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait(command)
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await mcp_started.wait()

    assert runtime.foreground_active
    runtime.screen.input.buffer.text = "hi"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert not model_started.is_set()

    release_mcp.set()
    await asyncio.wait_for(run_task, timeout=1.0)

    document = fragments_text(runtime.document.fragments(width=100))
    assert document.index("External MCP ready") < document.index("› hi")
    assert command not in document
    assert model_started.is_set()


@pytest.mark.anyio
async def test_ctrl_c_cancels_helix_foreground_task_without_exiting(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    task_event = asyncio.Event()
    link_started = asyncio.Event()
    link_cancelled = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    views = []
    pref_config = {"primary": {"model": "test-model"}}

    async def link_helix_runtime(_mind) -> None:
        link_started.set()
        try:
            await asyncio.Future()
        finally:
            link_cancelled.set()

    async def cancel_startup_cleanup() -> None:
        cleanup_started.set()
        await release_cleanup.wait()

    cancel_startup = AsyncMock(side_effect=cancel_startup_cleanup)
    mind = SimpleNamespace(
        subscription=SimpleNamespace(current=None),
        permissions=preset_permissions("auto"),
        task_event=task_event,
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            cancel_startup=cancel_startup,
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=views.append),
        ),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        native_coding=SimpleNamespace(reset_patch_diff=Mock()),
        set_history_workspace=Mock(),
        stop_anim=AsyncMock(),
        await_cleanup=lambda awaitable: awaitable,
    )

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        barriers,
        "link_helix_runtime",
        link_helix_runtime,
    )

    runtime.submissions.message_queue.put_nowait("/helix-link")
    run_task = asyncio.create_task(loop.run_tui_loop(mind))
    await link_started.wait()

    runtime.submissions.interrupt_input()
    await link_cancelled.wait()
    await cleanup_started.wait()

    assert runtime.foreground_active
    release_cleanup.set()
    for _ in range(20):
        if not runtime.foreground_active:
            break
        await asyncio.sleep(0)

    assert not runtime.foreground_active
    assert not run_task.done()
    cancel_startup.assert_awaited_once_with()
    assert any(view.type == "tui.helix.interrupted" for view in views)
    interrupted = next(
        view for view in views
        if view.type == "tui.helix.interrupted"
    )
    assert fragments_text(interrupted.renderable.fragments) == (
        "• Helix MCP · interrupted"
    )

    runtime.submissions.message_queue.put_nowait("/quit")
    await asyncio.wait_for(run_task, timeout=1.0)
