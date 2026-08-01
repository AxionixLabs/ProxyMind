# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.hooks import HookEventName
from .protocol import validate_hook_output
from .models import (
    HookNormalizedOutput,
    HookOutputEffect
)


def normalize_business_block(
    event: HookEventName,
    *,
    reason: str,
    transport_output: dict[str, typing.Any] | None = None
) -> HookNormalizedOutput:
    """把命令退出码表达的业务阻断转换为统一输出。"""
    output         = dict(transport_output or {})
    blocked_reason = str(reason or "").strip() or "hook blocked execution"

    if event == "PermissionRequest":
        output["hookSpecificOutput"] = {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "deny",
                "message": blocked_reason,
            },
        }
    elif event == "PreToolUse":
        output["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": blocked_reason,
        }
    elif event in {"PostToolUse", "UserPromptSubmit"}:
        output["decision"] = "block"
        output["reason"] = blocked_reason
    elif event in {"Stop", "SubagentStop"}:
        output["decision"] = "block"
        output["reason"] = blocked_reason
    else:
        raise ValueError(f"{event} does not support exit-code-2 blocking")

    return normalize_hook_output(event, output)


def normalize_hook_output(
    event: HookEventName,
    data: dict[str, typing.Any]
) -> HookNormalizedOutput:
    """把命令 Hook 的 JSON 输出归一化为统一影响模型。"""
    validate_hook_output(event, data)
    _validate_output_semantics(event, data)
    merged        = _merge_specific_output(event, data)
    decision      = _normalize_decision(event, merged)
    continuation  = _normalize_continue(merged)
    reason        = _normalize_reason(merged)
    updated_input = _normalize_updated_input(merged)
    contexts      = _normalize_additional_context(event, merged)

    system_text = _normalize_optional_text(
        merged,
        "systemMessage",
        error="hook systemMessage must be a string",
    )

    continuation_prompt = ""

    if (
        not continuation_prompt
        and continuation
        and decision == "block"
        and event in {"Stop", "SubagentStop"}
    ):
        continuation_prompt = reason

    if (
        event in {"Stop", "SubagentStop"}
        and decision == "block"
        and continuation
        and not continuation_prompt
    ):
        raise ValueError(f"{event} block decision requires a continuation prompt")

    replacement_set, replacement_result = _replacement_result(merged)

    suppress_original_output = _normalize_bool(
        merged,
        "suppressOriginalOutput",
        error="hook suppressOriginalOutput must be a boolean",
    )

    continue_execution = _continue_execution(
        event,
        decision=decision,
        continuation=continuation,
    )

    if (
        event == "PostToolUse"
        and not continue_execution
        and not replacement_set
    ):
        suppress_original_output = True

    output = _normalized_output(
        data,
        decision=decision,
        continuation=continuation,
        reason=reason,
        updated_input=updated_input,
        additional_context=contexts,
        replacement_set=replacement_set,
        replacement_result=replacement_result,
        continuation_prompt=continuation_prompt,
        suppress_original_output=suppress_original_output,
    )

    return HookNormalizedOutput(
        output=output,
        effect=HookOutputEffect(
            continue_execution=continue_execution,
            decision=decision,
            reason=reason,
            updated_input=updated_input,
            additional_context=contexts,
            warning=system_text,
            replacement_result=replacement_result,
            replacement_result_set=replacement_set,
            continuation_prompt=continuation_prompt,
            suppress_original_output=suppress_original_output,
        ),
    )


