# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.application.turns.run_result import RunResult
from agent.ports import (
    OutputSurfaceContext,
    ProtocolCommandClient,
)
from agent.ports.mcp_runtime import (
    McpControlResult,
    McpRuntimeSnapshot,
)
from agent.domain.policies import preset_permissions
from agent.application import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.render import fragments_text
from frontends.tui.core.styles import text_block
from frontends.tui.session import barriers
from frontends.tui.session import dispatch
from frontends.tui.session import loop
from infrastructure.errors import AppError


def _settings(
    pref_config: dict[str, object],
    *,
    permissions=None,
    apply_permissions=None,
) -> SimpleNamespace:
    """构造 TUI 流测试使用的设置会话端口。"""
    active_permissions = permissions or preset_permissions("auto")
    apply = apply_permissions or Mock(return_value=active_permissions)
    return SimpleNamespace(
        config=SimpleNamespace(load=lambda: {}),
        permissions=active_permissions,
        preference_config=lambda: pref_config,
        fresh_preferences=AsyncMock(return_value=pref_config),
        apply_permissions=apply,
    )


def _lifecycle(stop_event: asyncio.Event | None = None) -> SimpleNamespace:
    """构造共享同一停止信号的应用生命周期。"""
    event = stop_event or asyncio.Event()

    async def await_cleanup(awaitable):
        return await awaitable

    def request_stop(*, exit_code=None) -> None:
        _ = exit_code
        event.set()

    return SimpleNamespace(
        stop_event=event,
        request_stop=request_stop,
        await_cleanup=await_cleanup,
    )


def _activity() -> SimpleNamespace:
    """构造流命令测试使用的活动展示端口。"""
    return SimpleNamespace(
        enabled=True,
        stop=AsyncMock(),
    )


def _attachments() -> SimpleNamespace:
    """构造会话循环使用的附件状态端口。"""
    return SimpleNamespace(
        has_pending_attachments=lambda: False,
        pending_attachments_snapshot=lambda: [],
        replace_pending_attachments=lambda _items: None,
    )


def _configuration_service_url() -> str:
    """返回流命令测试使用的空配置服务地址。"""
    return ""


@pytest.fixture(autouse=True)
def frozen_environment_snapshot(monkeypatch) -> None:
    """固定 TUI 命令提交时捕获的环境事实。"""
    monkeypatch.setattr(
        loop,
        "capture_active_turn_environment",
        Mock(return_value={"snapshot_id": "envsnap_tui"}),
    )


