# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_core.hooks import (
    HOOK_EVENT_CONFIG_SPECS,
    HOOK_EVENT_NAMES,
    HookControlPolicy,
    HookEventName
)

HookOutputNormalizer = typing.Callable[
    [dict[str, typing.Any]],
    dict[str, typing.Any]
]


@dataclass(frozen=True, slots=True)
class HookEventSpec:
    """定义单个生命周期事件的输出协议。"""
    name: HookEventName
    control_policy: HookControlPolicy
    normalize_output: HookOutputNormalizer


def _event_spec(
    name: HookEventName,
    normalize_output: HookOutputNormalizer
) -> HookEventSpec:
    """根据配置目录构建生命周期事件运行规格。"""
    return HookEventSpec(
        name=name,
        control_policy=HOOK_EVENT_CONFIG_SPECS[name].control_policy,
        normalize_output=normalize_output,
    )


def _normalize_pre_tool_output(
    data: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """校验并规范化工具执行前 Hook 的输出。"""
    raw_decision = data.get("decision")
    if raw_decision is None:
        decision = ""
    elif isinstance(raw_decision, str):
        decision = raw_decision.strip().lower()
        if decision not in {"allow", "deny", "block"}:
            raise ValueError("hook decision must be allow, deny, or block")
    else:
        raise ValueError("hook decision must be a string")

    continuation = data.get("continue", True)
    if not isinstance(continuation, bool):
        raise ValueError("hook continue must be a boolean")

    raw_reason = data.get("reason", "")
    if not isinstance(raw_reason, str):
        raise ValueError("hook reason must be a string")

    return {
        **data,
        "decision" : decision,
        "continue" : continuation,
        "reason"   : raw_reason
    }


def _normalize_unrestricted_output(
    data: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """复制不需要额外字段约束的 Hook 输出。"""
    return dict(data)


def _normalize_gate_output(
    data: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """校验并规范化前置生命周期 Hook 的继续决定。"""
    continuation = data.get("continue", True)
    if not isinstance(continuation, bool):
        raise ValueError("hook continue must be a boolean")

    raw_reason = data.get("reason", "")
    if not isinstance(raw_reason, str):
        raise ValueError("hook reason must be a string")

    return {
        **data,
        "continue" : continuation,
        "reason"   : raw_reason
    }


def _normalize_permission_output(
    data: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """校验并规范化工具审批 Hook 的输出。"""
    raw_decision = data.get("decision")
    if raw_decision is None:
        decision = "abstain"
    elif isinstance(raw_decision, str):
        decision = raw_decision.strip().lower() or "abstain"
        if decision not in {"allow", "deny", "abstain"}:
            raise ValueError(
                "hook decision must be allow, deny, or abstain"
            )
    else:
        raise ValueError("hook decision must be a string")

    raw_reason = data.get("reason", "")
    if not isinstance(raw_reason, str):
        raise ValueError("hook reason must be a string")

    return {
        **data,
        "decision": decision,
        "reason": raw_reason,
    }


HOOK_EVENT_SPECS: dict[HookEventName, HookEventSpec] = {
    "PreToolUse": _event_spec("PreToolUse", _normalize_pre_tool_output),
    "PermissionRequest": _event_spec(
        "PermissionRequest",
        _normalize_permission_output,
    ),
    "PostToolUse": _event_spec("PostToolUse", _normalize_unrestricted_output),
    "PreCompact": _event_spec("PreCompact", _normalize_gate_output),
    "PostCompact": _event_spec("PostCompact", _normalize_unrestricted_output),
    "SessionStart": _event_spec("SessionStart", _normalize_unrestricted_output),
    "UserPromptSubmit": _event_spec("UserPromptSubmit", _normalize_gate_output),
    "SubagentStart": _event_spec("SubagentStart", _normalize_unrestricted_output),
    "SubagentStop": _event_spec("SubagentStop", _normalize_unrestricted_output),
    "Stop": _event_spec("Stop", _normalize_unrestricted_output),
}


def hook_event_spec(event: HookEventName) -> HookEventSpec:
    """返回指定生命周期事件的运行规格。"""
    try:
        return HOOK_EVENT_SPECS[event]
    except KeyError as error:
        raise ValueError(f"hook event is not registered: {event}") from error


def validate_hook_event_catalog() -> None:
    """校验配置事件目录与运行规格保持一致。"""
    configured = set(HOOK_EVENT_NAMES)
    registered = set(HOOK_EVENT_SPECS)

    if configured != registered:
        missing = sorted(configured.difference(registered))
        extra = sorted(registered.difference(configured))
        detail = f"missing={missing}, extra={extra}"
        raise RuntimeError(f"hook event catalog mismatch: {detail}")


validate_hook_event_catalog()


if __name__ == '__main__':
    pass
