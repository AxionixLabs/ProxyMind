# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from dataclasses import dataclass
from mind_core.hooks import (
    HookDefinitionConfig,
    HookStateTable
)

HookTrustState = typing.Literal[
    "managed",
    "trusted",
    "modified",
    "untrusted",
]


@dataclass(frozen=True, slots=True)
class HookResolvedState:
    """保存 Hook 当前的信任、启用和运行状态。"""
    trust_state: HookTrustState
    enabled: bool
    active: bool


def resolve_hook_state(
    definition: HookDefinitionConfig,
    states: HookStateTable
) -> HookResolvedState:
    """根据用户配置状态解析单个 Hook 是否可以运行。"""
    if definition.trust_policy == "managed":
        return HookResolvedState(
            trust_state="managed",
            enabled=True,
            active=True,
        )

    state        = states.get(definition.key, {})
    enabled      = state.get("enabled") is not False
    trusted_hash = state.get("trusted_hash")

    if trusted_hash is None:
        trust_state: HookTrustState = "untrusted"
    elif trusted_hash == definition.content_hash:
        trust_state = "trusted"
    else:
        trust_state = "modified"

    return HookResolvedState(
        trust_state=trust_state,
        enabled=enabled,
        active=enabled and trust_state == "trusted",
    )


if __name__ == '__main__':
    pass
