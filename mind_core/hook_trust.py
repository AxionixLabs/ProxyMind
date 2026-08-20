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


def hook_needs_review(trust_state: HookTrustState) -> bool:
    """判断指定信任状态是否需要人工审核。"""
    return trust_state in {"untrusted", "modified"}


def hook_is_trusted(trust_state: HookTrustState) -> bool:
    """判断指定信任状态是否允许按已信任 Hook 展示。"""
    return trust_state in {"trusted", "managed"}


def hook_is_active(trust_state: HookTrustState, enabled: bool) -> bool:
    """按统一信任和启用状态判断 Hook 是否正在运行。"""
    return bool(enabled) and hook_is_trusted(trust_state)


def hook_is_toggleable(trust_state: HookTrustState, trust_policy: str) -> bool:
    """判断 Hook 是否允许用户切换启用状态。"""
    return trust_policy != "managed" and not hook_needs_review(trust_state)


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
        active=hook_is_active(trust_state, enabled),
    )


if __name__ == '__main__':
    pass
