# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import BLOCK_OUTPUT, OutputPort
from rich.cells import cell_len
from ..contracts import PresentationSink, PresentationView
from ..renderers.dispatch import render_presentation_view


class RichPresentationSink(PresentationSink):
    """使用 Rich 输出能力展示结构化数据。"""

    def __init__(self, output: OutputPort) -> None:
        self.output = output

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        for block in render_presentation_view(
            view,
            terminal_width=self.output.terminal_width,
            measure_width=cell_len,
        ):
            display_parts = list(block.spans) if block.spans else None
            if block.direct:
                await self.output.print_block(
                    block.plain_text,
                    display_parts=display_parts,
                )
                continue
            await self.output.feed(
                block.plain_text,
                display=BLOCK_OUTPUT,
                display_parts=display_parts,
                preserve_display_parts=block.preserve_spans,
            )
if __name__ == '__main__':
    pass
