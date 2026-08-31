# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

StartupAnimation: typing.TypeAlias = typing.Callable[
    [], typing.Awaitable[None]
]

StartupFinalFrame: typing.TypeAlias = typing.Callable[[], None]


@dataclass(frozen=True, slots=True)
class StartupPresentation(object):
    """描述一项启动动画及可选的直接结算帧。"""
    animation: StartupAnimation
    final_frame: StartupFinalFrame | None = None


class StartupPresentationQueue(object):
    """按一次性语义保存启动展示，并原子转移待处理批次。"""

    def __init__(self) -> None:
        self._presentations: list[StartupPresentation] = []

    def __bool__(self) -> bool:
        return bool(self._presentations)

    def register(
        self,
        animation: StartupAnimation,
        *,
        final_frame: StartupFinalFrame | None,
    ) -> bool:
        """注册首项启动展示，后续注册请求保持幂等。"""
        if self._presentations:
            return False
        self._presentations.append(StartupPresentation(
            animation=animation,
            final_frame=final_frame,
        ))
        return True

    def take(self) -> tuple[StartupPresentation, ...]:
        """转移并清空待播放启动展示。"""
        presentations = tuple(self._presentations)
        self._presentations.clear()
        return presentations

    def clear(self) -> None:
        """丢弃尚未播放的启动展示。"""
        self._presentations.clear()


if __name__ == '__main__':
    pass
