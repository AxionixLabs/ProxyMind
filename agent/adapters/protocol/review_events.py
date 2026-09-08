# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.adapters.protocol.turn_projection import TurnEventProjection
from agent.application.turns.run_result import RunStatus
from agent.application.views.builders.review import (
    build_review_cancelled_view,
    build_review_completed_view,
    build_review_failed_view,
    build_review_finished_view,
    build_review_reconciliation_view,
    build_review_started_view,
    review_output_text,
)
from agent.ports.presentation import ApplicationSink
from agent.protocol import CanonicalItem
from protocol.schema.review import parse_review_output
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
    ReviewStartedEvent,
    StreamEvent,
)

AssistantReplySink: typing.TypeAlias = typing.Callable[[str], None]


class ReviewEventProjector(TurnEventProjection):
    """只投影 Review Item，并让共享 Turn 事件泵继续拥有执行生命周期。"""

    def __init__(
        self,
        application: ApplicationSink,
        *,
        hint: str,
        assistant_reply_sink: AssistantReplySink | None = None,
    ) -> None:
        """绑定应用输出和当前 Review 的公开目标摘要。"""
        if not isinstance(application, ApplicationSink):
            raise TypeError("review application sink is required")
        normalized_hint = str(hint or "").strip()
        if not normalized_hint:
            raise ValueError("review hint is required")
        if assistant_reply_sink is not None and not callable(assistant_reply_sink):
            raise TypeError("review assistant reply sink must be callable")
        self._application = application
        self._hint = normalized_hint
        self._assistant_reply_sink = assistant_reply_sink
        self._assistant_text = ""
        self._terminal_visible = False
        self._reconciliation_visible = False

    @property
    def assistant_output_visible(self) -> bool:
        """Review 的 reviewer 原始 assistant JSON 不得上屏。"""
        return False

    @property
    def run_lifecycle_visible(self) -> bool:
        """Review 使用专用 started、finished 和结果视图。"""
        return False

    async def observe(
        self,
        event: StreamEvent,
        current_item: CanonicalItem | None,
    ) -> bool:
        """从当前 Canonical Review Item 投影一项 Review 协议事件。"""
        if isinstance(event, ReviewStartedEvent):
            _require_review_item(event, current_item)
            self._application.emit(build_review_started_view(self._hint))
            return True
        if isinstance(event, ReviewCompletedEvent):
            item = _require_review_item(event, current_item)
            output = parse_review_output(item.payload_value().get("output"))
            self._assistant_text = review_output_text(output)
            self._terminal_visible = True
            self._application.emit(build_review_finished_view())
            self._application.emit(build_review_completed_view(output))
            if self._assistant_reply_sink is not None and self._assistant_text:
                self._assistant_reply_sink(self._assistant_text)
            return True
        if isinstance(event, ReviewFailedEvent):
            _require_review_item(event, current_item)
            self._terminal_visible = True
            self._application.emit(build_review_finished_view())
            self._application.emit(build_review_failed_view(event.error))
            return True
        if isinstance(event, ReviewCancelledEvent):
            _require_review_item(event, current_item)
            self._terminal_visible = True
            self._application.emit(build_review_finished_view())
            self._application.emit(build_review_cancelled_view(event.reason))
            return True
        if isinstance(event, ReviewReconciliationRequiredEvent):
            _require_review_item(event, current_item)
            self._reconciliation_visible = True
            self._application.emit(build_review_reconciliation_view(
                event.error,
                effect_id=event.effect_id,
            ))
            return True
        return False

    async def failure(
        self,
        status: RunStatus,
        error: str,
        *,
        effect_id: str = "",
    ) -> None:
        """把共享事件泵的本地失败转换为 Review 专用展示。"""
        if self._terminal_visible:
            return None
        if status == "reconciliation_required":
            if not self._reconciliation_visible:
                self._application.emit(build_review_reconciliation_view(
                    error,
                    effect_id=effect_id,
                ))
                self._reconciliation_visible = True
            return None
        self._application.emit(build_review_failed_view(error))

    def assistant_text(self, fallback: str) -> str:
        """只返回 Review completed 的结构化结果，忽略 reviewer 原始正文。"""
        _ = fallback
        return self._assistant_text


def _require_review_item(
    event: StreamEvent,
    item: CanonicalItem | None,
) -> CanonicalItem:
    """返回与当前 Review 事件一致的 Canonical Item。"""
    if (
        item is None
        or item.item_kind != "review"
        or item.item_id != event.item_id
        or item.item_status != event.item_status
    ):
        raise ValueError("canonical Review projection does not match event")
    return item


if __name__ == '__main__':
    pass
