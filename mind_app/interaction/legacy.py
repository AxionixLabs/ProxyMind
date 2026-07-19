# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.prompt import prompt_tool_approval_decision
from mind_core.prompting import PromptToolkitBox
from .contracts import (
    InteractionPort,
    PromptContext
)


class LegacyInteraction(InteractionPort):
    """使用当前终端交互能力读取输入。"""

    def __init__(self, prompt_box: PromptToolkitBox | None = None) -> None:
        self.prompt_box = prompt_box or PromptToolkitBox()

    async def read_message(self, context: PromptContext) -> str:
        """通过当前主输入框读取消息。"""
        return await self.prompt_box.prompt_async(
            mode=context.mode,
            model=context.model,
            workspace_label=context.workspace_label,
            access_label=context.access_label,
            exec_status_label=context.exec_status_label,
        )

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """通过当前审批菜单读取决策。"""
        return await prompt_tool_approval_decision(approval)


if __name__ == '__main__':
    pass
