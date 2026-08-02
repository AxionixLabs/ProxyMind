# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_nova.tool_approval import ToolApprovalDecision


ApprovalDecisionValue: typing.TypeAlias = (
    ToolApprovalDecision | typing.Literal["expired"]
)


@dataclass(frozen=True, slots=True)
class ExecPolicyAmendmentProposal(object):
    """保存服务端提供的执行策略修订提案。"""
    id: str
    command_prefix: tuple[str, ...]
    display: str


@dataclass(slots=True)
class ApprovalRecord(object):
    """保存审批请求元数据，用于校验后续工具调用。"""
    approval_id: str
    call_id: str
    tool: str
    arguments: dict[str, typing.Any]


@dataclass(slots=True)
class ApprovalDecision(object):
    """表示工具调用审批校验后的处理动作。"""
    action: typing.Literal["allow", "reject", "wait"]
    result: dict[str, typing.Any] | None = None


if __name__ == '__main__':
    pass
