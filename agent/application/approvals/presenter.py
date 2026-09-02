# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from .models import (
    ApprovalDecisionSource,
    ApprovalDecisionValue,
    ApprovalQueueSnapshot,
    ApprovalRequest,
)


class ApprovalPresenterPort(typing.Protocol):
    """描述单条审批展示和连续批次生命周期能力。

    协调器持有审批队列；实现方只展示当前请求，并在批次开始、快照变化和批次结束时
    维护自身交互表面。实现方不得持有或改写审批状态。
    """

    @property
    def approval_source(self) -> ApprovalDecisionSource:
        """返回该交互端产生审批决策时使用的来源。"""
        ...

    async def begin_approval_session(self) -> None:
        """开始一个连续审批批次。"""
        ...

    def approval_snapshot_changed(
        self,
        snapshot: ApprovalQueueSnapshot,
    ) -> None:
        """接收审批队列的最新只读快照。"""
        ...

    async def present_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecisionValue:
        """展示一条当前审批请求并等待用户决策。"""
        ...

    async def end_approval_session(self) -> None:
        """结束连续审批批次并恢复原交互表面。"""
        ...


if __name__ == '__main__':
    pass
