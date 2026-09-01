# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

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

    def emit_worked_footer(self, elapsed_seconds: float) -> None:
        """向当前终端应用提交轮次耗时展示。"""
        emit_worked_footer(self.application, elapsed_seconds)

    async def stop_animation(self) -> None:
        """停止当前轮次动画。"""
        await self._controller.stop_anim("wait")

    async def await_cleanup(self, awaitable: typing.Awaitable[None]) -> None:
        """等待当前轮次资源清理完成。"""
        await self._controller.await_cleanup(awaitable)

__all__ = (
    "ControllerTurnForegroundLifecycle",
)
