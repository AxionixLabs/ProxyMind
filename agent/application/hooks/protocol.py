# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.hooks import (
    HOOK_EVENT_NAMES,
    HookEventName,
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


_STRING: JsonSchema = {"type": "string"}
_BOOLEAN: JsonSchema = {"type": "boolean"}
_INTEGER: JsonSchema = {"type": "integer"}

_NULLABLE_STRING: JsonSchema = {"type": ["string", "null"]}

_OBJECT: JsonSchema = _object_schema({}, additional_properties=True)

_ANY: JsonSchema = {}

_CONTEXT: JsonSchema = _STRING

_PERMISSION_MODE: JsonSchema = {
    "type": "string",
    "enum": [
        "default",
        "acceptEdits",
        "plan",
        "dontAsk",
        "bypassPermissions",
    ],
}

_SESSION_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "session_id": _STRING,
    "transcript_path": _NULLABLE_STRING,
    "cwd": _STRING,
}

_MODEL_INPUT_PROPERTIES: dict[str, JsonSchema] = {"model": _STRING}

_TURN_INPUT_PROPERTIES: dict[str, JsonSchema] = {"turn_id": _STRING}

_PERMISSION_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "permission_mode": _PERMISSION_MODE,
}

_AGENT_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "agent_id": _STRING,
    "agent_type": _STRING,
}

_TOOL_INPUT_PROPERTIES: dict[str, JsonSchema] = {
    "tool_name": _STRING,
    "tool_input": _ANY,
}


def _input_schema(
    event: HookEventName,
    *groups: dict[str, JsonSchema],
    required: typing.Iterable[str],
    include_model: bool = True
) -> JsonSchema:
    """构建命令 Hook 的事件输入结构。"""
    properties = dict(_SESSION_INPUT_PROPERTIES)
    if include_model:
        properties.update(_MODEL_INPUT_PROPERTIES)
    for group in groups:
        properties.update(group)

    properties["hook_event_name"] = {
        "type": "string",
        "const": event,
    }

    return _object_schema(
        properties,
        required=(
            *_SESSION_INPUT_PROPERTIES,
            *(_MODEL_INPUT_PROPERTIES if include_model else ()),
            "hook_event_name",
            *required,
        ),
    )


