# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.domain.approvals import (
    ApprovalActionKind,
    ApprovalReviewIdentity,
    ApprovalReviewRecord,
    ApprovalReviewRiskLevel,
    ApprovalReviewStatus,
    ApprovalReviewUserAuthorization,
)
from agent.ports import ApprovalReviewFeedPort
from agent.protocol import ModelEvent
from observability import observe
from protocol.schema.stream_events import (
    ToolApprovalReviewCompletedEvent,
    ToolApprovalReviewEvent,
)


_ACTION_KINDS = {
    "command": ApprovalActionKind.COMMAND,
    "write_stdin": ApprovalActionKind.COMMAND,
    "apply_patch": ApprovalActionKind.PATCH,
    "network_access": ApprovalActionKind.NETWORK,
    "request_permissions": ApprovalActionKind.PERMISSION,
    "mcp_tool_call": ApprovalActionKind.MCP,
}


class ApprovalReviewEventHandler:
    """将自动评审通知投影到本地 reviewer 镜像。

    实例只服务于一个 Turn；它跟踪展示代次并在替代、终止或关闭时
    清理非权威镜像。实现方不提交审批决定，也不执行 Effect。
    """

    def __init__(
        self,
        feed: ApprovalReviewFeedPort,
        *,
        session_id: str,
        run_id: str,
    ) -> None:
        """绑定一个 Turn 的评审事实接收端。"""
        if not isinstance(feed, ApprovalReviewFeedPort):
            raise TypeError("approval review feed is required")
        if not session_id.strip() or not run_id.strip():
            raise ValueError("approval review session and run are required")
        self._feed = feed
        self._session_id = session_id.strip()
        self._run_id = run_id.strip()
        self._presentation_epoch: int = 0
        self._closed: bool = False

    @property
    def presentation_epoch(self) -> int:
        """返回当前接受的展示代次。"""
        return self._presentation_epoch

    async def observe_presentation(self, event: ModelEvent) -> bool:
        """观察事件代次，并返回它是否属于当前展示。"""
        if self._closed:
            raise RuntimeError("approval review event handler is closed")
        epoch = event.presentation_epoch
        if epoch < self._presentation_epoch:
            return False
        if epoch > self._presentation_epoch:
            if self._presentation_epoch > 0:
                await self._clear()
            self._presentation_epoch = epoch
        return True

    async def handle(self, event: ModelEvent) -> bool:
        """登记当前代次的评审事件，并返回是否已消费。"""
        if not isinstance(event, ToolApprovalReviewEvent):
            return False
        if event.presentation_epoch != self._presentation_epoch:
            return True
        record = approval_review_record(event)
        await self._feed.record_review(record)
        observe(
            "approval.review.received",
            turn_id=event.turn_id,
            review_id=event.review_id,
            approval_id=event.approval_id,
            call_id=event.call_id,
            status=event.review.status,
            presentation_epoch=event.presentation_epoch,
        )
        return True

    async def close(self) -> None:
        """幂等清理当前 Turn 的评审镜像。"""
        if self._closed:
            return
        self._closed = True
        await self._clear()

    async def _clear(self) -> None:
        """清理当前 Turn 的全部临时评审状态。"""
        await self._feed.clear_approval_reviews(
            self._session_id,
            self._run_id,
        )


def approval_review_record(
    event: ToolApprovalReviewEvent,
) -> ApprovalReviewRecord:
    """把已校验的 wire 评审事件转换为领域事实。"""
    event_seq = event.event_seq
    if isinstance(event_seq, bool) or not isinstance(event_seq, int):
        raise ValueError("approval review event_seq is required")
    review = event.review
    return ApprovalReviewRecord(
        identity=ApprovalReviewIdentity(
            session_id=event.sid,
            run_id=event.turn_id,
            review_id=event.review_id,
            approval_id=event.approval_id,
            action_id=event.call_id,
            action_kind=_ACTION_KINDS[event.kind],
        ),
        status=ApprovalReviewStatus(review.status),
        event_seq=event_seq,
        presentation_epoch=event.presentation_epoch,
        started_at_ms=event.started_at_ms,
        completed_at_ms=(
            event.completed_at_ms
            if isinstance(event, ToolApprovalReviewCompletedEvent)
            else None
        ),
        risk_level=(
            ApprovalReviewRiskLevel(review.risk_level)
            if review.risk_level is not None
            else None
        ),
        user_authorization=(
            ApprovalReviewUserAuthorization(review.user_authorization)
            if review.user_authorization is not None
            else None
        ),
        rationale=review.rationale,
    )


if __name__ == '__main__':
    pass
