# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.harness.process_lifecycle import ProcessLifecycle


def test_process_lifecycle_owns_stop_signal_and_exit_code() -> None:
    lifecycle = ProcessLifecycle()

    assert lifecycle.exit_code == 0
    assert not lifecycle.stop_event.is_set()

    lifecycle.request_stop(exit_code=130)

    assert lifecycle.exit_code == 130
    assert lifecycle.stop_event.is_set()


@pytest.mark.anyio
async def test_process_lifecycle_forces_cleanup_after_repeated_cancel() -> None:
    lifecycle = ProcessLifecycle()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_cancelled.set()

    async def wait_after_prior_cancellation() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        await lifecycle.await_cleanup(cleanup())

    waiting = asyncio.create_task(wait_after_prior_cancellation())
    await cleanup_started.wait()
    waiting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert cleanup_cancelled.is_set()
