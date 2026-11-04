# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.application.turns.commands import (
    RemoteTurnRecovery,
    SessionRecoveryResult,
    TurnApplication,
)
from agent.domain import (
    RecoveryAction,
    RunStatus,
)
from agent.domain.policies import preset_permissions
from agent.harness.process_lifecycle import ProcessLifecycle
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.ports import (
    ProtocolCommandClient,
    RunSnapshot,
)
from agent.protocol import SubmitTurnCommand
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.contracts.keyboard import enhanced_key_token
from frontends.tui.core.models import (
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.submission import TuiInterruptRequested
from frontends.tui.features.model import choose_model_effort
from frontends.tui.features.model import next_model_reasoning_effort
from frontends.tui.features.model import ReasoningEffortDirection
from frontends.tui.features.model import reasoning_effort_boundary_status_block
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.session import dispatch
from frontends.tui.session import loop
from frontends.tui.session.state import TuiSessionState


@pytest.fixture(autouse=True)
def frozen_environment_snapshot(monkeypatch) -> None:
    """固定 TUI 命令提交时捕获的环境事实。"""
    monkeypatch.setattr(
        loop,
        "capture_active_turn_environment",
        Mock(return_value={"snapshot_id": "envsnap_tui"}),
    )
    monkeypatch.setattr(
        loop,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


def _pending_recovery(command: SubmitTurnCommand) -> RunSnapshot:
    """构造一项仍需远端权威终态裁决的本地 Run。"""
    return RunSnapshot(
        command=command,
        status=RunStatus.RECONCILIATION_REQUIRED,
        sequence=3,
        snapshot_version=1,
        effect_id="",
        effect_status="reconciliation_required",
        recovery_action=RecoveryAction.RECONCILE,
        updated_at="2026-09-04T00:00:00Z",
    )


@pytest.mark.anyio
async def test_tui_uses_durable_runtime_composition_for_real_layout(
    monkeypatch,
    tmp_path,
) -> None:
    close = AsyncMock()
    application = SimpleNamespace(close=close)
    open_application = Mock(return_value=application)
    run_loop = AsyncMock()
    db_path = tmp_path / "runtime.db"
    monkeypatch.setattr(loop, "agent_runtime_db_path", lambda: db_path)
    monkeypatch.setattr(loop, "_run_tui_loop", run_loop)

    await loop.run_tui_loop(
        SimpleNamespace(application_layout=object()),
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_application_factory=open_application,
        turn_runner=AsyncMock(),
    )

    open_application.assert_called_once_with(db_path)
    assert run_loop.await_args.kwargs["turn_application"] is application
    assert run_loop.await_args.kwargs["local_session_id"] is None
    close.assert_awaited_once_with(cancel_running=True)


@pytest.mark.anyio
async def test_tui_requires_explicit_turn_application_factory_for_real_layout() -> None:
    with pytest.raises(
        RuntimeError,
        match="TUI turn application factory is required",
    ):
        await loop.run_tui_loop(
            SimpleNamespace(application_layout=object()),
            protocol_client=Mock(spec=ProtocolCommandClient),
        )


@pytest.mark.anyio
async def test_tui_requires_explicit_root_turn_runner() -> None:
    with pytest.raises(
        RuntimeError,
        match="TUI root turn runner is required",
    ):
        await loop.run_tui_loop(
            SimpleNamespace(),
            protocol_client=Mock(spec=ProtocolCommandClient),
        )


@pytest.mark.anyio
async def test_durable_queued_recovery_returns_input_to_composer() -> None:
    runtime = TuiRuntime()
    runtime.screen.input.buffer.text = "current draft"
    attachment_state = SimpleNamespace(
        pending_attachments_snapshot=Mock(return_value=[{
            "filename": "current.txt",
        }]),
        replace_pending_attachments=Mock(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={"current": True}),
        replace_pending_prompt_extras=Mock(),
    )
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="queued before restart",
        attachments=({"filename": "queued.txt"},),
        extras={"queued": True},
    )
    turn_application = AsyncMock()
    turn_application.reconcile_remote_session.return_value = (
        SessionRecoveryResult(
            pending=(),
            restore_commands=(command,),
            resolved_run_ids=(command.run_id,),
        )
    )
    host = SimpleNamespace(
        attach=attachment_state,
        lifecycle=ProcessLifecycle(),
    )

    ready = await loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    )

    assert ready is True
    assert runtime.screen.input.buffer.text == (
        "queued before restart\ncurrent draft"
    )
    attachment_state.replace_pending_attachments.assert_called_once_with((
        {"filename": "queued.txt"},
        {"filename": "current.txt"},
    ))
    state.replace_pending_prompt_extras.assert_called_once_with({
        "queued": True,
        "current": True,
    })


