# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    AssistantVisible,
    ContentOutput,
    ContentSink,
    OutputSurfaceContext,
    ResponseIdentity,
    SourcesOutput,
)
from frontends.output.source_text import render_sources_text
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
        surface_context: OutputSurfaceContext,
    ) -> None:
        """绑定持久终端界面的输出控制器。"""
        self.output = output
        self._before_assistant_output = before_assistant_output
        self._surface_context = surface_context
        self._completed_item_ids: set[tuple[str, str]] = set()

    async def _flush_before_assistant_output(self) -> None:
        """在任何可见 assistant 内容提交前结算后台终端等待。"""
        if self._before_assistant_output is not None:
            await self._before_assistant_output()

    async def emit(self, output: ContentOutput) -> None:
        """发送 assistant 增量或来源展示块。"""
        if isinstance(output, AssistantTextDelta):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            self._bind_visibility(output.identity, output.item_id)
            await self._flush_before_assistant_output()
            await self.output.append_assistant_delta(output.text)
            return None
        if isinstance(output, AssistantSegmentCompleted):
            item_key = (output.identity.turn_id, output.item_id)
            if output.item_id and item_key in self._completed_item_ids:
                return None
            if output.item_id:
                self._completed_item_ids.add(item_key)
            self._bind_visibility(output.identity, output.item_id)
            if output.final_text is not None:
                replace = getattr(self.output, "replace_assistant_stream", None)
                if callable(replace):
                    await replace(output.final_text)
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

    def _bind_visibility(
        self,
        identity: ResponseIdentity,
        item_id: str,
    ) -> None:
        """把正式 Item 身份绑定到下一次真实正文上屏。"""
        context = self._surface_context
        if not item_id:
            return None
        self.output.bind_assistant_visibility(AssistantVisible(
            surface_id=context.surface_id,
            turn_id=context.turn_id,
            identity=identity,
            item_id=item_id,
        ))


if __name__ == '__main__':
    pass
