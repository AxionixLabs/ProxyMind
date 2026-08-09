# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.approval.models import ApprovalDecisionValue
from .contracts import (
    ApprovalDecisionSource,
    InteractionPort,
    PromptContext
)


class NonInteractiveInteraction(InteractionPort):
    """为非交互运行提供安全的输入决策。"""

    @property
    def approval_source(self) -> ApprovalDecisionSource:
        """把自动拒绝归因于非交互安全策略。"""
        return "policy"

    async def read_message(self, context: PromptContext) -> str:
        """拒绝在非交互运行中读取主输入。"""
        _ = context
        raise RuntimeError("Non-interactive mode cannot read messages")

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """自动拒绝需要人工确认的工具调用。"""
        _ = approval
        return "decline"


if __name__ == '__main__':
    pass
