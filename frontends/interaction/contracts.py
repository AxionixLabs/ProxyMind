# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.approvals.presenter import ApprovalPresenterPort


@dataclass(frozen=True, slots=True)
class PromptContext(object):
    """描述主交互输入框需要展示的上下文。"""
    model: str
    workspace_label: str = ""
    permissions_label: str = ""


class InteractionPort(ApprovalPresenterPort, typing.Protocol):
    """描述主输入和工具审批展示需要的完整交互能力。"""

    async def read_message(self, context: PromptContext) -> str:
        """读取一条主交互输入。"""
        ...


if __name__ == '__main__':
    pass
