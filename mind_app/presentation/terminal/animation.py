# -*- coding: utf-8 -*-

import typing

from agent.ports import TurnAnimationPort
from ..application import (
    ActivityStatusKind,
    FrontendRuntime,
)


class _StopAnimation(typing.Protocol):
    """定义终端动画停止回调的实现签名。"""

    async def __call__(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True,
    ) -> None:
        ...


class TurnAnimationAdapter(TurnAnimationPort):
    """把前端活动 runtime 适配为模型轮次等待动画端口。"""

    def __init__(
        self,
        runtime: FrontendRuntime,
        stop_animation: _StopAnimation,
    ) -> None:
        """绑定前端状态和 Controller 动画停止动作。"""
        self._runtime = runtime
        self._stop_animation = stop_animation

    @property
    def active(self) -> bool:
        """返回前台是否正在接管等待动画。"""
        return self._runtime.active

    async def stop_wait(self, *, settle: bool = True) -> None:
        """停止模型轮次等待动画。"""
        await self._stop_animation("wait", settle=settle)


__all__ = ("TurnAnimationAdapter",)