@pytest.mark.anyio
async def test_durable_recovery_observes_remote_turn_before_opening_gate(
    monkeypatch,
) -> None:
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="active turn",
    )
    pending = _pending_recovery(command)
    observed = RemoteTurnRecovery(
        snapshot=pending,
        request=Mock(),
        replay_target_seq=8,
    )
    turn_application = AsyncMock()
    turn_application.reconcile_remote_session.side_effect = (
        SessionRecoveryResult(
            pending=(pending,),
            restore_commands=(),
            resolved_run_ids=(),
            observe_turns=(observed,),
        ),
        SessionRecoveryResult((), (), (command.run_id,)),
    )
    execute_recovery = AsyncMock(return_value=loop._TurnExecutionOutcome(
        settled=True,
        exit_requested=False,
    ))
    monkeypatch.setattr(loop, "_execute_tui_recovered_turn", execute_recovery)
    runtime = TuiRuntime()
    host = SimpleNamespace(
        attach=SimpleNamespace(
            pending_attachments_snapshot=Mock(return_value=[]),
            replace_pending_attachments=Mock(),
        ),
        lifecycle=ProcessLifecycle(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={}),
        replace_pending_prompt_extras=Mock(),
    )

    ready = await loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    )

    assert ready is True
    execute_recovery.assert_awaited_once()
    assert turn_application.reconcile_remote_session.await_count == 2
    await runtime.close()


@pytest.mark.anyio
async def test_durable_recovery_stops_automatic_polling_and_observes_exit(
    monkeypatch,
) -> None:
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="active turn",
    )
    pending = _pending_recovery(command)
    turn_application = AsyncMock()
    probes_complete = asyncio.Event()

    async def pending_response(*_args) -> SessionRecoveryResult:
        if turn_application.reconcile_remote_session.await_count >= 3:
            probes_complete.set()
        return SessionRecoveryResult((pending,), (), ())

    turn_application.reconcile_remote_session.side_effect = pending_response
    monkeypatch.setattr(loop, "RECOVERY_RETRY_DELAYS_SEC", (0.001, 0.001))
    runtime = TuiRuntime()
    host = SimpleNamespace(
        attach=SimpleNamespace(
            pending_attachments_snapshot=Mock(return_value=[]),
            replace_pending_attachments=Mock(),
        ),
        lifecycle=ProcessLifecycle(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={}),
        replace_pending_prompt_extras=Mock(),
    )

    recovery = asyncio.create_task(loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    ))
    await asyncio.wait_for(probes_complete.wait(), timeout=0.2)
    await asyncio.sleep(0.02)

    assert turn_application.reconcile_remote_session.await_count == 3
    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_ARMED
    )
    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_REQUESTED
    )
    with pytest.raises(TuiInterruptRequested):
        await asyncio.wait_for(recovery, timeout=0.1)

    await runtime.close()


@pytest.mark.anyio
async def test_durable_recovery_keeps_submit_draft_until_gate_opens(
    monkeypatch,
) -> None:
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="active turn",
    )
    pending = _pending_recovery(command)
    turn_application = AsyncMock()
    turn_application.reconcile_remote_session.side_effect = (
        SessionRecoveryResult((pending,), (), ()),
        SessionRecoveryResult((), (), (command.run_id,)),
    )
    monkeypatch.setattr(loop, "RECOVERY_RETRY_DELAYS_SEC", ())
    runtime = TuiRuntime()
    host = SimpleNamespace(
        attach=SimpleNamespace(
            pending_attachments_snapshot=Mock(return_value=[]),
            replace_pending_attachments=Mock(),
        ),
        lifecycle=ProcessLifecycle(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={}),
        replace_pending_prompt_extras=Mock(),
    )

    recovery = asyncio.create_task(loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    ))
    while turn_application.reconcile_remote_session.await_count < 1:
        await asyncio.sleep(0)

    buffer = runtime.screen.input.buffer
    buffer.text = "next turn"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    assert buffer.text == "next turn"
    assert runtime.submissions.message_queue.empty()
    assert runtime.input_model.history.get_strings() == []

    assert await asyncio.wait_for(recovery, timeout=0.1)
    submission = await runtime.submissions.read_submission()

    assert submission.value == "next turn"
    assert buffer.text == ""
    assert turn_application.reconcile_remote_session.await_count == 2

    await runtime.close()


