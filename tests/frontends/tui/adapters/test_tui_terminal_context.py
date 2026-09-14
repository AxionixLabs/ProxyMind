# -*- coding: utf-8 -*-

import asyncio
import contextlib
import typing
from unittest.mock import (
    PropertyMock,
    patch,
)

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from frontends.tui.adapters.terminal_context import in_terminal


@pytest.fixture
def application() -> typing.Iterator[Application[None]]:
    app: Application[None] = Application(input=DummyInput(), output=DummyOutput())
    app._is_running = True
    with set_app(app), patch.object(app, "_redraw"), patch.object(
        app, "_request_absolute_cursor_position",
    ):
        yield app


@pytest.mark.anyio
async def test_inactive_application_does_not_create_terminal_handover(
    application: Application[None],
) -> None:
    application._is_running = False
    async with in_terminal():
        assert application._running_in_terminal_f is None


@pytest.mark.anyio
async def test_cancelled_waiter_preserves_predecessor_and_successor_order(
    application: Application[None],
) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    third_entered = asyncio.Event()

    async def first() -> None:
        async with in_terminal():
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        async with in_terminal():
            pytest.fail("cancelled waiter acquired the terminal")

    async def third() -> None:
        async with in_terminal():
            third_entered.set()

    tasks = [asyncio.create_task(first())]
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)
        first_completion = application._running_in_terminal_f
        tasks.append(asyncio.create_task(second()))
        await asyncio.sleep(0)
        second_completion = application._running_in_terminal_f
        assert first_completion is not None
        assert second_completion is not None
        assert second_completion is not first_completion

        tasks[1].cancel()
        await asyncio.gather(tasks[1], return_exceptions=True)
        assert not first_completion.done()
        assert not second_completion.done()

        tasks.append(asyncio.create_task(third()))
        await asyncio.sleep(0)
        assert not third_entered.is_set()
        release_first.set()
        await asyncio.wait_for(asyncio.gather(tasks[0], tasks[2]), timeout=1.0)
        assert third_entered.is_set()
        assert first_completion.done() and not first_completion.cancelled()
        assert second_completion.done() and not second_completion.cancelled()
    finally:
        release_first.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["cancel", "error"])
async def test_cpr_wait_failure_releases_terminal_without_changing_input_mode(
    application: Application[None],
    failure: str,
) -> None:
    waiting = asyncio.Event()

    async def wait_for_cpr() -> None:
        waiting.set()
        if failure == "error":
            raise OSError("CPR failed")
        await asyncio.Future()

    async def handover() -> None:
        async with in_terminal():
            pytest.fail("failed CPR wait entered the terminal body")

    with patch.object(
        DummyOutput, "responds_to_cpr", new_callable=PropertyMock,
        return_value=True,
    ), patch.object(
        application.renderer, "wait_for_cpr_responses", side_effect=wait_for_cpr,
    ), patch.object(application.input, "detach") as detach:
        task = asyncio.create_task(handover())
        try:
            await asyncio.wait_for(waiting.wait(), timeout=1.0)
            if failure == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(OSError, match="CPR failed"):
                    await task
            completion = application._running_in_terminal_f
            assert completion is not None and completion.done()
            assert not completion.cancelled()
            assert not application._running_in_terminal
            detach.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async with in_terminal():
        assert application._running_in_terminal


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["normal", "cancel", "error"])
@pytest.mark.parametrize("render_cli_done", [False, True])
async def test_terminal_modes_restore_in_owning_task(
    application: Application[None],
    outcome: str,
    render_cli_done: bool,
) -> None:
    entered = asyncio.Event()
    transitions: list[str] = []

    @contextlib.contextmanager
    def mode(name: str) -> typing.Iterator[None]:
        owner = asyncio.current_task()
        transitions.append(f"enter {name}")
        try:
            yield
        finally:
            assert asyncio.current_task() is owner
            transitions.append(f"exit {name}")

    async def handover() -> None:
        async with in_terminal(render_cli_done=render_cli_done):
            assert application._running_in_terminal
            entered.set()
            if outcome == "cancel":
                await asyncio.Future()
            elif outcome == "error":
                raise ValueError("body failed")

    with patch.object(
        application.input, "detach", side_effect=lambda: mode("detach"),
    ), patch.object(
        application.input, "cooked_mode", side_effect=lambda: mode("cooked"),
    ):
        task = asyncio.create_task(handover())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "error":
                with pytest.raises(ValueError, match="body failed"):
                    await task
            else:
                await task
            assert transitions == [
                "enter detach", "enter cooked", "exit cooked", "exit detach",
            ]
            completion = application._running_in_terminal_f
            assert completion is not None and completion.done()
            assert not completion.cancelled()
            assert not application._running_in_terminal
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["erase", "reset", "redraw"])
async def test_renderer_failure_still_releases_terminal(
    application: Application[None],
    stage: str,
) -> None:
    target = application if stage == "redraw" else application.renderer
    method = "_redraw" if stage == "redraw" else stage
    with patch.object(target, method, side_effect=OSError("render failed")):
        with pytest.raises(OSError, match="render failed"):
            async with in_terminal():
                pass

    completion = application._running_in_terminal_f
    assert completion is not None and completion.done()
    assert not completion.cancelled()
    assert not application._running_in_terminal
    async with in_terminal():
        assert application._running_in_terminal
