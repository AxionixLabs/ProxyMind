# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.contracts import PresentationSink, PresentationView
from mind_app.output import BLOCK_OUTPUT
from mind_app.presentation.renderers.dispatch import render_presentation_view
from prompt_toolkit.utils import get_cwidth
from ..core.models import FragmentBlock
from ..core.styles import styled_block_fragments
from .output import TuiOutputControl


class TuiPresentationSink(PresentationSink):
    """把结构化展示数据写入持久 TUI。"""

    def __init__(self, output: TuiOutputControl) -> None:
        self.output = output

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        for block in render_presentation_view(
            view,
            terminal_width=self.output.terminal_width,
            measure_width=get_cwidth,
        ):
            if not block.direct:
                await self.output.feed(
                    block.plain_text,
                    display=BLOCK_OUTPUT,
                    display_parts=list(block.spans) if block.spans else None,
                    preserve_display_parts=block.preserve_spans,
                )
                continue
            await self.output.append_styled_block(
                block,
                FragmentBlock(styled_block_fragments(block)),
            )


if __name__ == '__main__':
    pass
