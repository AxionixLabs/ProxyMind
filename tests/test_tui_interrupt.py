# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_app.interaction.contracts import PromptContext
from mind_app.tui.adapters.application import TuiApplicationSink
from mind_app.tui.core.interrupt import TuiInterruptState
from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.submission import TuiInterruptRequested
from mind_app.tui.session import loop
from mind_core.permissions import preset_permissions
from mind_app.tui.session.turn import execute_tui_model_turn


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
    runtime.submissions.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))

    runtime.submissions.interrupt_input()
    assert "Ctrl + C again to exit" == _fragments_text(
        runtime.screen._footer_fragments()
    )

    runtime.submissions.interrupt_input()
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
    task_event = asyncio.Event()
    pref_config = {"primary": {"model": "test-model"}}
    mind = SimpleNamespace(
        permissions=preset_permissions("auto"),
        frontend=SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=SimpleNamespace(emit=Mock()),
        ),
        pref=SimpleNamespace(to_config=lambda: pref_config),
        fresh_pref_config=AsyncMock(return_value=pref_config),
        task_event=task_event,
        exit_code=0,
    )
    monkeypatch.setattr(
        loop,
        "monitor_exec_status",
        AsyncMock(return_value=None),
    )

    session_task = asyncio.create_task(loop.run_tui_loop(mind))
    await asyncio.sleep(0)
    runtime.submissions.interrupt_input()
    runtime.submissions.interrupt_input()

    await asyncio.wait_for(session_task, timeout=1.0)

    assert mind.exit_code == 130
    assert task_event.is_set()


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


def test_first_ctrl_c_clears_idle_draft_and_arms_exit() -> None:
    runtime = TuiRuntime()
    runtime.screen.input.buffer.text = "unfinished draft"

    runtime.submissions.interrupt_input()

    assert runtime.screen.input.buffer.text == ""
    assert runtime.submissions.interrupt_state.exit_armed
    assert (
        _fragments_text(runtime.screen._footer_fragments())
        == "Ctrl + C again to exit"
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
                assert runtime.submissions.interrupt_state.exit_armed
            finally:
                await runtime.close()


def test_finished_turn_is_not_marked_as_interrupted() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    runtime.bind_interrupt_handler(lambda: False)

    runtime.submissions.interrupt_input()

    assert not runtime.consume_turn_interrupt()
    assert runtime.submissions.interrupt_state.exit_armed


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

    task = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
    ))
    await started.wait()

    runtime.submissions.interrupt_input()
    await task

    assert not runtime.execution_active
    assert runtime.submissions.interrupt_state.exit_armed
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

    task = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
        stream_command_handler=handle,
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
    application.emit.assert_not_called()


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)


def _document_text(runtime: TuiRuntime) -> str:
    return _fragments_text(runtime.document.fragments(width=100))
