# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import time
import typing

from agent.application.turns.run_result import RunResult
from agent.ports.presentation import TurnForegroundLifecyclePort

ForegroundParameters = typing.ParamSpec("ForegroundParameters")


async def run_foreground_turn(
    lifecycle: TurnForegroundLifecyclePort,
    operation: typing.Callable[
        ForegroundParameters,
        typing.Awaitable[RunResult],
    ],
    *args: ForegroundParameters.args,
    **kwargs: ForegroundParameters.kwargs,
) -> RunResult:
    """在前台展示与清理生命周期内执行一次轮次操作。"""
    started_at = time.perf_counter()
    lifecycle.begin_terminal_progress()
    completed = False

    try:
        await lifecycle.start_animation()
        result = await operation(*args, **kwargs)
        completed = True
        return result
    finally:
        try:
            if completed:
                lifecycle.finish_turn_wait()
            if completed and lifecycle.animate:
                lifecycle.emit_worked_footer(
                    time.perf_counter() - started_at
                )
        finally:
            try:
                await lifecycle.await_cleanup(lifecycle.stop_animation())
            finally:
                lifecycle.end_terminal_progress()


if __name__ == "__main__":
    pass
