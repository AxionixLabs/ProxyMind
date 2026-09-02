# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import (
    OutputSession,
    OutputSurfaceContext,
)
from frontends.tui.runtime.turn_surface import (
    SurfaceProjection,
    TuiTurnSurfaceCoordinator,
)
from .content import TuiContentSink
from .output import TuiOutputControl
from .presentation import TuiPresentationSink
from .status import TuiStreamStatusControl
from ..core.runtime import TuiRuntime


def create_tui_output_session(
    log_file: str,
    *,
    context: OutputSurfaceContext,
    animate: bool = True,
    runtime: TuiRuntime,
) -> OutputSession:
    """创建持久终端 TUI 对应的单轮输出会话。"""
    control = TuiOutputControl(
        log_file,
        runtime=runtime,
        animate=animate,
    )
    presentation = TuiPresentationSink(control)
    return OutputSession(
        context=context,
        control=control,
        activity=TuiTurnSurfaceCoordinator(
            context,
            lambda projection: _apply_surface_projection(runtime, projection),
        ),
        status=TuiStreamStatusControl(),
        content=TuiContentSink(
            control,
            before_assistant_output=(
                presentation.flush_terminal_waits_before_assistant_output
            ),
        ),
        presentation=presentation,
    )


async def _apply_surface_projection(
    runtime: TuiRuntime,
    projection: SurfaceProjection,
) -> None:
    """把单一 Turn 表面投影提交给现有前景活动槽。"""
    if projection.indicator == "hidden":
        runtime.activity.finish_wait()
        return None

    if projection.indicator == "retrying":
        runtime.set_wait_retry_state(
            "transport" if projection.detail == "transport" else "provider"
        )
    else:
        runtime.set_wait_retry_state("idle")
    await runtime.activity.ensure_wait()
    if projection.indicator == "terminal":
        await runtime.begin_terminal_wait(projection.detail)
    else:
        await runtime.end_terminal_wait()


if __name__ == '__main__':
    pass
