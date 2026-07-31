# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.hooks import (
    HOOK_EVENT_NAMES,
    HookEventName
)

JsonSchema = dict[str, typing.Any]


def _object_schema(
    properties: dict[str, JsonSchema],
    *,
    required: typing.Iterable[str] = (),
    additional_properties: bool = False
) -> JsonSchema:
    """构建对象类型的协议 schema。"""
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": additional_properties
    }


_STRING: JsonSchema  = {"type": "string"}
_BOOLEAN: JsonSchema = {"type": "boolean"}
_INTEGER: JsonSchema = {"type": "integer"}

_NULLABLE_STRING: JsonSchema  = {"type": ["string", "null"]}
_NULLABLE_INTEGER: JsonSchema = {"type": ["integer", "null"]}

_OBJECT: JsonSchema = _object_schema({}, additional_properties=True)

_ANY: JsonSchema = {}

_CONTEXT: JsonSchema = {
    "oneOf": [
        _STRING,
        {"type": "array", "items": _STRING},
    ],
}

_COMMON_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "hook_event_name": _STRING,
    "session_id": _STRING,
    "root_session_id": _STRING,
    "conversation_id": _STRING,
    "turn_id": _STRING,
    "cwd": _STRING,
    "model": _STRING,
    "mode": _STRING,
    "source": _STRING,
    "sandbox_mode": _STRING,
    "permission_mode": _STRING,
    "agent_id": _STRING,
    "agent_type": _STRING,
    "agent_depth": _INTEGER,
    "parent_agent_id": _NULLABLE_STRING,
    "session_started": _BOOLEAN,
    "session_start_reason": _STRING,
}

_COMMON_INPUT_REQUIRED = tuple(_COMMON_INPUT_PROPERTIES)

_TOOL_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "call_id": _STRING,
    "tool_name": _STRING,
    "tool_kind": _STRING,
    "tool_input": _OBJECT,
}

_STOP_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "outcome": _STRING,
    "error": _STRING,
    "usage": _OBJECT,
    "stop_hook_active": _BOOLEAN,
    "last_assistant_message": _STRING,
    "continuation_count": _INTEGER,
}


def _input_schema(
    event: HookEventName,
    properties: dict[str, JsonSchema],
    *,
    required: typing.Iterable[str]
) -> JsonSchema:
    """构建包含公共执行上下文的事件输入 schema。"""
    common_properties = dict(_COMMON_INPUT_PROPERTIES)

    common_properties["hook_event_name"] = {
        "type": "string",
        "const": event,
    }

    return _object_schema(
        {**common_properties, **properties},
        required=(*_COMMON_INPUT_REQUIRED, *required),
    )


HOOK_INPUT_SCHEMAS: dict[HookEventName, JsonSchema] = {
    "PreToolUse": _input_schema(
        "PreToolUse",
        _TOOL_INPUT_PROPERTIES,
        required=_TOOL_INPUT_PROPERTIES,
    ),
    "PermissionRequest": _input_schema(
        "PermissionRequest",
        _TOOL_INPUT_PROPERTIES,
        required=_TOOL_INPUT_PROPERTIES,
    ),
    "PostToolUse": _input_schema(
        "PostToolUse",
        {
            **_TOOL_INPUT_PROPERTIES,
            "tool_outcome": _object_schema(
                {
                    "executed": _BOOLEAN,
                    "ok": _BOOLEAN,
                    "duration_ms": _INTEGER,
                    "result": _ANY,
                    "error": _STRING,
                    "cancelled": _BOOLEAN,
                },
                required=(
                    "executed",
                    "ok",
                    "duration_ms",
                    "result",
                    "error",
                    "cancelled",
                ),
            ),
        },
        required=(*_TOOL_INPUT_PROPERTIES, "tool_outcome"),
    ),
    "PreCompact": _input_schema(
        "PreCompact",
        {"trigger": _STRING},
        required=("trigger",),
    ),
    "PostCompact": _input_schema(
        "PostCompact",
        {
            "trigger": _STRING,
            "outcome": _STRING,
            "message": _STRING,
            "before_items": _NULLABLE_INTEGER,
            "after_items": _NULLABLE_INTEGER,
        },
        required=(
            "trigger",
            "outcome",
            "message",
            "before_items",
            "after_items",
        ),
    ),
    "SessionStart": _input_schema(
        "SessionStart",
        {"reason": _STRING},
        required=("reason",),
    ),
    "UserPromptSubmit": _input_schema(
        "UserPromptSubmit",
        {"prompt": _STRING},
        required=("prompt",),
    ),
    "SubagentStart": _input_schema(
        "SubagentStart",
        {"task": _STRING},
        required=("task",),
    ),
    "SubagentStop": _input_schema(
        "SubagentStop",
        {
            **_STOP_INPUT_PROPERTIES,
            "agent_transcript_path": _NULLABLE_STRING,
        },
        required=(*_STOP_INPUT_PROPERTIES, "agent_transcript_path"),
    ),
    "Stop": _input_schema(
        "Stop",
        _STOP_INPUT_PROPERTIES,
        required=_STOP_INPUT_PROPERTIES,
    ),
}