@pytest.mark.anyio
async def test_durable_recovery_keeps_draft_when_manual_probe_is_pending(
    monkeypatch,
) -> None:
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="active turn",
    )
    pending = _pending_recovery(command)
    turn_application = AsyncMock()
    manual_probe_complete = asyncio.Event()

    async def pending_response(*_args) -> SessionRecoveryResult:
        if turn_application.reconcile_remote_session.await_count >= 2:
            manual_probe_complete.set()
        return SessionRecoveryResult((pending,), (), ())

    turn_application.reconcile_remote_session.side_effect = pending_response
    monkeypatch.setattr(loop, "RECOVERY_RETRY_DELAYS_SEC", ())
    runtime = TuiRuntime()
    host = SimpleNamespace(
        attach=SimpleNamespace(
            pending_attachments_snapshot=Mock(return_value=[]),
            replace_pending_attachments=Mock(),
        ),
        lifecycle=ProcessLifecycle(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={}),
        replace_pending_prompt_extras=Mock(),
    )

    recovery = asyncio.create_task(loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    ))
    while turn_application.reconcile_remote_session.await_count < 1:
        await asyncio.sleep(0)

    buffer = runtime.screen.input.buffer
    buffer.text = "keep editing"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    await asyncio.wait_for(manual_probe_complete.wait(), timeout=0.1)
    await asyncio.sleep(0.02)

    assert turn_application.reconcile_remote_session.await_count == 2
    assert buffer.text == "keep editing"
    assert runtime.submissions.message_queue.empty()
    assert runtime.input_model.history.get_strings() == []

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.DRAFT_DISCARDED
    )
    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_ARMED
    )
    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_REQUESTED
    )
    with pytest.raises(TuiInterruptRequested):
        await asyncio.wait_for(recovery, timeout=0.1)

    await runtime.close()


@pytest.mark.anyio
async def test_durable_recovery_preserves_submit_during_terminal_probe(
    monkeypatch,
) -> None:
    command = SubmitTurnCommand.create(
        session_id="session_test",
        message="active turn",
    )
    pending = _pending_recovery(command)
    terminal_probe_started = asyncio.Event()
    release_terminal_probe = asyncio.Event()
    turn_application = AsyncMock()

    async def recovery_response(*_args) -> SessionRecoveryResult:
        if turn_application.reconcile_remote_session.await_count == 1:
            return SessionRecoveryResult((pending,), (), ())
        terminal_probe_started.set()
        await release_terminal_probe.wait()
        return SessionRecoveryResult((), (), (command.run_id,))

    turn_application.reconcile_remote_session.side_effect = recovery_response
    monkeypatch.setattr(loop, "RECOVERY_RETRY_DELAYS_SEC", (0.001,))
    runtime = TuiRuntime()
    host = SimpleNamespace(
        attach=SimpleNamespace(
            pending_attachments_snapshot=Mock(return_value=[]),
            replace_pending_attachments=Mock(),
        ),
        lifecycle=ProcessLifecycle(),
    )
    state = SimpleNamespace(
        consume_pending_prompt_extras=Mock(return_value={}),
        replace_pending_prompt_extras=Mock(),
    )

    recovery = asyncio.create_task(loop._await_durable_session_recovery(
        host,
        runtime,
        state,
        SimpleNamespace(emit=Mock()),
        turn_application,
        Mock(spec=ProtocolCommandClient),
        session_id="session_test",
    ))
    await asyncio.wait_for(terminal_probe_started.wait(), timeout=0.1)

    buffer = runtime.screen.input.buffer
    buffer.text = "next turn"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    release_terminal_probe.set()

    assert await asyncio.wait_for(recovery, timeout=0.1)
    submission = await runtime.submissions.read_submission()

    assert submission.value == "next turn"
    assert buffer.text == ""
    assert turn_application.reconcile_remote_session.await_count == 2

    await runtime.close()