@pytest.fixture(autouse=True)
def injected_turn_application(monkeypatch) -> None:
    """为 TUI 流命令单测注入显式 Session runtime。"""
    monkeypatch.setattr(
        loop,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


@pytest.mark.anyio
async def test_foreground_result_is_rendered_before_barrier_release() -> None:
    runtime = TuiRuntime()
    events = []
    host = SimpleNamespace(
        lifecycle=_lifecycle(),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)
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
    host = SimpleNamespace(
        lifecycle=_lifecycle(),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)

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
    host = SimpleNamespace(
        lifecycle=_lifecycle(),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)
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
    host = SimpleNamespace(
        lifecycle=_lifecycle(),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)
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
async def test_long_tool_keeps_ps_and_stop_available_during_turn() -> None:
    """验证长工具活动期间可以查看并停止持久后台终端。"""
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (100, 24)
    runtime.set_execution_active(True)
    context = OutputSurfaceContext(
        surface_id="surface_long_tool_commands",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        agent_id="root",
    )
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    activity = TurnActivityProjector(context, session.activity)
    await activity.tool_started(
        "exec_long",
        "client",
        name="exec_command",
    )
    assert "Thinking" in fragments_text(runtime.screen._status_fragments())

    process = {
        "session_id": "exec_long",
        "command": "python -m pytest",
        "origin": "tool",
        "status": "running",
    }
    listing = {"count": 1, "items": [process]}
    stopped = {
        "ok": True,
        "requested": 1,
        "stopped": 1,
        "failed": 0,
        "items": [process],
        "failures": [],
    }
    coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value=listing),
        exec_session_output_snapshot=AsyncMock(return_value={
            **process,
            "ok": True,
            "output_lines": ["collecting tests"],
        }),
        stop_exec_sessions=AsyncMock(return_value=stopped),
    )
    views = []
    host = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        workspace_runtime=SimpleNamespace(coding=coding),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)
    dispatcher = dispatch.TuiCommandDispatcher(
        host,
        runtime,
        SimpleNamespace(),
        foreground,
        protocol_client=Mock(spec=ProtocolCommandClient),
    )
    runtime.set_process_status_label(
        "1 background terminal running · /ps to view · /stop to close"
    )

    status = fragments_text(runtime.screen._status_fragments())
    assert (
        "Thinking (0s • esc to interrupt)"
        " · 1 background terminal running"
    ) in status
    assert "exec_command" not in status

    assert dispatcher.handle_stream_command("/ps", Mock(return_value=None))
    ps_task = dispatcher._local_tasks["ps"]
    await ps_task
    transcript = fragments_text(runtime.document.fragments(width=100))
    assert "/ps\n\nBackground terminals" in transcript
    assert "python -m pytest\n    ↳ collecting tests" in transcript

    assert dispatcher.handle_stream_command("/stop", Mock(return_value=None))
    await foreground.wait()

    coding.stop_exec_sessions.assert_awaited_once_with(
        session_ids=("exec_long",),
    )
    assert runtime.screen.process_status.label == ""
    assert "Thinking" in fragments_text(runtime.screen._status_fragments())
    assert any(view.type == "tui.exec.stopping" for view in views)

    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_cancel_cleanup_failure_still_releases_activity_handoff() -> None:
    runtime = TuiRuntime()
    host = SimpleNamespace(
        lifecycle=_lifecycle(),
    )
    foreground = barriers.TuiForegroundTasks(runtime, host)
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

    host = SimpleNamespace(
        configuration_service_url=_configuration_service_url,
        subscription=SimpleNamespace(current=None),
        settings=_settings(pref_config),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            require_context=lambda: object(),
            cancel_startup=AsyncMock(),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=_attachments(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        execution=SimpleNamespace(
            is_service_linked=lambda: False,
            external_mcp=SimpleNamespace(current=None),
        ),
    )

    async def monitor_exec_status(_runtime, _host) -> None:
        return None

    async def link_helix_runtime(_host) -> None:
        link_started.set()
        await release_link.wait()

    def run_model_turn(_host, *_ports, message_text, **_kwargs):
        async def execute() -> RunResult:
            turn_messages.append(message_text)
            if len(turn_messages) == 1:
                first_turn_started.set()
                await release_first_turn.wait()
                return RunResult(status="completed")
            second_turn_started.set()
            task_event.set()
            return RunResult(status="completed")

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
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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

    host = SimpleNamespace(
        configuration_service_url=_configuration_service_url,
        subscription=SimpleNamespace(current=None),
        settings=_settings(
            pref_config,
            permissions=initial_permissions,
            apply_permissions=Mock(return_value=updated_permissions),
        ),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=_attachments(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        execution=SimpleNamespace(
            external_mcp=SimpleNamespace(current=None),
        ),
    )

    async def choose_permissions(_runtime, current):
        assert current is initial_permissions
        settings_started.set()
        await release_settings.wait()
        return updated_permissions

    def run_model_turn(_host, *_ports, permissions, **_kwargs):
        async def execute() -> RunResult:
            turn_permissions.append(permissions)
            if len(turn_permissions) == 1:
                first_turn_started.set()
                await release_first_turn.wait()
                return RunResult(status="completed")
            second_turn_started.set()
            task_event.set()
            return RunResult(status="completed")

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(dispatch, "choose_permissions_mode", choose_permissions)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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
    host.settings.apply_permissions.assert_called_once_with(updated_permissions)


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

    host = SimpleNamespace(
        configuration_service_url=_configuration_service_url,
        subscription=SimpleNamespace(current=None),
        settings=_settings(pref_config),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        attach=_attachments(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        execution=SimpleNamespace(
            external_mcp=SimpleNamespace(current=None),
        ),
    )

    async def manage_agents(_runtime, _host) -> None:
        panel_started.set()
        await release_panel.wait()

    def run_model_turn(_host, *_ports, message_text, **_kwargs):
        async def execute() -> RunResult:
            turn_messages.append(message_text)
            if len(turn_messages) == 1:
                first_turn_started.set()
                await release_first_turn.wait()
                return RunResult(status="completed")
            second_turn_started.set()
            task_event.set()
            return RunResult(status="completed")

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(dispatch, "manage_agents", manage_agents)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait("first")
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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
    host = SimpleNamespace(
        configuration_service_url=_configuration_service_url,
        attach=_attachments(),
        subscription=SimpleNamespace(current=None),
        settings=_settings(pref_config),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            require_context=lambda: object(),
            cancel_startup=AsyncMock(),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        execution=SimpleNamespace(
            is_service_linked=lambda: False,
            external_mcp=SimpleNamespace(current=None),
        ),
    )

    async def link_helix_runtime(_host) -> None:
        link_started.set()
        try:
            await asyncio.Future()
        finally:
            link_cancelled.set()

    def run_model_turn(_host, *_ports, **_kwargs):
        async def execute() -> RunResult:
            turn_started.set()
            await release_turn.wait()
            return RunResult(status="completed")

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
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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
    host = SimpleNamespace(
        configuration_service_url=_configuration_service_url,
        attach=_attachments(),
        subscription=SimpleNamespace(current=None),
        settings=_settings(pref_config),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        execution=SimpleNamespace(
            external_mcp=SimpleNamespace(current=None, snapshot=McpRuntimeSnapshot("instance", "/workspace", ())),
        ),
    )

    async def run_mcp_action(_host, request) -> McpControlResult:
        assert request.action == command.split()[1]
        mcp_started.set()
        await release_mcp.wait()
        runtime.queue_background_block(text_block("External MCP ready"))
        return McpControlResult(request, ())

    def run_model_turn(_host, *_ports, message_text, **_kwargs):
        async def execute() -> RunResult:
            assert message_text == "hi"
            model_started.set()
            task_event.set()
            return RunResult(status="completed")

        return execute()

    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(barriers, "run_mcp_action", run_mcp_action)
    monkeypatch.setattr(loop, "run_tui_model_turn", run_model_turn)

    runtime.submissions.message_queue.put_nowait(command)
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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

    async def link_helix_runtime(_host) -> None:
        link_started.set()
        try:
            await asyncio.Future()
        finally:
            link_cancelled.set()

    async def cancel_startup_cleanup() -> None:
        cleanup_started.set()
        await release_cleanup.wait()

    cancel_startup = AsyncMock(side_effect=cancel_startup_cleanup)
    host = SimpleNamespace(
        history_workspace="D:/workspace",
        configuration_service_url=_configuration_service_url,
        attach=_attachments(),
        subscription=SimpleNamespace(current=None),
        settings=_settings(pref_config),
        lifecycle=_lifecycle(task_event),
        activity=_activity(),
        service_runtime=SimpleNamespace(
            request_termination_on_close=Mock(),
            cancel_startup=cancel_startup,
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=views.append),
        ),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        set_history_workspace=Mock(),
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
    run_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
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
