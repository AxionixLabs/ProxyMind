# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .content import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
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


class TerminalContentSink(ContentSink):
    """使用当前终端输出能力展示正文内容。"""

    def __init__(self, output: OutputPort) -> None:
        """绑定当前终端输出端口。"""
        self.output = output
        self._completed_item_ids: set[tuple[str, str]] = set()

    async def emit(self, output: ContentOutput) -> None:
        """把结构化正文内容映射到当前输出端。"""
        if isinstance(output, AssistantTextDelta):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            await self.output.feed(output.text, display=STREAM_OUTPUT)
            return None

        if isinstance(output, AssistantSegmentCompleted):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            if output.item_id:
                self._completed_item_ids.add(item_key)
            if output.final_text is not None:
                replace = getattr(self.output, "replace_assistant_stream", None)
                if callable(replace):
                    await replace(output.final_text)
            await self.output.settle_stream()
            self.output.mark_stream_boundary()
            return None

        if isinstance(output, AssistantOutputBoundary):
            await self.output.prepare_external_output()
            return None

        if isinstance(output, (
            AssistantPresentationSuperseded,
            AssistantResponseSuperseded,
        )):
            await self.output.prepare_external_output()
            await self.output.feed(
                "↻ Previous attempt interrupted; retrying",
                display=BLOCK_OUTPUT,
            )
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
