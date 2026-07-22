# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from .progress import show_tool_progress


class ToolEnhanceReporter(object):
    """把结果增强过程适配到运行时输出边界。"""

    def __init__(
        self,
        output: OutputControlPort,
        status: OutputStatusPort,
        presentation: PresentationSink,
        *,
        tool_name: str,
    ) -> None:
        self.output       = output
        self.status       = status
        self.presentation = presentation
        self.tool_name    = tool_name

    async def record(self, text: str) -> None:
        """记录不直接展示的增强内容。"""
        await self.output.record_hidden_output(text)

    async def display(self, text: str) -> None:
        """展示增强过程产生的文本。"""
        await show_tool_progress(
            self.presentation,
            text,
            source="enhancement",
            tool_name=self.tool_name,
        )

    async def begin_status(self) -> None:
        """启动增强过程状态。"""
        await self.status.begin_tool_status()

    async def end_status(self) -> None:
        """结束增强过程状态。"""
        await self.status.end_status()


if __name__ == '__main__':
    pass
