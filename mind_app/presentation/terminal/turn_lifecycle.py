# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing

from agent.application.turns.run_result import RunResult
from mind_app.presentation.stream.worked import emit_worked_footer

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


async def run_foreground_turn(
    controller: "Mind",
    operation: typing.Callable[..., typing.Awaitable[RunResult]],
    *args: typing.Any,
    **kwargs: typing.Any,
) -> RunResult:
    """在终端进度和动画生命周期内执行一次轮次操作。"""
    started_at = time.perf_counter()
    frontend_runtime = controller.frontend.runtime
    frontend_runtime.begin_terminal_progress()
    completed = False

    try:
        await controller.start_anim()
        result = await operation(*args, **kwargs)
        completed = True
        return result
    finally:
        try:
            if completed:
                finish_turn_wait = getattr(
                    frontend_runtime,
                    "finish_turn_wait",
                    None,
                )
                if callable(finish_turn_wait):
                    finish_turn_wait()
            if completed and controller.animate:
                emit_worked_footer(
                    controller.frontend.application,
                    time.perf_counter() - started_at,
                )
        finally:
            try:
                await controller.await_cleanup(controller.stop_anim("wait"))
            finally:
                frontend_runtime.end_terminal_progress()


__all__ = ("run_foreground_turn",)
