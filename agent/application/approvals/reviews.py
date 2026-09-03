# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

from agent.domain.approvals import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalIdentity,
    ApprovalReviewRecord,
    ApprovalReviewStatus,
)


class ApprovalReviewInbox:
    """归约自动评审通知并为审批核心提供匹配的 reviewer 决定。"""

    def __init__(self) -> None:
        """创建空收件箱，生命周期由审批协调器管理。"""
        self._records: dict[str, ApprovalReviewRecord] = {}
        self._action_reviews: dict[ApprovalIdentity, str] = {}
        self._lock = asyncio.Lock()

    async def record(self, update: ApprovalReviewRecord) -> None:
        """幂等登记一项评审状态并拒绝身份或终态冲突。"""
        if not isinstance(update, ApprovalReviewRecord):
            raise TypeError("approval review update is invalid")
        identity = update.identity
        action_identity = ApprovalIdentity(
            session_id=identity.session_id,
            run_id=identity.run_id,
            approval_id=identity.approval_id,
            action_id=identity.action_id,
        )
        async with self._lock:
            review_for_action = self._action_reviews.get(action_identity)
            if review_for_action is not None and review_for_action != identity.review_id:
                raise ValueError("approval action is already bound to another review")

            current = self._records.get(identity.review_id)
            if current is not None:
                if current.identity != identity:
                    raise ValueError("approval review identity was reused")
                if current == update:
                    return None
                if current.terminal:
                    if update.event_seq <= current.event_seq:
                        return None
                    raise ValueError("approval review terminal state cannot change")
                if update.status is ApprovalReviewStatus.IN_PROGRESS:
                    if update.event_seq < current.event_seq:
                        return None
                    raise ValueError("approval review start event conflicts")
                if update.started_at_ms != current.started_at_ms:
                    raise ValueError("approval review start time changed")
                if update.event_seq <= current.event_seq:
                    raise ValueError("approval review completion does not advance")

            self._records[identity.review_id] = update
            self._action_reviews[action_identity] = identity.review_id

    async def review(self, action: ApprovalAction) -> ApprovalDecision | None:
        """返回与完整动作身份匹配的终态决定。"""
        async with self._lock:
            review_id = self._action_reviews.get(action.identity)
            if review_id is None:
                return None
            record = self._records[review_id]
            if record.identity.action_kind is not action.kind:
                raise ValueError("approval review action kind does not match request")
            if not record.terminal:
                return None
            decision_kind = {
                ApprovalReviewStatus.APPROVED: ApprovalDecisionKind.ALLOW_ONCE,
                ApprovalReviewStatus.DENIED: ApprovalDecisionKind.DECLINE,
                ApprovalReviewStatus.TIMED_OUT: ApprovalDecisionKind.TIMEOUT,
                ApprovalReviewStatus.ABORTED: ApprovalDecisionKind.CANCEL,
            }[record.status]
            return ApprovalDecision(
                kind=decision_kind,
                action_fingerprint=action.fingerprint,
            )

    async def completed_for(
        self,
        identity: ApprovalIdentity,
    ) -> ApprovalReviewRecord | None:
        """返回动作对应的评审终态，供应用投影补充解释。"""
        async with self._lock:
            review_id = self._action_reviews.get(identity)
            if review_id is None:
                return None
            record = self._records[review_id]
            return record if record.terminal else None

    async def clear_run(self, session_id: str, run_id: str) -> None:
        """清除一个 Run 的评审镜像，不影响已持久化审批事实。"""
        normalized_session = session_id.strip()
        normalized_run = run_id.strip()
        async with self._lock:
            identities = tuple(
                identity
                for identity in self._action_reviews
                if identity.session_id == normalized_session
                and identity.run_id == normalized_run
            )
            for identity in identities:
                review_id = self._action_reviews.pop(identity)
                self._records.pop(review_id, None)

    async def close(self) -> None:
        """清除进程级评审镜像并结束生命周期。"""
        async with self._lock:
            self._records.clear()
            self._action_reviews.clear()


if __name__ == '__main__':
    pass
