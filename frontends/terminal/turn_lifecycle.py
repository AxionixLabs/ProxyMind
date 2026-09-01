# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing

from agent.application.turns.run_result import RunResult
from agent.ports.presentation import (
    ApplicationSink,
    TurnForegroundLifecyclePort,
)
from frontends.runtime import (
    ActivityStatusKind,
    Frontend,
)
from frontends.terminal.worked import emit_worked_footer


class TerminalTurnHost(typing.Protocol):
    """描述终端轮次生命周期适配器所需的最小宿主。

    实现方持有前端和动画清理生命周期；适配器只协调一次前台 Turn，不保存或
    修改 Harness 状态。
    """

    frontend: Frontend
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
        awaitable: typing.Awaitable[None],
    ) -> None:
        """在调用方取消时仍等待清理完成。"""
        ...


class ControllerTurnForegroundLifecycle(TurnForegroundLifecyclePort):
    """把 Controller 的前台运行能力适配为终端轮次生命周期端口。"""

    def __init__(self, controller: TerminalTurnHost) -> None:
        """绑定组合根提供的 Controller 生命周期实现。"""
        self._controller = controller

    @property
    def application(self) -> ApplicationSink:
        """返回 Controller 当前前端的应用展示端。"""
        return self._controller.frontend.application

    @property
    def animate(self) -> bool:
        """返回当前是否启用终端动画。"""
        return bool(self._controller.animate)

    def begin_terminal_progress(self) -> None:
        """启动终端轮次进度。"""
        self._controller.frontend.runtime.begin_terminal_progress()

    def end_terminal_progress(self) -> None:
        """结束终端轮次进度。"""
        self._controller.frontend.runtime.end_terminal_progress()

    async def start_animation(self) -> None:
        """开始当前轮次动画。"""
        await self._controller.start_anim()

    def finish_turn_wait(self) -> None:
        """结束当前轮次等待展示。"""
        self._controller.frontend.runtime.finish_turn_wait()

    async def stop_animation(self) -> None:
        """停止当前轮次动画。"""
        await self._controller.stop_anim("wait")

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """等待当前轮次资源清理完成。"""
        await self._controller.await_cleanup(awaitable)


async def run_foreground_turn(
    lifecycle: TurnForegroundLifecyclePort,
    operation: typing.Callable[..., typing.Awaitable[RunResult]],
    *args: typing.Any,
    **kwargs: typing.Any,
) -> RunResult:
    """在终端进度和动画生命周期内执行一次轮次操作。"""
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
                emit_worked_footer(
                    lifecycle.application,
                    time.perf_counter() - started_at,
                )
        finally:
            try:
                await lifecycle.await_cleanup(lifecycle.stop_animation())
            finally:
                lifecycle.end_terminal_progress()


__all__ = (
    "ControllerTurnForegroundLifecycle",
    "run_foreground_turn",
)
