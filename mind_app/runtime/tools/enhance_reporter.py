# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import (
    BLOCK_OUTPUT,
    OutputPort
)


class OutputEnhanceReporter(object):
    """把结果增强过程适配到当前输出端。"""

    def __init__(self, output: OutputPort) -> None:
        self.output = output

    async def record(self, text: str) -> None:
        """记录不直接展示的增强内容。"""
        await self.output.feed(text, echo=False, display=BLOCK_OUTPUT)

    async def display(self, text: str) -> None:
        """展示增强过程产生的文本。"""
        await self.output.feed(text, display=BLOCK_OUTPUT)

    async def begin_status(self) -> None:
        """启动增强过程状态。"""
        await self.output.begin_tool_status()

    async def end_status(self) -> None:
        """结束增强过程状态。"""
        await self.output.end_status()


if __name__ == '__main__':
    pass
