# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports import ModelCapabilityError
from agent.protocol import (
    ModelEvent,
    ModelStreamEndReason,
    ReviewStreamRequest,
)
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewCompletedEvent,
    ReviewFailedEvent,
    ReviewReconciliationRequiredEvent,
    ReviewStartedEvent,
    TurnCompletedEvent,
)

__all__ = ("ReviewStreamValidator",)

_ReviewTerminalEvent = (
    ReviewCancelledEvent
    | ReviewCompletedEvent
    | ReviewFailedEvent
    | ReviewReconciliationRequiredEvent
)


class ReviewStreamValidator:
    """校验 Review Item 与唯一 Turn 终态的顺序和冻结身份。"""

    def __init__(self, request: ReviewStreamRequest) -> None:
        """绑定不可变请求并初始化未结算的观察状态。"""
        if not isinstance(request, ReviewStreamRequest):
            raise TypeError("review stream request is required")
        self._request = request
        self._review_item_id: str | None = None
        self._terminal: _ReviewTerminalEvent | None = None
        self._turn_completed = False

    @property
    def settled(self) -> bool:
        """返回 Review 与 Turn 终态是否已完整对账。"""
        return self._turn_completed

    def validate(self, event: ModelEvent) -> None:
        """按交付顺序校验一项正式模型事件。"""
        if isinstance(event, ReviewStartedEvent):
            self._validate_started(event)
            return
        if isinstance(
            event,
            (
                ReviewCancelledEvent,
                ReviewCompletedEvent,
                ReviewFailedEvent,
                ReviewReconciliationRequiredEvent,
            ),
        ):
            self._validate_terminal(event)
            return
        if isinstance(event, TurnCompletedEvent):
            self._validate_turn_completed(event)

    def finish(self, end_reason: ModelStreamEndReason | None) -> None:
        """拒绝在唯一权威 Turn 终态到达前结束的观察流。"""
        if self._turn_completed:
            return
        raise ModelCapabilityError(
            "review_observation_incomplete",
            "Review observation ended before authoritative terminal settlement.",
            retryable=True,
            details={
                "end_reason": end_reason or "unknown",
                "reconciliation_required": True,
                "submission_unknown": False,
            },
        )

    def _validate_started(self, event: ReviewStartedEvent) -> None:
        """确认 Review 开始事件与冻结目标和工作区一致。"""
        if self._review_item_id is not None:
            self._raise_protocol_error("review.started must be unique")
        if event.target.request_payload() != self._request.target:
            self._raise_protocol_error(
                "review.started target does not match request"
            )
        revision = self._request.workspace.get("revision")
        if event.workspace_revision != revision:
            self._raise_protocol_error(
                "review.started workspace revision does not match request"
            )
        self._review_item_id = event.review_item_id

    def _validate_terminal(self, event: _ReviewTerminalEvent) -> None:
        """确认 Review Item 终态唯一且属于已开始项。"""
        if self._review_item_id is None:
            self._raise_protocol_error(
                "review terminal event arrived before review.started"
            )
        if event.review_item_id != self._review_item_id:
            self._raise_protocol_error(
                "review terminal event changed review_item_id"
            )
        if self._terminal is not None:
            self._raise_protocol_error("review terminal event must be unique")
        self._terminal = event

    def _validate_turn_completed(self, event: TurnCompletedEvent) -> None:
        """确认 Turn 终态与先到达的 Review Item 终态一致。"""
        if self._turn_completed:
            self._raise_protocol_error("turn.completed must be unique")
        terminal = self._terminal
        if terminal is None:
            self._raise_protocol_error(
                "turn.completed arrived before review terminal event"
            )
        expected_statuses: set[str]
        if isinstance(terminal, ReviewCompletedEvent):
            expected_statuses = {"completed"}
        elif isinstance(terminal, ReviewFailedEvent):
            expected_statuses = {"failed"}
        elif isinstance(terminal, ReviewCancelledEvent):
            expected_statuses = {"cancelled", "interrupted"}
        else:
            self._raise_protocol_error(
                "turn.completed cannot settle a review requiring reconciliation"
            )
        if event.status not in expected_statuses:
            self._raise_protocol_error(
                "turn.completed status conflicts with review terminal"
            )
        self._turn_completed = True

    @staticmethod
    def _raise_protocol_error(message: str) -> typing.NoReturn:
        """把已登记 Review 的协议冲突保留为待核对事实。"""
        raise ModelCapabilityError(
            "review_protocol_error",
            message,
            details={
                "reconciliation_required": True,
                "submission_unknown": False,
            },
        )


if __name__ == '__main__':
    pass