@pytest.mark.anyio
async def test_effort_menu_uses_primary_selection_contract() -> None:
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="high")

    selected = await choose_model_effort(runtime, "high")

    request = runtime.select_menu.await_args.args[0]
    assert selected == "high"
    assert request.view_id == "model:effort"
    assert request.title == "Update Reasoning Effort"
    assert request.title_accent_suffix == ""
    assert request.status == (
        "Choose the reasoning effort used by the primary model."
    )
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )
    assert [option.is_current for option in request.options] == [
        False,
        False,
        True,
        False,
    ]
    assert request.selected == 2


@pytest.mark.anyio
async def test_effort_command_updates_footer_context_immediately(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    lifecycle = ProcessLifecycle()
    stale_config = {
        "primary": {
            "model": "test-model",
            "reasoning_effort": "medium",
        },
    }
    host = SimpleNamespace(
        configuration_service_url=None,
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
            pending_attachments_snapshot=lambda: [],
        ),
        subscription=SimpleNamespace(current=None),
        lifecycle=lifecycle,
        settings=SimpleNamespace(
            preference_config=lambda: stale_config,
            permissions=preset_permissions("auto"),
            fresh_preferences=AsyncMock(return_value=stale_config),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
    )

    async def monitor_exec_status(_runtime, _host) -> None:
        return None

    def render_status(_application, _effort) -> None:
        lifecycle.request_stop()

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
    await loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    )

    assert runtime.context.model == "test-model high"


@pytest.mark.anyio
async def test_tui_loop_wires_non_persistent_effort_shortcuts(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    lifecycle = ProcessLifecycle()
    pref_config = {
        "primary": {
            "model": "test-model",
            "reasoning_effort": "medium",
        },
    }
    host = SimpleNamespace(
        configuration_service_url=None,
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
        ),
        subscription=SimpleNamespace(current=None),
        lifecycle=lifecycle,
        settings=SimpleNamespace(
            preference_config=lambda: pref_config,
            permissions=preset_permissions("auto"),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
    )
    observed: list[str] = []

    async def monitor_exec_status(_runtime, _host) -> None:
        return None

    def bind_shortcuts(*, decrease, increase) -> None:
        _ = decrease
        increase()
        observed.append(runtime.context.model)
        lifecycle.request_stop()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor_exec_status)
    monkeypatch.setattr(
        runtime,
        "bind_reasoning_effort_shortcuts",
        bind_shortcuts,
    )

    await loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    )

    assert observed == ["test-model high"]


@pytest.mark.parametrize(
    ("current", "direction", "expected"),
    (
        ("medium", "lower", "low"),
        ("medium", "raise", "high"),
        ("low", "lower", None),
        ("xhigh", "raise", None),
        ("unsupported", "raise", "high"),
    ),
)
def test_reasoning_effort_shortcut_uses_codex_step_semantics(
    current: str,
    direction: ReasoningEffortDirection,
    expected: str | None,
) -> None:
    assert next_model_reasoning_effort(current, direction) == expected


@pytest.mark.parametrize(
    ("effort", "direction", "expected"),
    (
        (
            "low",
            "lower",
            "• Reasoning is already at the lowest level (low).",
        ),
        (
            "xhigh",
            "raise",
            "• Reasoning is already at the highest level (extra high).",
        ),
    ),
)
def test_reasoning_effort_shortcut_uses_codex_boundary_messages(
    effort: str,
    direction: ReasoningEffortDirection,
    expected: str,
) -> None:
    block = reasoning_effort_boundary_status_block(effort, direction)

    assert "".join(text for _style, text in block.fragments) == expected


