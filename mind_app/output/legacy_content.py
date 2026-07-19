# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .content import (
    AssistantTextDelta,
    ContentOutput,
    ContentSink,
    SourcesOutput
)
from .contracts import (
    BLOCK_OUTPUT,
    STREAM_OUTPUT,
    OutputPort
)
from .source_text import render_sources_text


class LegacyContentSink(ContentSink):
    """使用当前 OutputPort 行为输出正文内容。"""

    def __init__(self, output: OutputPort) -> None:
        self.output = output

    async def emit(self, output: ContentOutput) -> None:
        """把结构化正文内容映射到当前输出端。"""
        if isinstance(output, AssistantTextDelta):
            await self.output.feed(output.text, display=STREAM_OUTPUT)
            return None

        if isinstance(output, SourcesOutput):
            await self.output.feed(
                render_sources_text(output.sources),
                display=BLOCK_OUTPUT,
            )
            return None

        raise TypeError(f"Unsupported content output: {type(output).__name__}")


if __name__ == '__main__':
    pass
