# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session.turn import execute_tui_model_turn


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def _transcript_text(runtime: TuiRuntime) -> str:
    return "".join(
        text
        for _style, text in runtime.document.transcript_fragments(width=80)
    )


async def _wait_until(
    predicate,
    *,
    message: str,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if predicate():
            return None
        await asyncio.sleep(0.001)

    raise AssertionError(message)


@pytest.mark.anyio
async def test_event_loop_error_exits_without_prompt_toolkit_exception_prompt(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        application = runtime.screen.application
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        with patch.object(application, "_handle_exception") as prompt_handler:
            await runtime.open()
            try:
                failure = asyncio.create_task(
                    runtime.wait_for_application_failure()
                )
                error = RuntimeError("event loop boundary probe")
                loop.call_exception_handler({
                    "message": "event loop boundary probe",
                    "exception": error,
                })

                assert await asyncio.wait_for(failure, timeout=1.0) is error
                await _wait_until(
                    lambda: not runtime.active,
                    message="application remained active after an event-loop error",
                )

                assert prompt_handler.call_count == 0
                assert loop.get_exception_handler() is previous_handler
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_background_task_error_is_reported_without_stopping_application(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            error = RuntimeError("background task boundary probe")

            async def fail() -> None:
                raise error

            task = runtime.start_background_task(
                fail(),
                name="background task boundary probe",
            )
            await asyncio.gather(task, return_exceptions=True)

            await _wait_until(
                lambda: "Background task failed" in _transcript_text(runtime),
                message="background error was not reported",
            )

            assert runtime.active
            assert (
                "Background task failed: RuntimeError: "
                "background task boundary probe"
            ) in _transcript_text(runtime)
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_event_loop_error_cancels_active_model_turn() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def turn() -> None:
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        await runtime.open()
        model_turn = asyncio.create_task(execute_tui_model_turn(
            SimpleNamespace(emit=Mock()),
            runtime,
            turn(),
        ))
        try:
            await started.wait()
            failure = asyncio.create_task(
                runtime.wait_for_application_failure()
            )
            error = RuntimeError("active turn boundary probe")
            asyncio.get_running_loop().call_exception_handler({
                "message": "active turn boundary probe",
                "exception": error,
            })

            with pytest.raises(RuntimeError, match="active turn boundary probe"):
                await asyncio.wait_for(model_turn, timeout=1.0)

            assert cancelled.is_set()
            assert await asyncio.wait_for(failure, timeout=1.0) is error
            assert not runtime.execution_active
        finally:
            if not model_turn.done():
                model_turn.cancel()
                await asyncio.gather(model_turn, return_exceptions=True)
            await runtime.close()


@pytest.mark.anyio
async def test_resize_reflow_failure_is_reported_once_and_next_resize_recovers(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(
                        f"entry {index}" for index in range(60)
                    )),
                    kind="assistant",
                )
                await _wait_until(
                    lambda: runtime.document.scrollback_line_count > 0
                    and runtime.viewport.scrollback_task is None,
                    message="initial scrollback did not settle",
                )

                error = RuntimeError("resize replay probe")
                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                    side_effect=error,
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)

                    await _wait_until(
                        runtime.viewport._scrollback_generation_failed,
                        message="failed resize generation was not blocked",
                    )

                    runtime.append_block(_block("stable output one"))
                    runtime.append_block(_block("stable output two"))
                    await asyncio.sleep(0.12)

                    assert clear.call_count == 1
                    assert runtime.active
                    assert _transcript_text(runtime).count(
                        "Display refresh failed: RuntimeError: resize replay probe"
                    ) == 1

                terminal_size = Size(rows=8, columns=26)
                runtime.viewport.observe_terminal_geometry(26, 8)
                await _wait_until(
                    lambda: (
                        runtime.viewport._reflowed_geometry == (26, 8)
                        and not runtime.viewport._reflow_required
                    ),
                    message="scrollback did not recover on the next resize",
                )

                assert not runtime.viewport._scrollback_generation_failed()
                assert runtime.active
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_print_failure_does_not_retry_until_geometry_changes(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                error = RuntimeError("scrollback print probe")
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    side_effect=error,
                ) as print_text:
                    runtime.append_block(
                        _block("\n".join(
                            f"entry {index}" for index in range(60)
                        )),
                        kind="assistant",
                    )
                    await _wait_until(
                        runtime.viewport._scrollback_generation_failed,
                        message="failed scrollback generation was not blocked",
                    )

                    runtime.append_block(_block("stable output one"))
                    runtime.append_block(_block("stable output two"))
                    await asyncio.sleep(0.12)

                    assert print_text.call_count == 1
                    assert runtime.active
                    assert _transcript_text(runtime).count(
                        "Display refresh failed: RuntimeError: scrollback print probe"
                    ) == 1

                terminal_size = Size(rows=8, columns=42)
                runtime.viewport.observe_terminal_geometry(42, 8)
                await _wait_until(
                    lambda: not runtime.viewport._scrollback_generation_failed(),
                    message="scrollback did not recover on the next geometry",
                )

                assert runtime.active
            finally:
                await runtime.close()
