# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ContentOutput,
    ContentSink,
    SourcesOutput
)
from mind_app.output.source_text import render_sources_text
from .output import TuiOutputControl


class TuiContentSink(ContentSink):
    """把结构化正文写入持久 TUI。"""

    def __init__(self, output: TuiOutputControl) -> None:
        self.output = output

    async def emit(self, output: ContentOutput) -> None:
        """发送 assistant 增量或来源展示块。"""
        if isinstance(output, AssistantTextDelta):
            await self.output.append_assistant_delta(output.text)
            return None
        if isinstance(output, AssistantSegmentCompleted):
            await self.output.settle_stream()
            self.output.mark_stream_boundary()
            return None
        if isinstance(output, AssistantOutputBoundary):
            await self.output.prepare_external_output()
            return None
        if isinstance(output, SourcesOutput):
            await self.output.append_assistant_metadata(
                render_sources_text(output.sources)
            )
            return None
        raise TypeError(f"Unsupported TUI content output: {type(output).__name__}")


if __name__ == '__main__':
    pass
