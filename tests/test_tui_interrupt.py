# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from metadata import const
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from frontends.interaction.contracts import PromptContext
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.core.interrupt import (
    InterruptDisposition,
    TuiInterruptState
)
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.submission import TuiInterruptRequested
from frontends.tui.session import loop
from agent.domain.policies import preset_permissions
from agent.application import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.harness.process_lifecycle import ProcessLifecycle
from agent.ports import ProtocolCommandClient
from frontends.tui.session.turn import execute_tui_model_turn


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
    """为 TUI 中断单测注入显式 Session runtime。"""
    monkeypatch.setattr(
        loop,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


async def _render_next_frame(runtime: TuiRuntime):
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


async def _wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return None
        await asyncio.sleep(0.001)

    raise AssertionError(
        "input text did not become "
        f"{text!r}: {runtime.screen.input.buffer.text!r}"
    )


def test_interrupt_state_expires_and_consumes_exit_requests() -> None:
    clock = [10.0]
    state = TuiInterruptState(timeout_sec=2.0, clock=lambda: clock[0])

    state.arm_exit()
    assert state.exit_armed

    clock[0] = 12.1
    assert not state.exit_armed

    state.request_exit()
    assert state.consume_exit_request() == "interrupt"
    assert state.consume_exit_request() is None


@pytest.mark.anyio
async def test_double_ctrl_c_exits_and_precedes_queued_message() -> None:
    runtime = TuiRuntime()
    runtime.submissions.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_ARMED
    )
    assert "Ctrl + C again to exit" == _fragments_text(
        runtime.screen._footer_fragments()
    )

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.EXIT_REQUESTED
    )
    with pytest.raises(TuiInterruptRequested):
        await runtime.read_message(PromptContext(model="test"))

    assert runtime.submissions.queued_messages.active


@pytest.mark.anyio
async def test_double_ctrl_c_wakes_pending_message_reader() -> None:
    runtime = TuiRuntime()

    async def read_until_interrupted() -> str:
        try:
            await runtime.read_message(PromptContext(model="test"))
        except TuiInterruptRequested:
            return "interrupted"
        return "message"

    reader = asyncio.create_task(read_until_interrupted())
    await asyncio.sleep(0)

    runtime.submissions.interrupt_input()
    await asyncio.sleep(0)
    assert not reader.done()

    runtime.submissions.interrupt_input()
    assert await reader == "interrupted"


@pytest.mark.anyio
async def test_double_ctrl_c_returns_normally_from_session_loop(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    lifecycle = ProcessLifecycle()
    pref_config = {"primary": {"model": "test-model"}}
    host = SimpleNamespace(
        configuration_service_url=None,
        attach=SimpleNamespace(
            has_pending_attachments=lambda: False,
            pending_attachments_snapshot=lambda: [],
        ),
        subscription=SimpleNamespace(current=None),
        lifecycle=lifecycle,
        settings=SimpleNamespace(
            preference_config=lambda: pref_config,
            permissions=preset_permissions("auto"),
            fresh_preferences=AsyncMock(return_value=pref_config),
        ),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
    )
    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )

    session_task = asyncio.create_task(loop.run_tui_loop(
        host,
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
    await asyncio.sleep(0)
    runtime.submissions.interrupt_input()
    runtime.submissions.interrupt_input()

    await asyncio.wait_for(session_task, timeout=1.0)

    assert lifecycle.exit_code == 130
    assert lifecycle.stop_event.is_set()


@pytest.mark.anyio
async def test_exit_confirmation_task_expires_footer() -> None:
    runtime = TuiRuntime()
    runtime.submissions.interrupt_state.timeout_sec = 0.01

    runtime.submissions.interrupt_input()

    assert runtime.submissions.exit_expiry_task is not None
    assert runtime.submissions.interrupt_state.exit_armed

    await asyncio.sleep(0.02)

    assert runtime.submissions.exit_expiry_task is None
    assert not runtime.submissions.interrupt_state.exit_armed
    assert "again to exit" not in _fragments_text(
        runtime.screen._footer_fragments()
    )


@pytest.mark.anyio
async def test_ctrl_d_requests_clean_exit_before_queued_message() -> None:
    runtime = TuiRuntime()
    runtime.submissions.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))

    runtime.submissions.exit_input()

    with pytest.raises(EOFError):
        await runtime.read_message(PromptContext(model="test"))
    assert runtime.submissions.queued_messages.active


@pytest.mark.anyio
async def test_editing_clears_exit_confirmation() -> None:
    runtime = TuiRuntime()

    runtime.submissions.interrupt_input()
    runtime.screen.input.buffer.text = "new direction"

    assert not runtime.submissions.interrupt_state.exit_armed
    assert runtime.submissions.exit_expiry_task is None
    assert "again to exit" not in _fragments_text(
        runtime.screen._footer_fragments()
    )


def test_first_ctrl_c_clears_idle_draft_without_arming_exit() -> None:
    runtime = TuiRuntime()
    runtime.screen.input.buffer.text = "unfinished draft"

    runtime.submissions.interrupt_input()

    assert runtime.screen.input.buffer.text == ""
    assert not runtime.submissions.interrupt_state.exit_armed
    assert runtime.submissions.exit_expiry_task is None
    assert "again to exit" not in _fragments_text(
        runtime.screen._footer_fragments()
    )


