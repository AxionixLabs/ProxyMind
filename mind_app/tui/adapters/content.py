# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
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

    def __init__(
        self,
        output: TuiOutputControl,
        *,
        before_assistant_output: (
            typing.Callable[[], typing.Awaitable[None]] | None
        ) = None,
    ) -> None:
        """绑定持久终端界面的输出控制器。"""
        self.output = output
        self._before_assistant_output = before_assistant_output

    async def _flush_before_assistant_output(self) -> None:
        """在任何可见 assistant 内容提交前结算后台终端等待。"""
        if self._before_assistant_output is not None:
            await self._before_assistant_output()

    async def emit(self, output: ContentOutput) -> None:
        """发送 assistant 增量或来源展示块。"""
        if isinstance(output, AssistantTextDelta):
            await self._flush_before_assistant_output()
            await self.output.append_assistant_delta(output.text)
            return None
        if isinstance(output, AssistantSegmentCompleted):
            await self.output.settle_stream()
            self.output.mark_stream_boundary()
            return None
        if isinstance(output, AssistantOutputBoundary):
            await self._flush_before_assistant_output()
            await self.output.prepare_external_output()
            return None
        if isinstance(output, (
            AssistantPresentationSuperseded,
            AssistantResponseSuperseded,
        )):
            await self._flush_before_assistant_output()
            await self.output.supersede_assistant_presentation()
            return None
        if isinstance(output, SourcesOutput):
            await self._flush_before_assistant_output()
            await self.output.append_assistant_metadata(
                render_sources_text(output.sources)
            )
            return None
        raise TypeError(f"Unsupported TUI content output: {type(output).__name__}")


if __name__ == '__main__':
    pass