def _merge_specific_output(
    event: HookEventName,
    data: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """合并 hookSpecificOutput 中与当前事件匹配的输出。"""
    merged   = dict(data)
    specific = data.get("hookSpecificOutput")

    if event == "PreToolUse":
        top_decision = data.get("decision")
        if top_decision == "approve":
            merged["decision"] = "allow"
        elif top_decision == "block":
            merged["decision"] = "deny"

    if specific is None:
        return merged
    if not isinstance(specific, dict):
        raise ValueError("hookSpecificOutput must be an object")

    specific_event = specific.get("hookEventName")

    if specific_event is not None and specific_event != event:
        raise ValueError(f"hookSpecificOutput event must be {event}")

    merged.update({
        key: value
        for key, value in specific.items()
        if key != "hookEventName"
    })

    if event == "PermissionRequest":
        decision = specific.get("decision")
        if decision is not None:
            if not isinstance(decision, dict):
                raise ValueError("PermissionRequest decision must be an object")
            behavior = decision.get("behavior")
            if not isinstance(behavior, str):
                raise ValueError(
                    "PermissionRequest decision.behavior must be a string"
                )
            merged["decision"] = behavior
            message = decision.get("message")
            if message is not None:
                if not isinstance(message, str):
                    raise ValueError(
                        "PermissionRequest decision.message must be a string"
                    )
                merged["reason"] = (
                    message
                    if message.strip() or behavior != "deny"
                    else "PermissionRequest hook denied approval"
                )
            elif behavior == "deny":
                merged["reason"] = "PermissionRequest hook denied approval"

    if event == "PreToolUse":
        permission_decision = specific.get("permissionDecision")
        if permission_decision is not None:
            merged["decision"] = (
                "" if permission_decision == "ask" else permission_decision
            )
            if "permissionDecisionReason" in specific:
                merged["reason"] = specific["permissionDecisionReason"]

    return merged


def _normalize_decision(
    event: HookEventName,
    data: dict[str, typing.Any]
) -> str:
    """读取并校验事件通用 decision 字段。"""
    default      = "abstain" if event == "PermissionRequest" else ""
    raw_decision = data.get("decision")

    if raw_decision is None:
        decision = default
    elif isinstance(raw_decision, str):
        decision = raw_decision.strip().lower() or default
    else:
        raise ValueError("hook decision must be a string")

    allowed = _allowed_decisions(event)

    if decision not in allowed:
        if event == "PreToolUse":
            raise ValueError("hook decision must be allow, deny, or block")
        if event == "PermissionRequest":
            raise ValueError("hook decision must be allow, deny, or abstain")
        if event in {"Stop", "SubagentStop"}:
            raise ValueError(f"{event} decision must be block")
        raise ValueError("hook decision is not supported for this event")

    return decision


def _allowed_decisions(event: HookEventName) -> set[str]:
    """返回当前事件允许的 decision 值。"""
    if event == "PreToolUse":
        return {"", "allow", "deny", "block"}
    if event == "PermissionRequest":
        return {"allow", "deny", "abstain"}
    if event == "UserPromptSubmit":
        return {"", "block"}
    if event == "PostToolUse":
        return {"", "block"}
    if event in {"Stop", "SubagentStop"}:
        return {"", "block"}

    return {"", "allow"}


def _validate_output_semantics(
    event: HookEventName,
    data: dict[str, typing.Any],
) -> None:
    """校验 schema 无法表达的事件控制约束。"""
    if event == "PermissionRequest":
        _reject_unsupported_universal(event, data)
        specific = data.get("hookSpecificOutput")
        if not isinstance(specific, dict):
            return None
        decision = specific.get("decision")
        if not isinstance(decision, dict):
            return None
        reserved = {
            key
            for key in ("updatedInput", "updatedPermissions", "interrupt")
            if key in decision and decision[key] not in (None, False)
        }
        if reserved:
            raise ValueError(
                "PermissionRequest decision does not support "
                f"{sorted(reserved)[0]}"
            )

    if event == "PreToolUse":
        _reject_unsupported_universal(event, data)
        specific = data.get("hookSpecificOutput")
        uses_specific_decision = isinstance(specific, dict) and any(
            specific.get(key) is not None
            for key in (
                "permissionDecision",
                "permissionDecisionReason",
                "updatedInput",
            )
        )
        if uses_specific_decision:
            _validate_pre_tool_specific_output(specific)
        else:
            _validate_pre_tool_legacy_output(data)

    if event == "PostToolUse":
        if data.get("suppressOutput"):
            raise ValueError(
                "PostToolUse hook returned unsupported suppressOutput"
            )
        specific = data.get("hookSpecificOutput")
        if (
            isinstance(specific, dict)
            and specific.get("updatedMCPToolOutput") is not None
        ):
            raise ValueError(
                "PostToolUse hook returned unsupported updatedMCPToolOutput"
            )
        if (
            data.get("continue", True) is True
            and data.get("decision") == "block"
            and not str(data.get("reason") or "").strip()
        ):
            raise ValueError(
                "PostToolUse block decision requires a non-empty reason"
            )
        if (
            data.get("continue", True) is True
            and data.get("decision") is None
            and data.get("reason") is not None
        ):
            raise ValueError(
                "PostToolUse hook returned reason without decision"
            )

    if event == "UserPromptSubmit" and data.get("decision") == "block":
        if not str(data.get("reason") or "").strip():
            raise ValueError(
                "UserPromptSubmit block decision requires a non-empty reason"
            )

    if event in {"Stop", "SubagentStop"} and data.get("decision") == "block":
        if not str(data.get("reason") or "").strip():
            raise ValueError(
                f"{event} block decision requires a non-empty reason"
            )

    return None


def _reject_unsupported_universal(
    event: HookEventName,
    data: dict[str, typing.Any],
) -> None:
    """拒绝工具前置与授权事件尚未实现的通用控制字段。"""
    if data.get("continue", True) is False:
        raise ValueError(f"{event} hook returned unsupported continue false")
    if data.get("stopReason") is not None:
        raise ValueError(f"{event} hook returned unsupported stopReason")
    if data.get("suppressOutput"):
        raise ValueError(f"{event} hook returned unsupported suppressOutput")


def _validate_pre_tool_specific_output(
    specific: dict[str, typing.Any],
) -> None:
    """校验 PreToolUse 的 hookSpecificOutput 控制组合。"""
    decision = specific.get("permissionDecision")
    updated_input_present = specific.get("updatedInput") is not None
    reason = str(specific.get("permissionDecisionReason") or "").strip()

    if updated_input_present and decision != "allow":
        raise ValueError(
            "PreToolUse updatedInput requires permissionDecision allow"
        )
    if decision == "allow" and not updated_input_present:
        raise ValueError(
            "PreToolUse permissionDecision allow requires updatedInput"
        )
    if decision == "ask":
        raise ValueError(
            "PreToolUse hook returned unsupported permissionDecision ask"
        )
    if decision == "deny" and not reason:
        raise ValueError(
            "PreToolUse permissionDecision deny requires a non-empty reason"
        )
    if decision is None and specific.get("permissionDecisionReason") is not None:
        raise ValueError(
            "PreToolUse permissionDecisionReason requires permissionDecision"
        )


def _validate_pre_tool_legacy_output(
    data: dict[str, typing.Any],
) -> None:
    """校验 PreToolUse 顶层兼容控制字段。"""
    decision = data.get("decision")
    if decision == "approve":
        raise ValueError(
            "PreToolUse hook returned unsupported decision approve"
        )
    if decision == "block" and not str(data.get("reason") or "").strip():
        raise ValueError(
            "PreToolUse block decision requires a non-empty reason"
        )
    if decision is None and data.get("reason") is not None:
        raise ValueError("PreToolUse hook returned reason without decision")


def _normalize_continue(data: dict[str, typing.Any]) -> bool:
    """读取通用 continue 字段。"""
    continuation = data.get("continue", True)
    if not isinstance(continuation, bool):
        raise ValueError("hook continue must be a boolean")
    return continuation


def _normalize_reason(data: dict[str, typing.Any]) -> str:
    """读取通用原因文本。"""
    raw_reason = data.get("reason", data.get("stopReason", ""))
    if not isinstance(raw_reason, str):
        raise ValueError("hook reason must be a string")
    return raw_reason.strip()


def _normalize_updated_input(
    data: dict[str, typing.Any]
) -> dict[str, typing.Any] | None:
    """读取可选的输入改写对象。"""
    found, value = _first_present(data, "updatedInput")

    if not found:
        return None
    if not isinstance(value, dict):
        raise ValueError("hook updatedInput must be an object")

    return dict(value)


def _normalize_additional_context(
    event: HookEventName,
    data: dict[str, typing.Any]
) -> tuple[str, ...]:
    """读取可注入到后续请求的上下文文本。"""
    found, value = _first_present(
        data,
        "additionalContext",
    )
    if (
        not found
        and event in {"SessionStart", "UserPromptSubmit", "SubagentStart"}
        and isinstance(data.get("stdout"), str)
    ):
        value = data.get("stdout")
        found = True
    if not found:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    raise ValueError("hook additionalContext must be a string")


def _normalize_optional_text(
    data: dict[str, typing.Any],
    camel_key: str,
    *,
    error: str
) -> str:
    """读取可选的文本字段。"""
    found, value = _first_present(data, camel_key)

    if not found:
        return ""
    if not isinstance(value, str):
        raise ValueError(error)

    return value.strip()


def _normalize_bool(
    data: dict[str, typing.Any],
    camel_key: str,
    *,
    error: str
) -> bool:
    """读取可选的布尔字段。"""
    found, value = _first_present(data, camel_key)

    if not found:
        return False
    if not isinstance(value, bool):
        raise ValueError(error)

    return value


def _replacement_result(
    data: dict[str, typing.Any]
) -> tuple[bool, typing.Any]:
    """读取可选的工具结果替换对象。"""
    return _first_present(
        data,
        "replacementResult",
    )


def _continue_execution(
    event: HookEventName,
    *,
    decision: str,
    continuation: bool
) -> bool:
    """把事件 decision 和 continue 字段转换为通用继续标记。"""
    if not continuation:
        return False
    if decision == "deny":
        return False
    if decision == "block" and event not in {"Stop", "SubagentStop"}:
        return False

    return True


def _normalized_output(
    data: dict[str, typing.Any],
    *,
    decision: str,
    continuation: bool,
    reason: str,
    updated_input: dict[str, typing.Any] | None,
    additional_context: tuple[str, ...],
    replacement_set: bool,
    replacement_result: typing.Any,
    continuation_prompt: str,
    suppress_original_output: bool
) -> dict[str, typing.Any]:
    """构建兼容既有调用点的规范化输出字典。"""
    output = dict(data)
    output["decision"] = decision
    output["continue"] = continuation
    output["reason"]   = reason

    if updated_input is not None:
        output["updated_input"] = dict(updated_input)
    if additional_context:
        output["additional_context"] = "\n\n".join(additional_context)
    if replacement_set:
        output["replacement_result"] = replacement_result
    if continuation_prompt:
        output["continuation_prompt"] = continuation_prompt

    output["suppress_original_output"] = suppress_original_output

    return output


def _first_present(
    data: dict[str, typing.Any],
    *keys: str
) -> tuple[bool, typing.Any]:
    """按顺序读取第一个存在的字段。"""
    for key in keys:
        if key in data:
            return True, data[key]

    return False, None


if __name__ == '__main__':
    pass
