# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

ApprovalDecisionValue = typing.Literal[
    "accept",
    "acceptForSession",
    "decline",
    "expired"
]


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
