# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.application import (
    HOOK_EVENT_CONFIG_SPECS,
    HOOK_EVENT_NAMES,
    HookControlPolicy,
    HookEventName
)
from .effects import normalize_hook_output
from .models import HookNormalizedOutput
from .protocol import (
    JsonSchema,
    hook_input_schema,
    hook_output_schema
)

HookOutputNormalizer = typing.Callable[
    [dict[str, typing.Any]],
    HookNormalizedOutput
]


@dataclass(frozen=True, slots=True)
class HookEventSpec:
    """定义单个生命周期事件的输出协议。"""
    name: HookEventName
    control_policy: HookControlPolicy
    input_schema: JsonSchema
    output_schema: JsonSchema
    normalize_output: HookOutputNormalizer


def _event_spec(
    name: HookEventName
) -> HookEventSpec:
    """根据配置目录构建生命周期事件运行规格。"""
    return HookEventSpec(
        name=name,
        control_policy=HOOK_EVENT_CONFIG_SPECS[name].control_policy,
        input_schema=hook_input_schema(name),
        output_schema=hook_output_schema(name),
        normalize_output=lambda data: normalize_hook_output(name, data),
    )


HOOK_EVENT_SPECS: dict[HookEventName, HookEventSpec] = {
    "PreToolUse": _event_spec("PreToolUse"),
    "PermissionRequest": _event_spec("PermissionRequest"),
    "PostToolUse": _event_spec("PostToolUse"),
    "PreCompact": _event_spec("PreCompact"),
    "PostCompact": _event_spec("PostCompact"),
    "SessionStart": _event_spec("SessionStart"),
    "SessionEnd": _event_spec("SessionEnd"),
    "UserPromptSubmit": _event_spec("UserPromptSubmit"),
    "SubagentStart": _event_spec("SubagentStart"),
    "SubagentStop": _event_spec("SubagentStop"),
    "Stop": _event_spec("Stop"),
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

    if configured != registered or tuple(HOOK_EVENT_SPECS) != HOOK_EVENT_NAMES:
        missing = sorted(configured.difference(registered))
        extra   = sorted(registered.difference(configured))
        detail  = f"missing={missing}, extra={extra}"

        raise RuntimeError(f"hook event catalog mismatch: {detail}")


validate_hook_event_catalog()


if __name__ == '__main__':
    pass
