# -*- coding: utf-8 -*-

import asyncio
import typing
from unittest.mock import (
    Mock,
    patch,
)

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from frontends.tui.runtime.lifecycle import ApplicationLifecycle


@pytest.mark.anyio
@pytest.mark.parametrize("stop_cancelled", [False, True])
async def test_stop_reaps_application_on_timeout_or_cancellation(
    stop_cancelled: bool,
) -> None:
    application: Application[None] = Application(
        input=DummyInput(), output=DummyOutput(),
    )
    started = asyncio.Event()
    cleaned = asyncio.Event()
    reset_output = Mock()
    lifecycle = ApplicationLifecycle(
        application,
        clear_pending_input=Mock(), finish_input=Mock(),
        reset_synchronized_output=reset_output,
    )

    async def run_application(
        *, pre_run: typing.Callable[[], None], set_exception_handler: bool,
    ) -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    with patch.object(application, "run_async", side_effect=run_application), patch(
        "frontends.tui.runtime.lifecycle._APPLICATION_EXIT_TIMEOUT_SEC", 0.02,
    ), patch(
        "frontends.tui.runtime.lifecycle._APPLICATION_CANCEL_TIMEOUT_SEC", 0.1,
    ), patch("frontends.tui.runtime.lifecycle.observe") as observe:
        task = lifecycle.start()
        closing: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            closing = asyncio.create_task(lifecycle.stop(erase=True))
            await asyncio.sleep(0)
            if stop_cancelled:
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(closing, timeout=0.5)
                observe.assert_not_called()
            else:
                await asyncio.wait_for(closing, timeout=0.5)
                observe.assert_called_once_with(
                    "tui.application.stop.timeout", level="WARNING",
                    phase="exit", timeout_sec=0.02,
                )
            assert cleaned.is_set() and task.cancelled()
            assert not lifecycle.active
            assert lifecycle._task is None
            assert not application.erase_when_done
            reset_output.assert_called_once_with()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)


@pytest.mark.anyio
async def test_stop_bounds_cancel_cleanup_and_retains_unfinished_task() -> None:
    application: Application[None] = Application(
        input=DummyInput(), output=DummyOutput(),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    reset_output = Mock()
    lifecycle = ApplicationLifecycle(
        application,
        clear_pending_input=Mock(), finish_input=Mock(),
        reset_synchronized_output=reset_output,
    )

    async def run_application(
        *, pre_run: typing.Callable[[], None], set_exception_handler: bool,
    ) -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            await release.wait()

    with patch.object(application, "run_async", side_effect=run_application), patch(
        "frontends.tui.runtime.lifecycle._APPLICATION_EXIT_TIMEOUT_SEC", 0.02,
    ), patch(
        "frontends.tui.runtime.lifecycle._APPLICATION_CANCEL_TIMEOUT_SEC", 0.02,
    ), patch("frontends.tui.runtime.lifecycle.observe") as observe:
        task = lifecycle.start()
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            with pytest.raises(TimeoutError, match="after cancellation"):
                async with asyncio.timeout(0.5):
                    await lifecycle.stop(erase=True)
            assert lifecycle.active
            assert lifecycle._task is task
            assert not application.erase_when_done
            assert [call.kwargs["phase"] for call in observe.call_args_list] == [
                "exit", "cancel",
            ]
            reset_output.assert_called_once_with()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await lifecycle.stop(erase=False)
        assert lifecycle._task is None
