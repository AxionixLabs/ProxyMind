# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from dataclasses import dataclass

ExecutionPolicyState: typing.TypeAlias = typing.Literal[
    "forbidden",
    "needs_approval",
    "skip",
]


@dataclass(frozen=True, slots=True)
class ExecutionPolicyAmendment:
    """表示一次可持久化的命令前缀修订提案。"""

    id: str
    command_prefix: tuple[str, ...]
    display: str


@dataclass(frozen=True, slots=True)
class ExecutionPolicyRequirement:
    """描述命令执行的跳过、审批或禁止要求。"""

    state: ExecutionPolicyState
    reason: str | None = None
    proposed_execpolicy_amendment: ExecutionPolicyAmendment | None = None
    bypass_sandbox: bool = False

    @classmethod
    def skip(
        cls,
        *,
        bypass_sandbox: bool = False,
        proposed_execpolicy_amendment: ExecutionPolicyAmendment | None = None,
    ) -> "ExecutionPolicyRequirement":
        """创建无需进一步审批的要求。"""
        return cls(
            state="skip",
            bypass_sandbox=bypass_sandbox,
            proposed_execpolicy_amendment=proposed_execpolicy_amendment,
        )

    @classmethod
    def needs_approval(
        cls,
        *,
        reason: str | None = None,
        proposed_execpolicy_amendment: ExecutionPolicyAmendment | None = None,
    ) -> "ExecutionPolicyRequirement":
        """创建需要用户审批的要求。"""
        return cls(
            state="needs_approval",
            reason=reason,
            proposed_execpolicy_amendment=proposed_execpolicy_amendment,
        )

    @classmethod
    def forbidden(cls, reason: str) -> "ExecutionPolicyRequirement":
        """创建禁止执行的要求。"""
        return cls(state="forbidden", reason=str(reason or "command forbidden"))


__all__ = (
    "ExecutionPolicyAmendment",
    "ExecutionPolicyRequirement",
    "ExecutionPolicyState",
)

if __name__ == "__main__":
    pass
