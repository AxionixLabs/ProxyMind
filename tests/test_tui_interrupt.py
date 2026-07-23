# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.adapters.application import TuiApplicationSink
from mind_app.tui.core.interrupt import TuiInterruptState
from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.session.loop import _execute_tui_model_turn


def test_interrupt_state_expires_and_consumes_requests() -> None:
    clock = [10.0]
    state = TuiInterruptState(timeout_sec=2.0, clock=lambda: clock[0])

    state.arm_exit()
    assert state.exit_armed

    clock[0] = 12.1
    assert not state.exit_armed

    state.request_turn_interrupt()
    assert state.consume_turn_interrupt()
    assert not state.consume_turn_interrupt()

    state.request_exit()
    assert state.consume_exit_request() == "interrupt"
    assert state.consume_exit_request() is None


@pytest.mark.anyio
async def test_double_ctrl_c_exits_and_precedes_queued_message() -> None:
    runtime = TuiRuntime()
    runtime.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))

    runtime._interrupt_input()
    assert "Ctrl + C again to exit" == _fragments_text(runtime._footer_fragments())

    runtime._interrupt_input()
    with pytest.raises(KeyboardInterrupt):
        await runtime.read_message(PromptContext(mode="chat", model="test"))

    assert runtime.queued_messages.active


@pytest.mark.anyio
async def test_double_ctrl_c_wakes_pending_message_reader() -> None:
    runtime = TuiRuntime()

    async def read_until_interrupted() -> str:
        try:
            await runtime.read_message(PromptContext(mode="chat", model="test"))
        except KeyboardInterrupt:
            return "interrupted"
        return "message"

    reader = asyncio.create_task(read_until_interrupted())
    await asyncio.sleep(0)

    runtime._interrupt_input()
    await asyncio.sleep(0)
    assert not reader.done()

    runtime._interrupt_input()
    assert await reader == "interrupted"


@pytest.mark.anyio
async def test_exit_confirmation_task_expires_footer() -> None:
    runtime = TuiRuntime()
    runtime.interrupt_state.timeout_sec = 0.01

    runtime._interrupt_input()

    assert runtime._exit_expiry_task is not None
    assert runtime.interrupt_state.exit_armed

    await asyncio.sleep(0.02)

    assert runtime._exit_expiry_task is None
    assert not runtime.interrupt_state.exit_armed
    assert "again to exit" not in _fragments_text(runtime._footer_fragments())


@pytest.mark.anyio
async def test_ctrl_d_requests_clean_exit_before_queued_message() -> None:
    runtime = TuiRuntime()
    runtime.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))

    runtime._exit_input()

    with pytest.raises(EOFError):
        await runtime.read_message(PromptContext(mode="chat", model="test"))
    assert runtime.queued_messages.active


@pytest.mark.anyio
async def test_editing_clears_exit_confirmation() -> None:
    runtime = TuiRuntime()

    runtime._interrupt_input()
    runtime.input.buffer.text = "new direction"

    assert not runtime.interrupt_state.exit_armed
    assert runtime._exit_expiry_task is None
    assert "again to exit" not in _fragments_text(runtime._footer_fragments())


def test_first_ctrl_c_clears_idle_draft_and_arms_exit() -> None:
    runtime = TuiRuntime()
    runtime.input.buffer.text = "unfinished draft"

    runtime._interrupt_input()

    assert runtime.input.buffer.text == ""
    assert runtime.interrupt_state.exit_armed
    assert _fragments_text(runtime._footer_fragments()) == "Ctrl + C again to exit"


def test_finished_turn_is_not_marked_as_interrupted() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.bind_turn_interrupt(lambda: False)

    runtime._interrupt_input()

    assert not runtime.consume_turn_interrupt()
    assert runtime.interrupt_state.exit_armed


def test_ctrl_c_closes_completion_before_global_interrupt() -> None:
    model = TuiRuntime().input_model
    interrupt = Mock()
    model.bind_interrupt(interrupt)
    buffer = SimpleNamespace(
        complete_state=object(),
        cancel_completion=Mock(),
    )

    model.handle_interrupt(buffer)

    buffer.cancel_completion.assert_called_once_with()
    interrupt.assert_not_called()


@pytest.mark.anyio
async def test_user_interrupt_cancels_only_current_turn_and_commits_notice() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    started = asyncio.Event()

    async def turn() -> None:
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(_execute_tui_model_turn(
        application,
        runtime,
        turn(),
    ))
    await started.wait()

    runtime._interrupt_input()
    await task

    assert not runtime.execution_active
    assert runtime.interrupt_state.exit_armed
    view = application.emit.call_args.args[0]
    assert view.type == "tui.interrupted"
    assert _fragments_text(view.renderable.fragments) == (
        "■ Response interrupted · Tell Mind what to do differently."
    )

    TuiApplicationSink(runtime)._emit_active(view)
    assert runtime.document.blocks[-1].kind == "notice"
    assert _document_text(runtime).endswith(_fragments_text(view.renderable.fragments))


@pytest.mark.anyio
async def test_external_cancellation_is_not_swallowed() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    started = asyncio.Event()

    async def turn() -> None:
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(_execute_tui_model_turn(
        application,
        runtime,
        turn(),
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not runtime.execution_active
    application.emit.assert_not_called()


@pytest.mark.anyio
async def test_stream_quit_command_cancels_turn_without_queueing_message() -> None:
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    started = asyncio.Event()

    async def turn() -> None:
        started.set()
        await asyncio.Future()

    def handle(_value, cancel_turn) -> bool:
        runtime.request_turn_interrupt()
        cancel_turn()
        return True

    task = asyncio.create_task(_execute_tui_model_turn(
        application,
        runtime,
        turn(),
        stream_command_handler=handle,
        show_interrupt_notice=lambda: False,
    ))
    await started.wait()

    runtime.input.buffer.text = "/quit"
    runtime._accept_input(runtime.input.buffer)
    await task

    assert not runtime.execution_active
    assert not runtime.queued_messages.active
    assert runtime.message_queue.empty()
    assert "/quit" in _document_text(runtime)
    application.emit.assert_not_called()


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)


def _document_text(runtime: TuiRuntime) -> str:
    return _fragments_text(runtime.document.fragments(width=100))
