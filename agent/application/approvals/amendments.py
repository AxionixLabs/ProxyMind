# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.domain.identifiers import derive_stable_id


@dataclass(frozen=True, slots=True)
class ExecPolicyAmendmentProposal:
    """保存服务端提供的执行策略修订提案。"""

    id: str
    command_prefix: tuple[str, ...]
    display: str


def approval_execpolicy_amendment(
    approval: dict[str, typing.Any] | None,
) -> ExecPolicyAmendmentProposal | None:
    """读取已经通过结构校验的执行策略修订提案。"""
    if not isinstance(approval, dict):
        return None
    raw = approval.get("proposed_execpolicy_amendment")
    if not isinstance(raw, dict):
        return None

    command_prefix = raw.get("command")
    if isinstance(command_prefix, list):
        if any(not isinstance(value, str) or not value for value in command_prefix):
            return None
        command_prefix = tuple(command_prefix)
        amendment_id = derive_stable_id("execpolicy", *command_prefix)
        display = " ".join(command_prefix)
    else:
        amendment_id = str(raw.get("id") or "").strip()
        display = str(raw.get("display") or "").strip()
        command_prefix = raw.get("command_prefix")

    if (
        not amendment_id
        or not display
        or not isinstance(command_prefix, (list, tuple))
        or not command_prefix
        or any(not isinstance(value, str) or not value for value in command_prefix)
    ):
        return None
    return ExecPolicyAmendmentProposal(
        id=amendment_id,
        command_prefix=tuple(command_prefix),
        display=display,
    )


if __name__ == '__main__':
    pass
