# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import typing

from metadata import const

TERMINAL_TITLE_SPINNER_FRAMES = (
    "⠋", "⠙", "⠹", "⠸", "⠼",
    "⠴", "⠦", "⠧", "⠇", "⠏",
)

TERMINAL_TITLE_SPINNER_INTERVAL = 0.1
TERMINAL_TITLE_ACTION_INTERVAL = 1.0

TERMINAL_TITLE_ACTION_PREFIXES = (
    "[ ! ] Action Required",
    "[ . ] Action Required",
)


class TerminalProgress(typing.Protocol):
    """描述终端标题中的运行状态。"""

    def set_workspace_title(self, title: str) -> None:
        """设置终端标题中使用的工作区名称。"""
        ...

    def begin(self) -> None:
        """进入不确定进度状态。"""
        ...

    def warning(self) -> None:
        """进入警告进度状态。"""
        ...

    def clear(self) -> None:
        """清除终端标题中的运行状态。"""
        ...

    def close(self) -> None:
        """停止标题状态并清除应用设置的标题。"""
        ...


class PassiveTerminalProgress(object):
    """提供不支持标题状态的空实现。"""

    @staticmethod
    def set_workspace_title(title: str) -> None:
        """忽略工作区标题更新。"""
        _ = title
        return None

    @staticmethod
    def begin() -> None:
        """忽略进度开始请求。"""
        return None

    @staticmethod
    def warning() -> None:
        """忽略警告状态请求。"""
        return None

    @staticmethod
    def clear() -> None:
        """忽略进度清理请求。"""
        return None

    @staticmethod
    def close() -> None:
        """忽略标题状态关闭请求。"""
        return None


class OscTerminalProgress(object):
    """通过 OSC 0 维护终端标题中的运行状态。"""

    def __init__(self, stream: typing.TextIO) -> None:
        self.stream = stream
        self._mode: typing.Literal["spinner", "action"] | None = None
        self._frame_index: int = 0
        self._title: str | None = None
        self._workspace_title: str = ""
        self._animation_task: asyncio.Task[None] | None = None

    def set_workspace_title(self, title: str) -> None:
        """更新终端标题中使用的工作区名称。"""
        workspace_title = _sanitize_title(title)
        if workspace_title == self._workspace_title:
            return None
        self._workspace_title = workspace_title
        if self._mode is None:
            self._write_title(self._workspace_title)

    def begin(self) -> None:
        """进入不确定进度状态。"""
        if self._mode == "spinner":
            return None
        self._start("spinner")

    def warning(self) -> None:
        """进入等待用户处理的标题状态。"""
        if self._mode == "action":
            return None
        self._start("action")

    def clear(self) -> None:
        """清除运行状态并恢复静止标题。"""
        if self._mode is not None:
            self._cancel_animation()
            self._mode = None
        self._write_title(self._workspace_title)

    def close(self) -> None:
        """停止动画并清除应用设置的终端标题。"""
        self._cancel_animation()
        self._mode = None
        if self._title is not None:
            self._write_title("")

    def _start(self, mode: typing.Literal["spinner", "action"]) -> None:
        """切换标题动画并立即写入首帧。"""
        self._cancel_animation()
        self._mode = mode
        self._frame_index = 0
        self._write_frame()
        self._start_animation(mode)

    async def _animate(
        self,
        mode: typing.Literal["spinner", "action"],
    ) -> None:
        """持续推进当前标题动画。"""
        interval = (
            TERMINAL_TITLE_SPINNER_INTERVAL
            if mode == "spinner"
            else TERMINAL_TITLE_ACTION_INTERVAL
        )
        try:
            while self._mode == mode:
                await asyncio.sleep(interval)
                if self._mode != mode:
                    break
                self._frame_index += 1
                self._write_frame()
        except asyncio.CancelledError:
            return None

    def _write_frame(self) -> None:
        """写入当前动画状态对应的标题。"""
        if self._mode == "spinner":
            prefix = TERMINAL_TITLE_SPINNER_FRAMES[
                self._frame_index % len(TERMINAL_TITLE_SPINNER_FRAMES)
                ]
            title = f"{prefix} {self._workspace_title or const.APP_DESC}"
        else:
            title = TERMINAL_TITLE_ACTION_PREFIXES[
                self._frame_index % len(TERMINAL_TITLE_ACTION_PREFIXES)
                ]

        self._write_title(title)

    def _start_animation(
        self,
        mode: typing.Literal["spinner", "action"],
    ) -> None:
        """在事件循环可用时启动标题动画任务。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._animation_task = loop.create_task(self._animate(mode))

    def _cancel_animation(self) -> None:
        """取消正在运行的标题动画任务。"""
        if self._animation_task is not None:
            self._animation_task.cancel()
            self._animation_task = None

    def _write_title(self, title: str) -> None:
        """写入发生变化的终端标题。"""
        if title == self._title:
            return None
        self.stream.write(f"\x1b]0;{title}\x07")
        self.stream.flush()
        self._title = title


def _sanitize_title(title: str) -> str:
    """移除终端标题中的控制字符并限制展示长度。"""
    value = "".join(
        character
        for character in str(title or "").strip()
        if ord(character) >= 0x20
        and not 0x7F <= ord(character) <= 0x9F
    )
    return value[:120]


def supports_osc_title(
    stream: typing.TextIO,
    environ: typing.Mapping[str, str] | None = None
) -> bool:
    """判断输出流是否适合写入 OSC 0 标题。"""
    isatty = getattr(stream, "isatty", None)

    try:
        interactive = bool(callable(isatty) and isatty())
    except (OSError, ValueError):
        interactive = False

    env = os.environ if environ is None else environ
    return interactive and env.get("TERM", "").lower() != "dumb"


def create_terminal_progress(
    stream: typing.TextIO,
    environ: typing.Mapping[str, str] | None = None
) -> TerminalProgress:
    """为当前终端创建标题状态实现。"""
    if supports_osc_title(stream, environ):
        return OscTerminalProgress(stream)
    return PassiveTerminalProgress()


if __name__ == '__main__':
    pass
