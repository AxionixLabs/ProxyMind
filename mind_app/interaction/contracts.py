# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_app.approval.models import ApprovalDecisionValue
from mind_nova.modes import RunMode


@dataclass(frozen=True, slots=True)
class PromptContext(object):
    """描述主交互输入框需要展示的上下文。"""

    mode: RunMode
    model: str
    workspace_label: str = ""
    access_label: str = ""
    exec_status_label: str = ""


class InteractionPort(typing.Protocol):
    """描述主输入和工具审批需要的交互能力。"""

    async def read_message(self, context: PromptContext) -> str:
        """读取一条主交互输入。"""
        ...

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """读取工具审批决策。"""
        ...


if __name__ == '__main__':
    pass
