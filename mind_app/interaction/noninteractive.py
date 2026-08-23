# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.approval.models import (
    ApprovalDecisionSource,
    ApprovalDecisionValue,
    ApprovalQueueSnapshot,
    ApprovalRequest
)
from .contracts import (
    InteractionPort,
    PromptContext
)


class NonInteractiveInteraction(InteractionPort):
    """为非交互运行提供安全的输入决策。"""

    @property
    def approval_source(self) -> ApprovalDecisionSource:
        """把自动拒绝归因于非交互安全策略。"""
        return "policy"

    def approval_snapshot_changed(
        self,
        snapshot: ApprovalQueueSnapshot
    ) -> None:
        """非交互模式忽略审批队列快照。"""
        _ = snapshot
        return None

    async def read_message(self, context: PromptContext) -> str:
        """拒绝在非交互运行中读取主输入。"""
        _ = context
        raise RuntimeError("Non-interactive mode cannot read messages")

    async def begin_approval_session(self) -> None:
        """非交互模式无需建立审批表面。"""
        return None

    async def present_approval(
        self,
        request: ApprovalRequest
    ) -> ApprovalDecisionValue:
        """自动拒绝需要人工确认的工具调用。"""
        _ = request
        return "decline"

    async def end_approval_session(self) -> None:
        """非交互模式无需恢复审批表面。"""
        return None


if __name__ == '__main__':
    pass