HOOK_INPUT_SCHEMAS: dict[HookEventName, JsonSchema] = {
    "PreToolUse": _input_schema(
        "PreToolUse",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        _TOOL_INPUT_PROPERTIES,
        {"tool_use_id": _STRING},
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            *_TOOL_INPUT_PROPERTIES,
            "tool_use_id",
        ),
    ),
    "PermissionRequest": _input_schema(
        "PermissionRequest",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        _TOOL_INPUT_PROPERTIES,
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            *_TOOL_INPUT_PROPERTIES,
        ),
    ),
    "PostToolUse": _input_schema(
        "PostToolUse",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        _TOOL_INPUT_PROPERTIES,
        {
            "tool_use_id": _STRING,
            "tool_response": _ANY,
        },
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            *_TOOL_INPUT_PROPERTIES,
            "tool_use_id",
            "tool_response",
        ),
    ),
    "PreCompact": _input_schema(
        "PreCompact",
        _TURN_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        {
            "trigger": {
                "type": "string",
                "enum": ["manual", "auto"],
            },
        },
        required=(*_TURN_INPUT_PROPERTIES, "trigger"),
    ),
    "PostCompact": _input_schema(
        "PostCompact",
        _TURN_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        {
            "trigger": {
                "type": "string",
                "enum": ["manual", "auto"],
            },
        },
        required=(*_TURN_INPUT_PROPERTIES, "trigger"),
    ),
    "SessionStart": _input_schema(
        "SessionStart",
        _PERMISSION_INPUT_PROPERTIES,
        {
            "source": {
                "type": "string",
                "enum": ["startup", "resume", "clear", "compact"],
            },
        },
        required=(*_PERMISSION_INPUT_PROPERTIES, "source"),
    ),
    "SessionEnd": _input_schema(
        "SessionEnd",
        {
            "reason": {
                "type": "string",
                "const": "other",
            },
        },
        required=("reason",),
        include_model=False,
    ),
    "UserPromptSubmit": _input_schema(
        "UserPromptSubmit",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        {"prompt": _STRING},
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            "prompt",
        ),
    ),
    "SubagentStart": _input_schema(
        "SubagentStart",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            *_AGENT_INPUT_PROPERTIES,
        ),
    ),
    "SubagentStop": _input_schema(
        "SubagentStop",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        _AGENT_INPUT_PROPERTIES,
        {
            "agent_transcript_path": _NULLABLE_STRING,
            "stop_hook_active": _BOOLEAN,
            "last_assistant_message": _NULLABLE_STRING,
        },
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            *_AGENT_INPUT_PROPERTIES,
            "agent_transcript_path",
            "stop_hook_active",
            "last_assistant_message",
        ),
    ),
    "Stop": _input_schema(
        "Stop",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        {
            "stop_hook_active": _BOOLEAN,
            "last_assistant_message": _NULLABLE_STRING,
        },
        required=(
            *_TURN_INPUT_PROPERTIES,
            *_PERMISSION_INPUT_PROPERTIES,
            "stop_hook_active",
            "last_assistant_message",
        ),
    ),
    "Interrupt": _input_schema(
        "Interrupt",
        _TURN_INPUT_PROPERTIES,
        _PERMISSION_INPUT_PROPERTIES,
        required=(*_TURN_INPUT_PROPERTIES, *_PERMISSION_INPUT_PROPERTIES),
    ),
}

_COMMON_OUTPUT_PROPERTIES: dict[str, JsonSchema] = {
    "continue": _BOOLEAN,
    "stopReason": _STRING,
    "suppressOutput": _BOOLEAN,
    "systemMessage": _STRING,
}

_CONTEXT_OUTPUT_PROPERTIES: dict[str, JsonSchema] = {
    "additionalContext": _CONTEXT,
}

_SPILL_DETAIL_SCHEMA = _object_schema(
    {
        "path": _STRING,
        "size_bytes": _INTEGER,
        "head": _STRING,
        "tail": _STRING,
    },
    required=("path", "size_bytes", "head", "tail"),
)

_COMMAND_OUTPUT_PROPERTIES: dict[str, JsonSchema] = {
    "stdout": _STRING,
    "outputSpill": _object_schema({
        "stdout": _SPILL_DETAIL_SCHEMA,
        "stderr": _SPILL_DETAIL_SCHEMA,
    }),
}


def _decision_schema(*values: str) -> JsonSchema:
    """构建只允许指定决策文本的 schema。"""
    return {"type": "string", "enum": list(values)}


def _output_schema(
    event: HookEventName,
    *groups: dict[str, JsonSchema],
    specific: dict[str, JsonSchema] | None = None
) -> JsonSchema:
    """构建命令 Hook 的事件输出结构。"""
    properties = dict(_COMMAND_OUTPUT_PROPERTIES)

    for group in groups:
        properties.update(group)

    if specific is not None:
        properties["hookSpecificOutput"] = _object_schema(
            {
                "hookEventName": {
                    "type": "string",
                    "const": event,
                },
                **specific,
            },
            required=("hookEventName",),
        )

    return _object_schema(properties)


_PERMISSION_REQUEST_DECISION = _object_schema(
    {
        "behavior": _decision_schema("allow", "deny"),
        "message": _STRING,
        "interrupt": _BOOLEAN,
        "updatedInput": _ANY,
        "updatedPermissions": _ANY,
    },
    required=("behavior",),
)

