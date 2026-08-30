# -*- coding: utf-8 -*-

import pytest

from mind_app.runtime.hooks.effects import (
    normalize_business_block,
    normalize_hook_output,
)
from mind_app.runtime.hooks.events import HOOK_EVENT_SPECS
from mind_app.runtime.hooks.protocol import (
    HOOK_INPUT_SCHEMAS,
    HOOK_OUTPUT_SCHEMAS,
    validate_hook_input,
    validate_hook_output,
)
from agent.application import HOOK_EVENT_NAMES


def _prompt_input(**overrides):
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sid",
        "transcript_path": None,
        "cwd": ".",
        "model": "model",
        "turn_id": "turn",
        "permission_mode": "default",
        "prompt": "hello",
    }
    payload.update(overrides)
    return payload


def test_protocol_catalog_covers_all_hook_events() -> None:
    assert tuple(HOOK_INPUT_SCHEMAS) == HOOK_EVENT_NAMES
    assert tuple(HOOK_OUTPUT_SCHEMAS) == HOOK_EVENT_NAMES
    assert tuple(HOOK_EVENT_SPECS) == HOOK_EVENT_NAMES


def test_protocol_schemas_reject_unknown_top_level_fields() -> None:
    assert all(
        schema["additionalProperties"] is False
        for schema in (*HOOK_INPUT_SCHEMAS.values(), *HOOK_OUTPUT_SCHEMAS.values())
    )


def test_input_schema_requires_matching_event_name() -> None:
    with pytest.raises(ValueError, match="must be UserPromptSubmit"):
        validate_hook_input(
            "UserPromptSubmit",
            _prompt_input(hook_event_name="Stop"),
        )


def test_input_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown UserPromptSubmit input field"):
        validate_hook_input(
            "UserPromptSubmit",
            _prompt_input(extra=True),
        )


def test_session_end_input_rejects_unknown_reason() -> None:
    payload = _prompt_input(
        hook_event_name="SessionEnd",
        reason="reset",
    )
    payload = {
        key: value
        for key, value in payload.items()
        if key in {
            "hook_event_name",
            "session_id",
            "transcript_path",
            "cwd",
            "reason",
        }
    }

    with pytest.raises(ValueError, match="must be other"):
        validate_hook_input("SessionEnd", payload)


def test_lifecycle_schemas_use_codex_trigger_contracts() -> None:
    session_end = HOOK_INPUT_SCHEMAS["SessionEnd"]["properties"]
    pre_compact = HOOK_INPUT_SCHEMAS["PreCompact"]["properties"]
    post_compact = HOOK_INPUT_SCHEMAS["PostCompact"]["properties"]

    assert session_end["reason"]["const"] == "other"
    assert pre_compact["trigger"]["enum"] == ["manual", "auto"]
    assert post_compact["trigger"]["enum"] == ["manual", "auto"]


def test_output_schema_validates_event_specific_output() -> None:
    validate_hook_output("PreToolUse", {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "rg TODO"},
        },
    })

    validate_hook_output("PermissionRequest", {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny", "message": "blocked"},
        },
    })


def test_output_schema_rejects_mismatched_specific_event() -> None:
    with pytest.raises(ValueError, match="must be PreToolUse"):
        validate_hook_output("PreToolUse", {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
            },
        })


def test_output_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown Stop output field"):
        validate_hook_output("Stop", {"unexpected": True})


def test_output_schema_rejects_invalid_decision() -> None:
    with pytest.raises(ValueError, match="must be one of approve, block"):
        validate_hook_output("PreToolUse", {"decision": "unknown"})


def test_post_compact_output_rejects_hook_specific_context() -> None:
    with pytest.raises(ValueError, match="unknown PostCompact output field"):
        validate_hook_output("PostCompact", {
            "hookSpecificOutput": {
                "hookEventName": "PostCompact",
                "additionalContext": "late context",
            },
        })


def test_user_prompt_output_rejects_updated_input() -> None:
    with pytest.raises(
        ValueError,
        match="unknown UserPromptSubmit output.hookSpecificOutput field",
    ):
        validate_hook_output("UserPromptSubmit", {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "updatedInput": {"prompt": "rewritten"},
            },
        })


@pytest.mark.parametrize("field,value", [
    ("continue", False),
    ("stopReason", "stop"),
    ("suppressOutput", True),
])
def test_permission_request_rejects_unsupported_universal_fields(
    field,
    value,
) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalize_hook_output("PermissionRequest", {field: value})


@pytest.mark.parametrize("field,value", [
    ("updatedInput", {}),
    ("updatedPermissions", {}),
    ("interrupt", True),
])
def test_permission_request_rejects_reserved_decision_fields(
    field,
    value,
) -> None:
    with pytest.raises(ValueError, match=field):
        normalize_hook_output("PermissionRequest", {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    field: value,
                },
            },
        })


def test_permission_request_supplies_default_denial_message() -> None:
    normalized = normalize_hook_output("PermissionRequest", {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny"},
        },
    })

    assert normalized.effect.reason == "PermissionRequest hook denied approval"


def test_pre_tool_use_requires_allow_to_include_updated_input() -> None:
    with pytest.raises(ValueError, match="requires updatedInput"):
        normalize_hook_output("PreToolUse", {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            },
        })


def test_post_tool_use_rejects_reserved_result_rewrite() -> None:
    with pytest.raises(ValueError, match="updatedMCPToolOutput"):
        normalize_hook_output("PostToolUse", {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": {},
            },
        })


def test_post_tool_use_stop_is_distinct_from_block() -> None:
    normalized = normalize_hook_output("PostToolUse", {
        "continue": False,
        "stopReason": "stop processing hook output",
        "reason": "review the tool result",
    })

    assert normalized.effect.stop_requested
    assert normalized.effect.decision == ""
    assert normalized.effect.reason == "review the tool result"
    assert not normalized.effect.continue_execution


def test_post_tool_use_stop_reason_becomes_feedback_fallback() -> None:
    normalized = normalize_hook_output("PostToolUse", {
        "continue": False,
        "stopReason": "review before continuing",
    })

    assert normalized.effect.stop_requested
    assert normalized.effect.reason == "review before continuing"


def test_post_tool_use_block_is_not_a_stop_request() -> None:
    normalized = normalize_hook_output("PostToolUse", {
        "decision": "block",
        "reason": "reject this result",
    })

    assert not normalized.effect.stop_requested
    assert normalized.effect.decision == "block"
    assert not normalized.effect.continue_execution


def test_output_schema_accepts_transport_spill_metadata() -> None:
    validate_hook_output("SessionStart", {
        "stdout": "output spilled",
        "outputSpill": {
            "stdout": {
                "path": "D:/tmp/stdout.log",
                "size_bytes": 300000,
                "head": "first",
                "tail": "last",
            },
        },
    })


def test_business_block_maps_to_permission_denial() -> None:
    normalized = normalize_business_block(
        "PermissionRequest",
        reason="policy denied",
    )

    assert normalized.effect.decision == "deny"
    assert not normalized.effect.continue_execution


def test_business_block_maps_stop_to_continuation() -> None:
    normalized = normalize_business_block(
        "Stop",
        reason="continue checking",
    )

    assert normalized.effect.decision == "block"
    assert normalized.effect.continuation_prompt == "continue checking"
