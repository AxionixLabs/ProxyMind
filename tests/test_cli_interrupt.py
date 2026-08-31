# -*- coding: utf-8 -*-

import asyncio

from frontends.cli.entry import _InterruptController


async def _wait_forever() -> int:
    await asyncio.Event().wait()
    return 0


def _run_scheduled_callbacks(loop: asyncio.AbstractEventLoop) -> None:
    loop.call_soon(loop.stop)
    loop.run_forever()


def test_repeated_process_interrupt_cancels_owned_tasks() -> None:
    loop = asyncio.new_event_loop()
    try:
        main_task = loop.create_task(_wait_forever())
        background_task = loop.create_task(_wait_forever())
        interrupts = _InterruptController(loop)
        interrupts.bind_main_task(main_task)

        interrupts.handle(0, None)
        _run_scheduled_callbacks(loop)

        assert main_task.cancelling() == 1
        assert background_task.cancelling() == 0

        interrupts.handle(0, None)
        _run_scheduled_callbacks(loop)

        assert background_task.cancelling() == 1

        pending = asyncio.all_tasks(loop)
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()


def test_process_interrupt_ignores_closed_loop() -> None:
    loop = asyncio.new_event_loop()
    interrupts = _InterruptController(loop)
    loop.close()

    interrupts.handle(0, None)