@pytest.mark.anyio
async def test_reasoning_effort_session_override_survives_refresh_and_is_cleared_by_persistence(
) -> None:
    persisted = {
        "primary": {
            "model": "test-model",
            "reasoning_effort": "medium",
        },
    }
    state = TuiSessionState(
        pref_config=persisted,
        model="test-model",
        workspace_label="workspace",
        permissions=preset_permissions("auto"),
    )
    host = SimpleNamespace(
        settings=SimpleNamespace(
            fresh_preferences=AsyncMock(return_value=persisted),
        ),
    )

    assert state.override_reasoning_effort("high") == "high"
    assert state.pref_config["primary"]["reasoning_effort"] == "high"

    await state.refresh_preferences(host, ttl_sec=0.0)

    assert state.reasoning_effort_override == "high"
    assert state.pref_config["primary"]["reasoning_effort"] == "high"
    assert persisted["primary"]["reasoning_effort"] == "medium"

    state.merge_primary({
        "model": "test-model",
        "reasoning_effort": "low",
    })

    assert state.reasoning_effort_override is None
    assert state.pref_config["primary"]["reasoning_effort"] == "low"


def test_reasoning_effort_shortcut_updates_next_turn_without_persisting() -> None:
    runtime = TuiRuntime()
    emit = Mock()
    host = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=emit),
        ),
    )
    state = TuiSessionState(
        pref_config={
            "primary": {
                "model": "test-model",
                "reasoning_effort": "medium",
            },
        },
        model="test-model",
        workspace_label="workspace",
        permissions=preset_permissions("auto"),
    )
    dispatcher = dispatch.TuiCommandDispatcher(
        host,
        runtime,
        state,
        TuiForegroundTasks(runtime, host),
        protocol_client=Mock(spec=ProtocolCommandClient),
    )

    dispatcher.increase_reasoning_effort()

    assert state.reasoning_effort_override == "high"
    assert state.pref_config["primary"]["reasoning_effort"] == "high"
    assert runtime.context.model == "test-model high"
    emit.assert_not_called()

    dispatcher.increase_reasoning_effort()
    dispatcher.increase_reasoning_effort()

    assert state.reasoning_effort_override == "xhigh"
    assert runtime.context.model == "test-model xhigh"
    boundary = emit.call_args_list[0].args[0]
    assert boundary.type == "tui.model_effort"
    assert "".join(
        text
        for _style, text in boundary.renderable.fragments
    ) == (
        "• Reasoning is already at the highest level (extra high)."
    )
    assert emit.call_args_list[1].args[0].type == "tui.gap"


def test_reasoning_effort_shortcut_does_not_mutate_frozen_active_turn() -> None:
    state = TuiSessionState(
        pref_config={
            "primary": {
                "model": "test-model",
                "reasoning_effort": "medium",
            },
        },
        model="test-model",
        workspace_label="workspace",
        permissions=preset_permissions("auto"),
    )
    active_turn = SubmitTurnCommand.create(
        session_id="session-effort",
        message="active turn",
        pref_config=state.pref_config,
    )

    state.override_reasoning_effort("high")

    active_pref = active_turn.pref_config_value()
    active_primary = active_pref["primary"] if active_pref is not None else {}
    assert active_primary["reasoning_effort"] == "medium"
    assert state.pref_config["primary"]["reasoning_effort"] == "high"


@pytest.mark.anyio
async def test_reasoning_effort_keys_dispatch_once_and_defer_to_popup() -> None:
    calls: list[str] = []
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        runtime.bind_reasoning_effort_shortcuts(
            decrease=lambda: calls.append("lower"),
            increase=lambda: calls.append("raise"),
        )
        await runtime.open()
        try:
            alt_period = enhanced_key_token(
                ".",
                frozenset({"alt"}),
            )
            assert alt_period is not None
            pipe_input.send_text(alt_period)
            for _index in range(100):
                if calls:
                    break
                await asyncio.sleep(0.01)
            assert calls == ["raise"]

            shift_down = enhanced_key_token(
                "down",
                frozenset({"shift"}),
            )
            assert shift_down is not None
            pipe_input.send_text(shift_down)
            for _index in range(100):
                if len(calls) == 2:
                    break
                await asyncio.sleep(0.01)
            assert calls == ["raise", "lower"]

            runtime.input_model.completion_menu_completions = Mock(
                return_value=(),
            )
            pipe_input.send_text(alt_period)
            await asyncio.sleep(0.1)

            assert calls == ["raise", "lower"]
        finally:
            await runtime.close()
