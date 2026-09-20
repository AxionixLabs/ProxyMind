# -*- coding: utf-8 -*-

import asyncio
import typing
from unittest.mock import (
    Mock,
    patch,
)

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import (
    DummyInput,
    create_pipe_input,
)
from prompt_toolkit.output import DummyOutput

from frontends.tui.runtime.lifecycle import (
    ApplicationLifecycle,
    TerminalApplication,
)


class ResizableOutput(DummyOutput):
    """提供可观测读取次数的终端尺寸，不主动发送输入或重绘通知。"""

    def __init__(self, size: Size) -> None:
        self.size = size
        self.read_count = 0

    def get_size(self) -> Size:
        """返回当前终端尺寸并记录轮询读取。"""
        self.read_count += 1
        return self.size


async def _wait_for_size_reads(output: ResizableOutput, count: int) -> None:
    """等待真实后台轮询经过指定次数的尺寸读取。"""
    async with asyncio.timeout(1.0):
        while output.read_count < count:
            await asyncio.sleep(0)


@pytest.mark.anyio
@pytest.mark.parametrize("cancelled", (False, True), ids=("exit", "cancel"))
async def test_terminal_resize_before_first_poll_and_after_reopen(
    cancelled: bool,
) -> None:
    output = ResizableOutput(Size(rows=24, columns=80))
    with create_pipe_input() as pipe:
        application = TerminalApplication(
            input=pipe, output=output, terminal_size_polling_interval=0.02,
        )
        for initial, target in (
            (Size(rows=24, columns=80), Size(rows=18, columns=44)),
            (Size(rows=32, columns=120), Size(rows=20, columns=50)),
        ):
            output.size = initial
            rendered: asyncio.Queue[Size] = asyncio.Queue()
            first_frame = asyncio.Event()

            def after_render(app: Application[None]) -> None:
                size = app.renderer._last_size
                assert size is not None
                rendered.put_nowait(size)
                if not first_frame.is_set():
                    first_frame.set()
                    # 终端在首帧之后、尺寸轮询任务获得执行前缩放。
                    output.size = target

            application.after_render += after_render
            task = asyncio.create_task(application.run_async(
                set_exception_handler=False, handle_sigint=False,
            ))
            try:
                assert await asyncio.wait_for(rendered.get(), 1.0) == initial
                assert await asyncio.wait_for(rendered.get(), 1.0) == target

                reads = output.read_count
                revision = application.render_counter
                await _wait_for_size_reads(output, reads + 2)
                assert application.render_counter == revision
                assert rendered.empty()

                output.size = Size(rows=28, columns=100)
                assert await asyncio.wait_for(rendered.get(), 1.0) == output.size
            finally:
                application.after_render -= after_render
                if cancelled:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    application.exit(result=None)
                    await asyncio.wait_for(task, 1.0)
            assert not application._background_tasks

            reads = output.read_count
            await asyncio.sleep(0.05)
            assert output.read_count == reads


@pytest.mark.anyio
async def test_terminal_size_polling_can_be_disabled() -> None:
    output = ResizableOutput(Size(rows=24, columns=80))
    rendered = asyncio.Event()
    with create_pipe_input() as pipe:
        application = TerminalApplication(
            input=pipe, output=output, terminal_size_polling_interval=None,
            after_render=lambda app: rendered.set(),
        )
        task = asyncio.create_task(application.run_async(
            set_exception_handler=False, handle_sigint=False,
        ))
        try:
            await asyncio.wait_for(rendered.wait(), 1.0)
            output.size = Size(rows=18, columns=44)
            reads = output.read_count
            revision = application.render_counter
            await asyncio.sleep(0.05)
            assert output.read_count == reads
            assert application.render_counter == revision
        finally:
            application.exit(result=None)
            await asyncio.wait_for(task, 1.0)
        assert not application._background_tasks


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