_BASE_OUTPUT_PROPERTIES: dict[str, JsonSchema] = {
    "continue": _BOOLEAN,
    "reason": _STRING,
    "stopReason": _STRING,
}
_CONTEXT_OUTPUT_PROPERTIES: dict[str, JsonSchema] = {
    "additionalContext": _CONTEXT,
    "additional_context": _CONTEXT,
    "systemMessage": _STRING,
    "system_message": _STRING,
    "stdout": _STRING,
}
_UPDATED_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "updatedInput": _OBJECT,
    "updated_input": _OBJECT,
}
_REPLACEMENT_PROPERTIES: dict[str, JsonSchema] = {
    "replacementResult": _ANY,
    "replacement_result": _ANY,
    "toolResult": _ANY,
    "tool_result": _ANY,
    "suppressOriginalOutput": _BOOLEAN,
    "suppress_original_output": _BOOLEAN,
}
_CONTINUATION_PROPERTIES: dict[str, JsonSchema] = {
    "continuationPrompt": _STRING,
    "continuation_prompt": _STRING,
}


def _decision_schema(*values: str) -> JsonSchema:
    """构建只允许指定决策文本的 schema。"""
    return {"type": "string", "enum": list(values)}


def _output_schema(
    event: HookEventName,
    *groups: dict[str, JsonSchema],
) -> JsonSchema:
    """构建事件输出 schema。"""
    properties: dict[str, JsonSchema] = {}
    for group in groups:
        properties.update(group)
    properties["hookSpecificOutput"] = _object_schema({
        "hookEventName": {
            "type": "string",
            "const": event,
        },
        **properties,
    })
    return _object_schema(properties)


HOOK_OUTPUT_SCHEMAS: dict[HookEventName, JsonSchema] = {
    "PreToolUse": _output_schema(
        "PreToolUse",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("allow", "deny", "block")},
        _UPDATED_INPUT_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "PermissionRequest": _output_schema(
        "PermissionRequest",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("allow", "deny", "abstain")},
    ),
    "PostToolUse": _output_schema(
        "PostToolUse",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("allow", "block")},
        _REPLACEMENT_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "PreCompact": _output_schema(
        "PreCompact",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("allow", "deny", "block")},
    ),
    "PostCompact": _output_schema(
        "PostCompact",
        _BASE_OUTPUT_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SessionStart": _output_schema(
        "SessionStart",
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "UserPromptSubmit": _output_schema(
        "UserPromptSubmit",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("allow", "deny", "block")},
        _UPDATED_INPUT_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SubagentStart": _output_schema(
        "SubagentStart",
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SubagentStop": _output_schema(
        "SubagentStop",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("block")},
        _CONTINUATION_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
    "Stop": _output_schema(
        "Stop",
        _BASE_OUTPUT_PROPERTIES,
        {"decision": _decision_schema("block")},
        _CONTINUATION_PROPERTIES,
        _CONTEXT_OUTPUT_PROPERTIES,
    ),
}


def hook_input_schema(event: HookEventName) -> JsonSchema:
    """返回指定事件的输入 schema。"""
    return HOOK_INPUT_SCHEMAS[event]


def hook_output_schema(event: HookEventName) -> JsonSchema:
    """返回指定事件的输出 schema。"""
    return HOOK_OUTPUT_SCHEMAS[event]


def validate_hook_input(
    event: HookEventName,
    value: dict[str, typing.Any]
) -> None:
    """校验指定事件的输入对象。"""
    _validate_schema(value, hook_input_schema(event), path=f"{event} input")


def validate_hook_output(
    event: HookEventName,
    value: dict[str, typing.Any]
) -> None:
    """校验指定事件的输出对象。"""
    _validate_schema(value, hook_output_schema(event), path=f"{event} output")


def validate_hook_protocol_catalog() -> None:
    """校验输入输出 schema 与事件目录保持一致。"""
    configured = set(HOOK_EVENT_NAMES)
    if configured != set(HOOK_INPUT_SCHEMAS):
        raise RuntimeError("hook input schema catalog mismatch")
    if configured != set(HOOK_OUTPUT_SCHEMAS):
        raise RuntimeError("hook output schema catalog mismatch")


def _validate_schema(
    value: typing.Any,
    schema: JsonSchema,
    *,
    path: str
) -> None:
    """校验协议使用的 JSON schema 子集。"""
    if not schema:
        return None

    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        for alternative in alternatives:
            try:
                _validate_schema(value, alternative, path=path)
            except ValueError:
                continue
            return None
        raise ValueError(f"{path} does not match any allowed type")

    expected = schema.get("type")
    if expected is not None and not _matches_type(value, expected):
        expected_text = "/".join(expected) if isinstance(expected, list) else expected
        raise ValueError(f"{path} must be {expected_text}")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must be {schema['const']}")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        raise ValueError(f"{path} must be one of {', '.join(map(str, allowed))}")

    if expected == "object":
        _validate_object(value, schema, path=path)
    elif expected == "array":
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_object(
    value: dict[str, typing.Any],
    schema: JsonSchema,
    *,
    path: str
) -> None:
    """校验对象字段、必填项和未知字段。"""
    properties = schema.get("properties", {})
    required   = schema.get("required", ())

    for key in required:
        if key not in value:
            raise ValueError(f"{path}.{key} is required")

    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value).difference(properties))
        if unknown:
            raise ValueError(f"unknown {path} field: {unknown[0]}")

    for key, item in value.items():
        field_schema = properties.get(key)
        if field_schema is not None:
            _validate_schema(item, field_schema, path=f"{path}.{key}")


def _matches_type(value: typing.Any, expected: str | list[str]) -> bool:
    """判断值是否符合 JSON 类型声明。"""
    expected_types = expected if isinstance(expected, list) else [expected]

    return any(
        _matches_single_type(value, candidate)
        for candidate in expected_types
    )


def _matches_single_type(value: typing.Any, expected: str) -> bool:
    """判断值是否符合单个 JSON 类型。"""
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)

    return False


validate_hook_protocol_catalog()


if __name__ == '__main__':
    pass