HOOK_OUTPUT_SCHEMAS: dict[HookEventName, JsonSchema] = {
    "PreToolUse": _output_schema(
        "PreToolUse",
        _COMMON_OUTPUT_PROPERTIES,
        {
            "decision": _decision_schema("approve", "block"),
            "reason": _STRING,
        },
        specific={
            **_CONTEXT_OUTPUT_PROPERTIES,
            "permissionDecision": _decision_schema("allow", "deny", "ask"),
            "permissionDecisionReason": _STRING,
            "updatedInput": _ANY,
        },
    ),
    "PermissionRequest": _output_schema(
        "PermissionRequest",
        _COMMON_OUTPUT_PROPERTIES,
        specific={"decision": _PERMISSION_REQUEST_DECISION},
    ),
    "PostToolUse": _output_schema(
        "PostToolUse",
        _COMMON_OUTPUT_PROPERTIES,
        {
            "decision": _decision_schema("block"),
            "reason": _STRING,
        },
        specific=_CONTEXT_OUTPUT_PROPERTIES,
    ),
    "PreCompact": _output_schema(
        "PreCompact",
        _COMMON_OUTPUT_PROPERTIES,
    ),
    "PostCompact": _output_schema(
        "PostCompact",
        _COMMON_OUTPUT_PROPERTIES,
    ),
    "SessionStart": _output_schema(
        "SessionStart",
        _COMMON_OUTPUT_PROPERTIES,
        specific=_CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SessionEnd": _output_schema("SessionEnd"),
    "UserPromptSubmit": _output_schema(
        "UserPromptSubmit",
        _COMMON_OUTPUT_PROPERTIES,
        {
            "decision": _decision_schema("block"),
            "reason": _STRING,
        },
        specific=_CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SubagentStart": _output_schema(
        "SubagentStart",
        _COMMON_OUTPUT_PROPERTIES,
        specific=_CONTEXT_OUTPUT_PROPERTIES,
    ),
    "SubagentStop": _output_schema(
        "SubagentStop",
        _COMMON_OUTPUT_PROPERTIES,
        {
            "decision": _decision_schema("block"),
            "reason": _STRING,
        },
    ),
    "Stop": _output_schema(
        "Stop",
        _COMMON_OUTPUT_PROPERTIES,
        {
            "decision": _decision_schema("block"),
            "reason": _STRING,
        },
    ),
    "Interrupt": _object_schema(
        {"systemMessage": _STRING},
    ),
}


def hook_input_schema(event: HookEventName) -> JsonSchema:
    """返回指定事件的输入 schema。"""
    return HOOK_INPUT_SCHEMAS[event]


def hook_output_schema(event: HookEventName) -> JsonSchema:
    """返回指定事件的输出 schema。"""
    return HOOK_OUTPUT_SCHEMAS[event]


def build_hook_input(
    event: HookEventName,
    *,
    session_id: str,
    transcript_path: str | None,
    cwd: str,
    model: str,
    permission_mode: str,
    turn_id: str,
    agent_id: str,
    agent_type: str,
    include_agent: bool,
    payload: dict[str, typing.Any] | None = None
) -> dict[str, typing.Any]:
    """按事件 schema 构建命令 Hook 的 stdin 对象。"""
    properties = hook_input_schema(event)["properties"]

    common = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "hook_event_name": event,
        "model": model,
        "permission_mode": permission_mode,
        "turn_id": turn_id,
    }

    if include_agent:
        common.update({
            "agent_id": agent_id,
            "agent_type": agent_type,
        })

    result = {
        key: value
        for key, value in (payload or {}).items()
        if key in properties
    }
    result.update({
        key: value
        for key, value in common.items()
        if key in properties
    })

    return result


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
    if tuple(HOOK_INPUT_SCHEMAS) != HOOK_EVENT_NAMES:
        raise RuntimeError("hook input schema catalog mismatch")
    if tuple(HOOK_OUTPUT_SCHEMAS) != HOOK_EVENT_NAMES:
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
    required = schema.get("required", ())

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
