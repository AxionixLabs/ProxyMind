# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from protocol.schema.tool_approval import ToolApprovalDecision

ApprovalDecision: typing.TypeAlias = ToolApprovalDecision

ApprovalState: typing.TypeAlias = typing.Literal[
    "approved",
    "denied",
    "cancelled",
]
ApprovalSource: typing.TypeAlias = typing.Literal[
    "user",
    "hook",
    "policy",
    "auto_review",
]


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """描述工具审批结果的展示数据。"""

    approval: dict[str, typing.Any]
    decision: ApprovalDecision
    state: ApprovalState
    source: ApprovalSource = "user"


if __name__ == '__main__':
    pass
