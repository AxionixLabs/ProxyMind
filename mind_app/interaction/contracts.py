# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.application.approvals.models import (
    ApprovalDecisionSource,
    ApprovalDecisionValue,
    ApprovalQueueSnapshot,
    ApprovalRequest
)


@dataclass(frozen=True, slots=True)
class PromptContext(object):
    """描述主交互输入框需要展示的上下文。"""
    model: str
    workspace_label: str = ""
    permissions_label: str = ""


class ApprovalPresenterPort(typing.Protocol):
    """描述单条审批展示和连续批次生命周期能力。

    审批队列由应用层协调器持有；实现方只展示协调器指定的当前请求，并在批次开始、
    快照变化和批次结束时维护自身交互表面。
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


class InteractionPort(ApprovalPresenterPort, typing.Protocol):
    """描述主输入和工具审批展示需要的完整交互能力。"""

    async def read_message(self, context: PromptContext) -> str:
        """读取一条主交互输入。"""
        ...


if __name__ == '__main__':
    pass
