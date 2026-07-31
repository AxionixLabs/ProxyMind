# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.interaction.contracts import InteractionPort


class ApprovalCoordinator:
    """串行协调共享交互前端上的工具审批请求。"""

    def __init__(self, interaction: InteractionPort) -> None:
        self._interaction = interaction
        self._lock        = asyncio.Lock()

    async def request(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """等待独占交互窗口并返回审批决策。"""
        async with self._lock:
            return await self._interaction.request_approval(approval)


if __name__ == '__main__':
    pass
