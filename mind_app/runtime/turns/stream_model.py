# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.stream_events import (
    PresentationSupersededEvent,
    StreamEvent,
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    ToolBuiltinDoneEvent,
    TurnRetryingEvent,
)
from mind_app.history.contracts import TranscriptSink
from mind_app.output import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ContentSink,
    OutputStatusPort,
    ResponseIdentity,
)
from mind_app.stream_events.assistant_boundary import (
    is_assistant_output_boundary,
)
from mind_app.stream_state.segment import SegmentTracker


class ModelStreamEventHandler:
    """在单轮生命周期内投影模型正文、重试和展示替换事件。"""

    def __init__(
        self,
        *,
        transcript: TranscriptSink,
        content: ContentSink,
        status_control: OutputStatusPort,
        provider_retry_sink: typing.Callable[[bool], None],
        idle_reschedule: typing.Callable[[], None],
    ) -> None:
        """绑定单轮输出端口并创建专属正文状态。"""
        self.transcript = transcript
        self.content = content
        self.status_control = status_control
        self.provider_retry_sink = provider_retry_sink
        self.idle_reschedule = idle_reschedule
        self._tracker = SegmentTracker()

    @property
    def sources(self) -> tuple[typing.Any, ...]:
        """返回未被取代正文关联的来源快照。"""
        return tuple(self._tracker.iter_sources())

    async def handle(self, event: StreamEvent) -> bool:
        """处理模型输出事件，并返回事件是否已经被完整消费。"""
        if not isinstance(event, TurnRetryingEvent):
            self.provider_retry_sink(False)

        if is_assistant_output_boundary(event):
            self.flush_pending()
            await self.content.emit(AssistantOutputBoundary())

        if isinstance(event, TurnRetryingEvent):
            await self._handle_retrying(event)
            return True
        if isinstance(event, TextDeltaEvent):
            await self._handle_text_delta(event)
            return True
        if isinstance(event, PresentationSupersededEvent):
            await self._handle_presentation_superseded(event)
            return True
        if isinstance(event, TextDoneEvent):
            await self._handle_text_done(event)
            return True
        if isinstance(event, TextMetaEvent):
            self._tracker.on_text_meta(event)
            return True
        return False

    def record_builtin_sources(self, event: ToolBuiltinDoneEvent) -> None:
        """把内置工具来源关联到当前或下一正文段。"""
        self._tracker.on_builtin_done(event)

    def flush_pending(self, *, complete_only: bool = False) -> None:
        """把待提交的 assistant item 按稳定身份写入会话记录。"""
        for identity, item_id, output in self._tracker.drain_assistant_outputs(
            complete_only=complete_only,
        ):
            if not output:
                continue
            epoch, round_no, attempt = identity
            self.transcript.append(
                "message.created",
                actor="assistant",
                payload={
                    "content": output,
                    "item_id": item_id,
                    "presentation_epoch": epoch,
                    "round": round_no,
                    "attempt": attempt,
                },
            )

    async def _handle_retrying(self, event: TurnRetryingEvent) -> None:
        """淘汰旧 provider attempt 并恢复回复等待状态。"""
        self.provider_retry_sink(True)
        self.flush_pending()
        had_assistant_output = self._tracker.on_turn_retrying(event)

        if had_assistant_output:
            payload: dict[str, typing.Any] = {
                "scope": "response",
                "presentation_epoch": event.presentation_epoch,
                "round": event.round,
                "attempt": event.attempt,
                "reason": event.reason,
            }
            if event.supersedes_item_id:
                payload["supersedes_item_id"] = event.supersedes_item_id
            self.transcript.append(
                "message.superseded",
                actor="assistant",
                payload=payload,
            )
            await self.content.emit(AssistantResponseSuperseded(
                turn_id=event.turn_id,
                presentation_epoch=event.presentation_epoch,
                round=event.round,
                attempt=event.attempt,
                item_id=event.supersedes_item_id,
            ))
        await self.status_control.begin_reply_wait_status()

    async def _handle_text_delta(self, event: TextDeltaEvent) -> None:
        """追加一个正文增量并在 item 切换时提交上一项。"""
        event_identity = self._tracker.response_identity(event)
        if self._tracker.should_ignore_item(
            event.item_id,
            identity=event_identity,
        ):
            return

        identity = self._response_identity(event)
        item_changed = self._tracker.on_text_delta(event)

        if item_changed:
            self._tracker.defer_current_output()
            self.flush_pending(complete_only=True)
            self._tracker.remember_current_output()
            await self.content.emit(AssistantOutputBoundary())
        await self.content.emit(AssistantTextDelta(
            event.text,
            identity,
            item_id=event.item_id,
        ))
        self.idle_reschedule()

    async def _handle_presentation_superseded(
        self,
        event: PresentationSupersededEvent,
    ) -> None:
        """提交旧展示输出并把对应代次移出规范正文。"""
        self.flush_pending()
        self.transcript.append(
            "message.superseded",
            actor="assistant",
            payload={
                "scope": "presentation",
                "presentation_epoch": event.superseded_epoch,
                "superseded_by_epoch": event.presentation_epoch,
                "reason": event.reason,
            },
        )
        self._tracker.on_presentation_superseded(event)
        await self.content.emit(AssistantPresentationSuperseded(
            turn_id=event.turn_id,
            superseded_epoch=event.superseded_epoch,
            presentation_epoch=event.presentation_epoch,
        ))

    async def _handle_text_done(self, event: TextDoneEvent) -> None:
        """完成正文 item，并在需要时修正已提交的最终文本。"""
        event_identity = self._tracker.response_identity(event)
        if self._tracker.should_ignore_item(
            event.item_id,
            identity=event_identity,
        ):
            return

        identity = self._response_identity(event)
        output_was_drained = self._tracker.was_output_drained(event.item_id)
        self._tracker.on_text_done(event)

        if output_was_drained and event.final_text is not None:
            self.transcript.append(
                "message.updated",
                actor="assistant",
                payload={
                    "content": event.final_text,
                    "item_id": event.item_id,
                    "presentation_epoch": identity.presentation_epoch,
                    "round": identity.round,
                    "attempt": identity.attempt,
                },
            )
        await self.content.emit(AssistantSegmentCompleted(
            identity,
            final_text=event.final_text,
            item_id=event.item_id,
        ))
        await self.status_control.begin_reply_wait_status()

    def _response_identity(self, event: StreamEvent) -> ResponseIdentity:
        """把模型事件映射为机器输出使用的稳定响应身份。"""
        epoch, round_no, attempt = self._tracker.response_identity(event)
        return ResponseIdentity(
            turn_id=event.turn_id,
            presentation_epoch=epoch,
            round=round_no,
            attempt=attempt,
        )


if __name__ == '__main__':
    pass
