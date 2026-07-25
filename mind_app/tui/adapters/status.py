# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.output import OutputStatusPort


class TuiStreamStatusControl(OutputStatusPort):
    """隔离共享事件流的局部状态与 TUI 整轮活动状态。"""

    async def begin_tool_status(self) -> None:
        """保持当前 TUI 整轮活动状态。"""
        return None

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """忽略事件级自定义状态切换。"""
        _ = text
        return None

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        """保持由 TUI 轮次生命周期统一持有的等待状态。"""
        _ = text, delay_sec, animate_after_sec
        return None

    async def end_status(self, *, immediate: bool = False) -> None:
        """忽略事件级状态结束通知。"""
        _ = immediate
        return None


if __name__ == '__main__':
    pass
