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
from ..core.runtime import TuiRuntime


def create_tui_output_session(
    log_file: str,
    *,
    context: OutputSurfaceContext,
    animate: bool = True,
    runtime: TuiRuntime,
) -> OutputSession:
    """创建持久终端 TUI 对应的单轮输出会话。"""
    activity = TuiTurnSurfaceCoordinator(
        context,
        lambda projection: _apply_surface_projection(runtime, projection),
        apply_immediate_projection=(
            lambda projection: _apply_immediate_surface_projection(
                runtime,
                projection,
            )
        ),
    )
    control = TuiOutputControl(
        log_file,
        runtime=runtime,
        activity=activity,
        surface_context=context,
        assistant_visible=activity.emit_assistant_visible,
        animate=animate,
    )
    presentation = TuiPresentationSink(control)
    return OutputSession(
        context=context,
        control=control,
        activity=activity,
        content=TuiContentSink(
            control,
            before_assistant_output=(
                presentation.flush_terminal_waits_before_assistant_output
            ),
            surface_context=context,
        ),
        presentation=presentation,
    )


async def _apply_surface_projection(
    runtime: TuiRuntime,
    projection: SurfaceProjection,
) -> None:
    """把单一 Turn 表面投影提交给现有前景活动槽。"""
    if runtime.turn_output_suppressed or projection.indicator == "hidden":
        runtime.activity.finish_wait()
        return None

    await runtime.activity.show_turn_surface(
        projection.indicator,
        title=projection.title,
        detail=projection.detail,
    )


def _apply_immediate_surface_projection(
    runtime: TuiRuntime,
    projection: SurfaceProjection,
) -> None:
    """在正文画布事务内同步释放活动区域。"""
    if projection.indicator != "hidden":
        raise ValueError("immediate surface projection must be hidden")
    if runtime.activity.finish_wait():
        runtime.screen.synchronize_next_render()


if __name__ == '__main__':
    pass
