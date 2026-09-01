# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntroFrame(object):
    """描述启动标题动画中的一帧。"""
    prompt_on: bool
    title_visible: int
    version_visible: bool
    delay_after: float


def intro_frames(title: str) -> tuple[IntroFrame, ...]:
    """生成 TUI 启动标题使用的帧计划。"""
    title_length = len(str(title or ""))

    frames = [
        IntroFrame(True, 0, False, 0.050),
        IntroFrame(False, 0, False, 0.045),
        IntroFrame(True, 0, False, 0.060),
    ]

    frames.extend(
        IntroFrame(
            True,
            visible,
            False,
            0.045 if visible < title_length else 0.080,
        )
        for visible in range(1, title_length + 1)
    )
    frames.append(IntroFrame(True, title_length, True, 0.180))
    return tuple(frames)


if __name__ == '__main__':
    pass
