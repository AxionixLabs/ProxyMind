# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import time
import typing

from agent.application.turns.run_result import RunResult
from agent.ports.frontend import (
    FrontendActivityPort,
    FrontendPort,
    TurnCompletionPresenterPort,
)
from agent.ports.presentation import (
    ApplicationSink,
    TurnForegroundLifecyclePort,
)
from agent.ports.process_lifecycle import ProcessLifecyclePort
from agent.ports.turns import TurnAnimationPort

ForegroundParameters = typing.ParamSpec("ForegroundParameters")


class FrontendTurnAnimation(TurnAnimationPort):
    """把应用活动状态适配为模型轮次等待动画端口。"""

    def __init__(
        self,
        activity: FrontendActivityPort,
    ) -> None:
        """绑定活动状态和停止动作。"""
        self._activity = activity

    @property
    def active(self) -> bool:
        """返回前端是否正在接管等待动画。"""
        return self._activity.active

    async def stop_wait(self, *, settle: bool = True) -> None:
        """停止模型轮次等待动画。"""
        await self._activity.stop("wait", settle=settle)


class ApplicationTurnForegroundLifecycle(TurnForegroundLifecyclePort):
    """把应用宿主适配为通用前台轮次生命周期端口。"""

    def __init__(
        self,
        frontend: FrontendPort,
        activity: FrontendActivityPort,
        lifecycle: ProcessLifecyclePort,
        completion_presenter: TurnCompletionPresenterPort,
    ) -> None:
        """绑定显式前端、活动和进程生命周期。"""
        self._frontend = frontend
        self._activity = activity
        self._lifecycle = lifecycle
        self._completion_presenter = completion_presenter

    @property
    def application(self) -> ApplicationSink:
        """返回当前前端的应用展示端。"""
        return self._frontend.application

    @property
    def animate(self) -> bool:
        """返回当前是否启用活动展示。"""
        return self._activity.enabled

    def begin_terminal_progress(self) -> None:
        """启动前台轮次进度。"""
        self._frontend.runtime.begin_terminal_progress()

    def end_terminal_progress(self) -> None:
        """结束前台轮次进度。"""
        self._frontend.runtime.end_terminal_progress()

    async def start_animation(self) -> None:
        """开始当前轮次动画。"""
        await self._activity.start_wait()

    def finish_turn_wait(self) -> None:
        """结束当前轮次等待展示。"""
        self._frontend.runtime.finish_turn_wait()

    def emit_worked_footer(self, elapsed_seconds: float) -> None:
        """提交当前轮次完成展示。"""
        self._completion_presenter(self.application, elapsed_seconds)

    async def stop_animation(self) -> None:
        """停止当前轮次动画。"""
        await self._activity.stop("wait")

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """等待当前轮次资源清理完成。"""
        await self._lifecycle.await_cleanup(awaitable)


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