@pytest.mark.anyio
async def test_ctrl_c_clears_multiline_input_without_top_canvas_spacer() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                initial_height = runtime.screen._visible_height()
                value = "\n".join(f"line {index}" for index in range(8))
                runtime.screen.input.buffer.text = value
                runtime.screen.input.buffer.cursor_position = len(value)
                await _render_next_frame(runtime)

                assert runtime.screen._visible_height() > initial_height

                pipe_input.send_text("\x03")
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._visible_height() == initial_height
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert not runtime.submissions.interrupt_state.exit_armed
            finally:
                await runtime.close()


def test_ignored_turn_interrupt_only_arms_exit() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.bind_interrupt_handler(lambda: InterruptDisposition.IGNORED)

    disposition = runtime.submissions.interrupt_input()

    assert disposition is InterruptDisposition.EXIT_ARMED
    assert runtime.submissions.interrupt_state.exit_armed


def test_ctrl_c_during_stream_clears_draft_without_interrupt() -> None:
    runtime = TuiRuntime()
    interrupt = Mock(return_value=InterruptDisposition.CONSUMED)
    runtime.set_execution_active(True)
    runtime.bind_interrupt_handler(interrupt)
    runtime.screen.input.buffer.text = "draft while streaming"

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.DRAFT_DISCARDED
    )

    assert runtime.screen.input.buffer.text == ""
    interrupt.assert_not_called()
    assert not runtime.submissions.interrupt_state.exit_armed

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )

    interrupt.assert_called_once_with()
    assert runtime.submissions.interrupt_state.exit_armed


def test_ctrl_c_during_foreground_barrier_clears_without_interrupt() -> None:
    runtime = TuiRuntime()
    interrupt = Mock(return_value=InterruptDisposition.CONSUMED)
    runtime.set_foreground_active(True)
    runtime.bind_interrupt_handler(interrupt)
    runtime.screen.input.buffer.text = "draft while waiting"

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.DRAFT_DISCARDED
    )

    assert runtime.screen.input.buffer.text == ""
    interrupt.assert_not_called()
    assert not runtime.submissions.interrupt_state.exit_armed

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )

    interrupt.assert_called_once_with()
    assert runtime.submissions.interrupt_state.exit_armed


def test_ctrl_c_clears_at_query_and_completion_without_interrupt() -> None:
    runtime = TuiRuntime()
    interrupt = Mock(return_value=InterruptDisposition.CONSUMED)
    runtime.bind_interrupt_handler(interrupt)
    model = runtime.input_model
    buffer = runtime.screen.input.buffer
    buffer.text = "@aaa"
    buffer.cursor_position = len(buffer.text)

    assert model.completion_menu_completions(buffer.document) == ()

    disposition = model.handle_interrupt(buffer)

    assert disposition is InterruptDisposition.DRAFT_DISCARDED
    assert buffer.text == ""
    assert buffer.complete_state is None
    assert model.completion_menu_completions(buffer.document) is None
    interrupt.assert_not_called()
    assert not runtime.submissions.interrupt_state.exit_armed


@pytest.mark.anyio
async def test_user_interrupt_cancels_only_current_turn_and_commits_notice() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    started = asyncio.Event()

    async def turn() -> None:
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
    ))
    await started.wait()

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    await task

    assert not runtime.execution_active
    assert runtime.submissions.interrupt_state.exit_armed
    view = application.emit.call_args.args[0]
    assert view.type == "tui.interrupted"
    assert _fragments_text(view.renderable.fragments) == (
        f"■ Conversation interrupted · Tell {const.APP_DESC} what to do differently."
    )

    TuiApplicationSink(runtime)._emit_active(view)
    assert runtime.document.blocks[-1].kind == "notice"
    assert _document_text(runtime).endswith(_fragments_text(view.renderable.fragments))


@pytest.mark.anyio
async def test_tui_uses_event_projection_for_terminal_status() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    projected = SimpleNamespace(
        status="completed",
        projection=SimpleNamespace(status="interrupted"),
    )

    async def turn():
        return projected

    result = await execute_tui_model_turn(
        application,
        runtime,
        turn(),
    )

    assert result is projected
    assert application.emit.call_args.args[0].type == "tui.interrupted"


@pytest.mark.anyio
async def test_external_cancellation_is_not_swallowed() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def turn() -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    task = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not runtime.execution_active
    assert cancelled.is_set()
    application.emit.assert_not_called()


@pytest.mark.anyio
async def test_stream_quit_command_cancels_turn_without_queueing_message() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    interrupt_requested = Mock()
    started = asyncio.Event()

    async def turn() -> None:
        started.set()
        await asyncio.Future()

    def handle(_value, cancel_turn) -> bool:
        cancel_turn()
        return True

    task = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
        stream_command_handler=handle,
        on_interrupt_requested=interrupt_requested,
        show_interrupt_notice=lambda: False,
    ))
    await started.wait()

    runtime.screen.input.buffer.text = "/quit"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    await task

    assert not runtime.execution_active
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert "/quit" not in _document_text(runtime)
    interrupt_requested.assert_not_called()
    application.emit.assert_not_called()


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)


def _document_text(runtime: TuiRuntime) -> str:
    return _fragments_text(runtime.document.fragments(width=100))
