# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import time
import typing

from agent.application.turns.run_result import RunResult
from agent.ports.frontend import (
    ActivityRuntimePort,
    ActivityStatusKind,
    FrontendPort,
    TurnCompletionPresenterPort,
)
from agent.ports.presentation import (
    ApplicationSink,
    TurnForegroundLifecyclePort,
)
from agent.ports.turns import TurnAnimationPort

ForegroundParameters = typing.ParamSpec("ForegroundParameters")
CleanupResult = typing.TypeVar("CleanupResult")


class ForegroundTurnHost(typing.Protocol):
    """描述前台轮次生命周期适配器需要的最小应用宿主。"""

    frontend: FrontendPort
    animate: bool

    async def start_anim(self) -> None:
        """开始当前轮次动画。"""
        ...

    async def stop_anim(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """停止指定活动动画。"""
        ...

    async def await_cleanup(
        self,
        awaitable: typing.Awaitable[CleanupResult],
    ) -> CleanupResult:
        """在取消边界内等待清理完成。"""
        ...


class _StopActivity(typing.Protocol):
    """定义活动状态停止动作。"""

    async def __call__(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        ...


class FrontendTurnAnimation(TurnAnimationPort):
    """把应用活动状态适配为模型轮次等待动画端口。"""

    def __init__(
        self,
        runtime: ActivityRuntimePort,
        stop_activity: _StopActivity,
    ) -> None:
        """绑定活动状态和停止动作。"""
        self._runtime = runtime
        self._stop_activity = stop_activity

    @property
    def active(self) -> bool:
        """返回前端是否正在接管等待动画。"""
        return self._runtime.active

    async def stop_wait(self, *, settle: bool = True) -> None:
        """停止模型轮次等待动画。"""
        await self._stop_activity("wait", settle=settle)


class ApplicationTurnForegroundLifecycle(TurnForegroundLifecyclePort):
    """把应用宿主适配为通用前台轮次生命周期端口。"""

    def __init__(
        self,
        host: ForegroundTurnHost,
        completion_presenter: TurnCompletionPresenterPort,
    ) -> None:
        """绑定宿主生命周期和前端完成投影。"""
        self._host = host
        self._completion_presenter = completion_presenter

    @property
    def application(self) -> ApplicationSink:
        """返回当前前端的应用展示端。"""
        return self._host.frontend.application

    @property
    def animate(self) -> bool:
        """返回当前是否启用活动展示。"""
        return self._host.animate

    def begin_terminal_progress(self) -> None:
        """启动前台轮次进度。"""
        self._host.frontend.runtime.begin_terminal_progress()

    def end_terminal_progress(self) -> None:
        """结束前台轮次进度。"""
        self._host.frontend.runtime.end_terminal_progress()

    async def start_animation(self) -> None:
        """开始当前轮次动画。"""
        await self._host.start_anim()

    def finish_turn_wait(self) -> None:
        """结束当前轮次等待展示。"""
        self._host.frontend.runtime.finish_turn_wait()

    def emit_worked_footer(self, elapsed_seconds: float) -> None:
        """提交当前轮次完成展示。"""
        self._completion_presenter(self.application, elapsed_seconds)

    async def stop_animation(self) -> None:
        """停止当前轮次动画。"""
        await self._host.stop_anim("wait")

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """等待当前轮次资源清理完成。"""
        await self._host.await_cleanup(awaitable)


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
