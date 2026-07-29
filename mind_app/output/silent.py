# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.contracts import (
    PresentationSink,
    PresentationView
)
from .content import (
    ContentOutput,
    ContentSink
)
from .contracts import (
    OutputControlPort,
    OutputStatusPort
)
from .session import OutputSession


class SilentOutputControl(OutputControlPort, OutputStatusPort):
    """提供不写入终端的单轮输出控制。"""

    async def open(self) -> None:
        """忽略输出会话启动。"""
        return None

    async def stop(self, *, blink: bool = True) -> None:
        """忽略输出会话停止。"""
        _ = blink
        return None

    async def record_hidden_output(self, text: str) -> None:
        """忽略隐藏输出记录。"""
        _ = text
        return None

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: str | None = None,
    ) -> None:
        """忽略工具参数展示。"""
        _ = name, arguments, call_id
        return None

    async def begin_tool_status(self) -> None:
        """忽略工具状态。"""
        return None

    async def begin_custom_tool_status(self, text: str | None) -> None:
        """忽略自定义工具状态。"""
        _ = text
        return None

    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        """忽略回复等待状态。"""
        _ = text, delay_sec, animate_after_sec
        return None

    async def end_status(self, *, immediate: bool = False) -> None:
        """忽略状态结束。"""
        _ = immediate
        return None


class SilentContentSink(ContentSink):
    """忽略无终端前端的正文事件。"""

    async def emit(self, output: ContentOutput) -> None:
        """忽略正文事件。"""
        _ = output
        return None


class SilentPresentationSink(PresentationSink):
    """忽略无终端前端的展示事件。"""

    async def emit(self, view: PresentationView) -> None:
        """忽略展示事件。"""
        _ = view
        return None


def create_silent_output_session(
    log_file: str,
    *,
    animate: bool = True,
) -> OutputSession:
    """创建不写入终端的单轮输出会话。"""
    _ = log_file, animate

    control = SilentOutputControl()

    return OutputSession(
        control=control,
        status=control,
        content=SilentContentSink(),
        presentation=SilentPresentationSink(),
    )


if __name__ == '__main__':
    pass
