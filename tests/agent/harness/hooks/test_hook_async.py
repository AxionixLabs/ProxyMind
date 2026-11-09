# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.harness.hooks.async_tasks import HookAsyncTaskOwner


@pytest.mark.anyio
async def test_hook_async_task_owner_enforces_concurrency_and_closes() -> None:
    owner = HookAsyncTaskOwner(max_concurrency=1)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def first() -> None:
        first_started.set()
        await release_first.wait()

    async def second() -> None:
        second_started.set()
        await release_second.wait()

    assert owner.submit(first(), name="hook first")
    assert owner.submit(second(), name="hook second")
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not second_started.is_set()

    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    release_second.set()
    await owner.close()


@pytest.mark.anyio
@pytest.mark.parametrize("start_before_close", [False, True])
async def test_shutdown_cancels_running_and_queued_hooks_and_awaits_cleanup(start_before_close) -> None:
    owner = HookAsyncTaskOwner(max_concurrency=1)
    started = asyncio.Event()
    cleaned = asyncio.Event()
    queued_started = asyncio.Event()

    async def running() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    async def queued() -> None:
        queued_started.set()

    first = running()
    second = queued()
    assert owner.submit(first, name="running hook")
    assert owner.submit(second, name="queued hook")
    if start_before_close:
        await asyncio.wait_for(started.wait(), timeout=1)

    async with asyncio.timeout(1):
        if start_before_close:
            await asyncio.gather(owner.close(), owner.close())
        else:
            await owner.close()
            await owner.close()

    assert cleaned.is_set() is start_before_close
    assert not queued_started.is_set()
    assert first.cr_frame is None
    assert second.cr_frame is None


@pytest.mark.anyio
async def test_hook_async_task_owner_rejects_new_tasks_after_close() -> None:
    owner = HookAsyncTaskOwner()
    await owner.close()

    completed: list[bool] = []

    async def never_runs() -> None:
        completed.append(True)

    assert not owner.submit(never_runs(), name="hook rejected")
    await asyncio.sleep(0)
    assert completed == []
