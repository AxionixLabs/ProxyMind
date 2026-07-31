# -*- coding: utf-8 -*-

import json

import pytest

from mind_app.runtime.hooks.effects import normalize_business_block
from mind_app.runtime.hooks.events import HOOK_EVENT_SPECS
from mind_app.runtime.hooks.protocol import (
    HOOK_INPUT_SCHEMAS,
    HOOK_OUTPUT_SCHEMAS,
    validate_hook_input,
    validate_hook_output,
)
from mind_core.hooks import HOOK_EVENT_NAMES


def _prompt_input(**overrides):
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sid",
        "root_session_id": "sid",
        "conversation_id": "cid",
        "turn_id": "turn",
        "cwd": ".",
        "model": "model",
        "mode": "chat",
        "source": "test",
        "sandbox_mode": "workspace-write",
        "permission_mode": "on-request",
        "agent_id": "root",
        "agent_type": "root",
        "agent_depth": 0,
        "parent_agent_id": None,
        "session_started": False,
        "session_start_reason": "",
        "prompt": "hello",
    }
    payload.update(overrides)
    return payload


def test_protocol_catalog_covers_all_hook_events() -> None:
    assert tuple(HOOK_INPUT_SCHEMAS) == HOOK_EVENT_NAMES
    assert tuple(HOOK_OUTPUT_SCHEMAS) == HOOK_EVENT_NAMES
    assert tuple(HOOK_EVENT_SPECS) == HOOK_EVENT_NAMES


def test_protocol_schemas_do_not_expose_schema_version() -> None:
    serialized = json.dumps({
        "input": HOOK_INPUT_SCHEMAS,
        "output": HOOK_OUTPUT_SCHEMAS,
    })

    assert "schema_version" not in serialized


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


def test_output_schema_validates_event_specific_output() -> None:
    validate_hook_output("PreToolUse", {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "decision": "allow",
            "updatedInput": {"command": "rg TODO"},
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
    with pytest.raises(ValueError, match="must be one of allow, deny, block"):
        validate_hook_output("PreToolUse", {"decision": "unknown"})


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
