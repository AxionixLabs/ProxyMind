# -*- coding: utf-8 -*-

import asyncio
import typing
from io import StringIO
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime


class TerminalOutput(StringIO):
    def isatty(self) -> bool:
        return True


async def _wait_until(predicate: typing.Callable[[], bool]) -> None:
    async with asyncio.timeout(1.0):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cpr_response", ["disabled", "received", "delayed", "missing"],
)
async def test_double_ctrl_c_closes_during_scrollback(cpr_response: str) -> None:
    with create_pipe_input() as pipe_input:
        output = Vt100_Output(
            TerminalOutput(),
            get_size=lambda: Size(rows=24, columns=80),
            term="xterm-256color",
            enable_cpr=cpr_response != "disabled",
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        application = runtime.screen.application
        application_task = runtime._application_lifecycle._task
        assert application_task is not None
        closing: asyncio.Task[None] | None = None
        try:
            runtime.append_block(
                FragmentBlock((("", "question"),)), kind="user",
            )
            runtime.append_block(
                FragmentBlock((("", "answer\n" * 70),)), kind="assistant",
            )
            await _wait_until(
                lambda: application._running_in_terminal_f is not None,
            )
            if cpr_response != "disabled":
                scrollback = runtime.viewport.scrollback_task
                assert scrollback is not None and not scrollback.done()
                assert application.renderer.waiting_for_cpr
            if cpr_response == "received":
                while application.renderer.waiting_for_cpr:
                    application.renderer.report_absolute_cursor_row(1)

            runtime.submissions.interrupt_input()
            runtime.submissions.interrupt_input()
            closing = asyncio.create_task(runtime.close())
            if cpr_response == "delayed":
                await asyncio.sleep(0.02)
                while application.renderer.waiting_for_cpr:
                    application.renderer.report_absolute_cursor_row(1)
            done, _ = await asyncio.wait((closing,), timeout=1.8)
            terminal_completion = application._running_in_terminal_f

            assert done, "TUI close remained blocked on terminal handover"
            await closing
            assert terminal_completion is not None
            assert terminal_completion.done()
            assert not terminal_completion.cancelled()
            assert application_task.done() and not application_task.cancelled()
            assert not runtime.active
        finally:
            if not application_task.done():
                application_task.cancel()
            await asyncio.gather(application_task, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            else:
                await runtime.close()


@pytest.mark.anyio
async def test_exit_watchdog_reaps_application_with_orphaned_terminal_future(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        await runtime.open()
        application_task = runtime._application_lifecycle._task
        assert application_task is not None
        runtime.screen.application._running_in_terminal_f = loop.create_future()

        with patch(
            "frontends.tui.runtime.lifecycle._APPLICATION_EXIT_TIMEOUT_SEC", 0.02,
        ), patch("frontends.tui.runtime.lifecycle.observe") as observe:
            try:
                async with asyncio.timeout(0.5):
                    await runtime.close()
                assert application_task.cancelled()
                assert not runtime.active
                assert loop.get_exception_handler() is previous_handler
                assert observe.call_args.kwargs["phase"] == "exit"
            finally:
                application_task.cancel()
                await asyncio.gather(application_task, return_exceptions=True)


@pytest.mark.anyio
async def test_application_exit_failure_still_releases_terminal_guards() -> None:
    runtime = TuiRuntime(input_obj=None, output_obj=DummyOutput())
    error = TimeoutError("application cleanup failed")
    with patch.object(
        runtime, "_exit_application", new=AsyncMock(side_effect=error),
    ), patch.object(
        runtime.terminal_progress, "close", new=Mock(),
    ) as progress_close, patch.object(
        runtime._terminal_stderr_guard, "close", new=Mock(),
    ) as stderr_close, patch.object(
        runtime.screen.directory_trust, "close", new=Mock(),
    ) as trust_close:
        with pytest.raises(TimeoutError, match="application cleanup failed"):
            await runtime.close()
        progress_close.assert_called_once_with()
        trust_close.assert_called_once_with()
        stderr_close.assert_called_once_with()
