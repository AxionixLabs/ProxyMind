# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.protocol import CanonicalItem
from agent.ports import ModelEventStream
from protocol.schema.stream_events import (
    PresentationSupersededEvent,
    StreamEvent,
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    TurnRetryingEvent,
)
from agent.ports.transcript import TranscriptSink
from agent.ports import OutputStatusPort
from mind_app.presentation.output import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ContentSink,
    ResponseIdentity,
)
from mind_app.presentation.stream.assistant_boundary import (
    is_assistant_output_boundary,
)

_ItemRevision: typing.TypeAlias = tuple[str, int, int, int]


class ModelStreamEventHandler:
    """把 Protocol Client 的 Canonical Item 投影交付给展示和 Transcript。"""

    def __init__(
        self,
        *,
        transcript: TranscriptSink,
        content: ContentSink,
        status_control: OutputStatusPort,
        provider_retry_sink: typing.Callable[[bool], None],
        idle_reschedule: typing.Callable[[], None],
    ) -> None:
        """绑定输出端口并初始化 Transcript 交付水位。"""
        self.transcript = transcript
        self.content = content
        self.status_control = status_control
        self.provider_retry_sink = provider_retry_sink
        self.idle_reschedule = idle_reschedule
        self._item_history: tuple[CanonicalItem, ...] = ()
        self._delivered_text: dict[_ItemRevision, str] = {}
        self._completed_presentations: set[_ItemRevision] = set()
        self._presented_text_revision: _ItemRevision | None = None

    async def handle(
        self,
        event: StreamEvent,
        *,
        projection: ModelEventStream,
    ) -> bool:
        """交付一条已归约事件，并返回事件是否已被完整消费。"""
        self._item_history = projection.canonical_item_history
        current_item = projection.current_item

        if not isinstance(event, TurnRetryingEvent):
            self.provider_retry_sink(False)

        if is_assistant_output_boundary(event):
            self.flush_pending()
            self._presented_text_revision = None
            await self.content.emit(AssistantOutputBoundary())

        if isinstance(event, TurnRetryingEvent):
            await self._handle_retrying(event)
            return True
        if isinstance(event, TextDeltaEvent):
            await self._handle_text_delta(event, current_item=current_item)
            return True
        if isinstance(event, PresentationSupersededEvent):
            await self._handle_presentation_superseded(event)
            return True
        if isinstance(event, TextDoneEvent):
            await self._handle_text_done(event, current_item=current_item)
            return True
        if isinstance(event, TextMetaEvent):
            return True
        return False

    def flush_pending(self, *, complete_only: bool = False) -> None:
        """把 Canonical text Items 幂等同步到 Transcript。"""
        for item in self._item_history:
            if item.item_kind != "text":
                continue
            if complete_only and item.item_status != "completed":
                continue
            self._synchronize_transcript_item(item)

    async def _handle_retrying(self, event: TurnRetryingEvent) -> None:
        """提交旧 attempt 的审计正文并投影替换边界。"""
        self.provider_retry_sink(True)
        self.flush_pending()
        replaced_items = tuple(
            item
            for item in self._item_history
            if item.item_kind == "text"
            and item.superseded_by_attempt == event.attempt
            and bool(str(item.payload_value().get("text") or "").strip())
        )
        self._presented_text_revision = None

        if replaced_items:
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

    async def _handle_text_delta(
        self,
        event: TextDeltaEvent,
        *,
        current_item: CanonicalItem | None,
    ) -> None:
        """把当前 canonical text Item 的新增 delta 交给展示层。"""
        item = _matching_text_item(event, current_item)
        if item is None or not event.text:
            return
        revision = _item_revision(item)
        if (
            self._presented_text_revision is not None
            and self._presented_text_revision != revision
        ):
            self.flush_pending(complete_only=True)
            await self.content.emit(AssistantOutputBoundary())
        self._presented_text_revision = revision
        await self.content.emit(AssistantTextDelta(
            event.text,
            _response_identity(item),
            item_id=item.item_id,
        ))
        self.idle_reschedule()

    async def _handle_presentation_superseded(
        self,
        event: PresentationSupersededEvent,
    ) -> None:
        """提交旧展示正文并投影 Worker 展示代次替换边界。"""
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
        self._presented_text_revision = None
        await self.content.emit(AssistantPresentationSuperseded(
            turn_id=event.turn_id,
            superseded_epoch=event.superseded_epoch,
            presentation_epoch=event.presentation_epoch,
        ))

    async def _handle_text_done(
        self,
        event: TextDoneEvent,
        *,
        current_item: CanonicalItem | None,
    ) -> None:
        """完成当前 canonical text Item 并同步已交付文本的修订。"""
        item = _matching_text_item(event, current_item)
        if item is None:
            return
        revision = _item_revision(item)
        if revision in self._completed_presentations:
            return
        if revision in self._delivered_text and event.final_text is not None:
            self._synchronize_transcript_item(item)
        self._completed_presentations.add(revision)
        self._presented_text_revision = None
        await self.content.emit(AssistantSegmentCompleted(
            _response_identity(item),
            final_text=event.final_text,
            item_id=item.item_id,
        ))
        await self.status_control.begin_reply_wait_status()

    def _synchronize_transcript_item(self, item: CanonicalItem) -> None:
        """按 Item revision 创建或更新一条 Transcript assistant 消息。"""
        text = str(item.payload_value().get("text") or "").strip()
        if not text:
            return
        revision = _item_revision(item)
        previous = self._delivered_text.get(revision)
        if previous == text:
            return
        self.transcript.append(
            "message.created" if previous is None else "message.updated",
            actor="assistant",
            payload={
                "content": text,
                "item_id": item.item_id,
                "presentation_epoch": item.presentation_epoch,
                "round": item.round_no,
                "attempt": item.attempt,
            },
        )
        self._delivered_text[revision] = text


def _matching_text_item(
    event: TextDeltaEvent | TextDoneEvent,
    item: CanonicalItem | None,
) -> CanonicalItem | None:
    """校验最近投影确实属于当前正文事件。"""
    if item is None:
        return None
    if item.item_kind != "text" or item.item_id != event.item_id:
        raise ValueError("canonical text projection does not match event")
    return item


def _item_revision(item: CanonicalItem) -> _ItemRevision:
    """返回 Transcript 与展示交付使用的稳定 Item revision。"""
    return (
        item.item_id,
        item.presentation_epoch,
        item.round_no,
        item.attempt,
    )


def _response_identity(item: CanonicalItem) -> ResponseIdentity:
    """把 Canonical Item 身份映射为机器输出响应身份。"""
    return ResponseIdentity(
        turn_id=item.turn_id,
        presentation_epoch=item.presentation_epoch,
        round=item.round_no,
        attempt=item.attempt,
    )


if __name__ == '__main__':
    pass
