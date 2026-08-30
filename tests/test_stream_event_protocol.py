# -*- coding: utf-8 -*-

import pytest

from protocol.schema.stream_events import (
    PresentationSupersededEvent,
    StreamGapEvent,
    TextMetaEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnRetryingEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent,
    UnknownStreamEvent,
    parse_stream_event as _parse_stream_event,
)
from mind_app.runtime.turns.stream_tools import ToolCallBatchBuffer


def parse_stream_event(payload):
    """为协议单测补齐当前 wire envelope。"""
    current = dict(payload)
    if str(current.get("type") or "") != "ping":
        current.setdefault("proto", "mind.chat")
        current.setdefault("cid", "cid_test")
        current.setdefault("sid", "sid_test")
        current.setdefault("turn_id", "turn_test")
        current.setdefault("event_seq", 1)
        current.setdefault("presentation_epoch", 1)
        if str(current.get("type") or "").startswith("text."):
            current.setdefault("segment_id", "segment_test")
        _complete_item_projection(current)
        if str(current.get("type") or "") == "tool.approval_required":
            current.setdefault("approval_id", "approval_test")
            current.setdefault("started_at_ms", 0)
            current.setdefault("status", "pending")
            current.setdefault("ack", None)
            current.setdefault("reason", "")
            current.setdefault(
                "available_decisions",
                ["accept", "acceptForSession", "decline"],
            )
            kind = current.get("kind", "command")
            if kind == "command":
                current.setdefault("environment_id", "workspace-write")
                if isinstance(current.get("command"), str):
                    current["command"] = [current["command"]]
                current.setdefault("command", ["echo", "ready"])
                current.setdefault("cwd", ".")
                current.setdefault("cwd_raw", ".")
                current.setdefault("tty", False)
                current.setdefault("sandbox_permissions", "use_default")
                current.setdefault("additional_permissions", None)
                current.setdefault("proposed_execpolicy_amendment", None)
                current.setdefault("parsed_cmd", [])
            elif kind == "apply_patch":
                current.setdefault("environment_id", "workspace-write")
                current.setdefault("cwd_raw", current.get("cwd", "."))
                current.setdefault(
                    "files",
                    current.get("patch_scope", ["app.py"]),
                )
                current.pop("patch_scope", None)
                current.setdefault("permissions_preapproved", False)
            elif kind == "network_access":
                current.setdefault("environment_id", "workspace-write")
                current.setdefault("cwd", ".")
                current.setdefault("cwd_raw", ".")
            elif kind == "request_permissions":
                current.setdefault("permissions", {})
            elif kind == "mcp_tool_call":
                current.setdefault("server", "server")
                current.setdefault("tool_name", "tool")
                current.setdefault("arguments", {})
                current.setdefault("mcp_request_id", "mcp_request_test")
    return _parse_stream_event(current)


def _complete_item_projection(payload) -> None:
    """按正式协议为测试事件补齐 Canonical Item 字段。"""
    event_type = str(payload.get("type") or "")
    projection = None
    if event_type.startswith("text."):
        projection = (
            payload.get("segment_id"),
            "text",
            "in_progress" if event_type == "text.delta" else "completed",
        )
    elif event_type == "tool.call":
        projection = (payload.get("call_id"), "tool_call", "waiting_result")
    elif event_type == "tool.output":
        output_status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(payload.get("status"), "result_received")
        projection = (
            f"{payload.get('call_id')}:output",
            "tool_output",
            output_status,
        )
    elif event_type == "tool.approval_required":
        projection = (
            payload.get("approval_id") or "approval_test",
            "approval",
            "waiting_approval",
        )
    elif event_type.startswith("tool.builtin."):
        status = "in_progress" if event_type.endswith("call") else "completed"
        projection = (
            payload.get("builtin_call_id") or "builtin_test",
            "builtin_tool",
            status,
        )
    if projection is None:
        return
    item_id, item_kind, item_status = projection
    payload.setdefault("item_id", item_id)
    payload.setdefault("item_kind", item_kind)
    payload.setdefault("item_status", item_status)


def _durable_tool_event() -> dict:
    """构造最新客户端工具调用协议事件。"""
    return {
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "type": "tool.call",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 2,
        "name": "shell_command",
        "call_id": "call_test",
        "arguments": {"command": "echo ready"},
        "reason": "模型需要检查命令输出。",
    }


def test_latest_tool_protocol_parses_direct_tool_fields() -> None:
    event = parse_stream_event(_durable_tool_event())

    assert isinstance(event, ToolCallEvent)
    assert event.name == "shell_command"
    assert event.call_id == "call_test"
    assert event.arguments == {"command": "echo ready"}
    assert event.reason == "模型需要检查命令输出。"


def test_tool_call_batch_boundaries_are_typed_and_strict() -> None:
    start = parse_stream_event({
        "type": "tool.calls.start",
        "batch_id": "batch-1",
        "call_ids": ["call-a", "call-b"],
        "count": 2,
        "ready": True,
        "timeout_sec": 60,
    })
    done = parse_stream_event({
        "type": "tool.calls.done",
        "batch_id": "batch-1",
        "call_ids": ["call-a", "call-b"],
        "count": 2,
    })

    assert isinstance(start, ToolCallsStartEvent)
    assert start.call_ids == ("call-a", "call-b")
    assert start.timeout_sec == 60
    assert isinstance(done, ToolCallsDoneEvent)
    assert done.call_ids == start.call_ids

    for invalid in (
        {"ready": False},
        {"count": 1},
        {"call_ids": ["call-a", "call-a"]},
    ):
        payload = {
            "type": "tool.calls.start",
            "batch_id": "batch-1",
            "call_ids": ["call-a", "call-b"],
            "count": 2,
            "ready": True,
            **invalid,
        }
        with pytest.raises(ValueError):
            parse_stream_event(payload)


def test_formal_item_projection_requires_matching_domain_identity() -> None:
    payload = {
        "type": "text.delta",
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "segment_id": "segment-1",
        "item_id": "segment-1",
        "item_kind": "text",
        "item_status": "in_progress",
        "text": "answer",
    }

    event = _parse_stream_event(payload)

    assert event.item_id == "segment-1"
    assert event.item_kind == "text"
    assert event.item_status == "in_progress"

    payload["item_id"] = "another-segment"
    with pytest.raises(ValueError, match="domain identity"):
        _parse_stream_event(payload)


def test_formal_item_projection_is_required_only_for_display_events() -> None:
    display_event = {
        "type": "tool.call",
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "call_id": "call-1",
        "name": "test_tool",
        "arguments": {},
    }
    with pytest.raises(ValueError, match="item_id is required"):
        _parse_stream_event(display_event)

    control_event = {
        "type": "turn.start",
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "item_id": "forbidden",
    }
    with pytest.raises(ValueError, match="must not carry Item projection"):
        _parse_stream_event(control_event)


def test_stream_gap_is_a_nonpersistent_control_event() -> None:
    gap = _parse_stream_event({
        "type": "stream.gap",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "gap_kind": "retained_prefix",
        "requested_after_seq": 2,
        "first_event_seq": 5,
        "next_seq": 4,
        "replay_required": False,
        "retryable": False,
    })

    assert isinstance(gap, StreamGapEvent)
    assert gap.event_seq is None
    assert gap.next_seq == 4

    with pytest.raises(ValueError, match="sequence coordinates"):
        _parse_stream_event({
            "type": "stream.gap",
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_test",
            "gap_kind": "internal",
            "requested_after_seq": 2,
        })


def test_tool_call_requires_name_and_call_id() -> None:
    with pytest.raises(ValueError, match="name is required"):
        parse_stream_event({
            "type": "tool.call",
            "call_id": "call-a",
            "reason": "execute",
        })
    with pytest.raises(ValueError, match="call_id is required"):
        parse_stream_event({
            "type": "tool.call",
            "name": "shell_command",
            "arguments": {"command": "pwd"},
            "reason": "execute",
        })


def test_presentation_superseded_requires_preceding_epoch() -> None:
    event = parse_stream_event({
        "proto": "mind.chat",
        "type": "presentation.superseded",
        "turn_id": "turn_test",
        "presentation_epoch": 2,
        "superseded_epoch": 1,
    })
    assert isinstance(event, PresentationSupersededEvent)

    with pytest.raises(ValueError, match="must precede"):
        parse_stream_event({
            "proto": "mind.chat",
            "type": "presentation.superseded",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "superseded_epoch": 2,
        })


def test_turn_retrying_requires_strict_attempt_metadata() -> None:
    """校验 provider 重试事件的类型化字段和边界。"""
    event = parse_stream_event({
        "type": "turn.retrying",
        "turn_id": "turn_test",
        "event_seq": 7,
        "round": 2,
        "attempt": 2,
        "max_attempts": 3,
        "retry_in_ms": 250,
        "reason": "stream_reset",
        "error_type": "provider_error",
        "error": {
            "type": "provider_error",
            "source": "provider",
            "retryable": True,
        },
    })

    assert isinstance(event, TurnRetryingEvent)
    assert event.attempt == 2
    assert event.max_attempts == 3
    assert event.retry_in_ms == 250
    assert event.error_type == "provider_error"
    assert event.error_source == "provider"
    assert event.retryable is True

    for invalid in (
        {"attempt": 0, "max_attempts": 3, "retry_in_ms": 0},
        {"attempt": 4, "max_attempts": 3, "retry_in_ms": 0},
        {"attempt": 2, "max_attempts": 3, "retry_in_ms": -1},
        {"attempt": 2, "max_attempts": True, "retry_in_ms": 0},
        {"attempt": 2, "max_attempts": 3, "retry_in_ms": True},
    ):
        with pytest.raises(ValueError):
            parse_stream_event({"type": "turn.retrying", "round": 2, **invalid})

    with pytest.raises(ValueError, match="round"):
        parse_stream_event({
            "type": "turn.retrying",
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 0,
        })

    with pytest.raises(ValueError, match="error.type"):
        parse_stream_event({
            "type": "turn.retrying",
            "round": 2,
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 0,
            "error_type": "provider_error",
            "error": {
                "type": "different_error",
                "source": "provider",
                "retryable": True,
            },
        })


def test_text_meta_event_copies_structured_metadata() -> None:
    sources = [{"url": "https://example.com"}]
    payload = {
        "type": "text.meta",
        "segment_id": "segment-1",
        "annotations": [{"start": 0}],
        "citations": [{"source": 0}],
        "sources": sources,
        "source_count": 1,
        "builtin_call_ids": ["builtin-1"],
        "proto": "mind.chat",
        "round": 2,
    }

    event = parse_stream_event(payload)
    sources[0]["url"] = "changed"

    assert isinstance(event, TextMetaEvent)
    assert event.segment_id == "segment-1"
    assert event.sources == ({"url": "https://example.com"},)
    assert event.source_count == 1
    assert event.builtin_call_ids == ("builtin-1",)
    assert event.proto == "mind.chat"
    assert event.round == 2


def test_tool_call_event_rejects_removed_tool_name_alias() -> None:
    payload = _durable_tool_event()
    payload.pop("name")
    payload.update({
        "tool": "shell_command",
        "call_id": "call-1",
        "arguments": {"command": "pytest -q"},
        "reason": "模型需要运行测试。",
    })
    with pytest.raises(ValueError, match="removed protocol field"):
        parse_stream_event(payload)


def test_tool_call_event_accepts_calls_without_execution_metadata() -> None:
    event = parse_stream_event({
        "type": "tool.call",
        "name": "shell_command",
        "call_id": "call_without_execution",
        "arguments": {"command": "pwd"},
        "reason": "模型需要确认工作目录。",
    })

    assert isinstance(event, ToolCallEvent)
    assert event.reason == "模型需要确认工作目录。"


def test_tool_output_rejects_status_outside_formal_contract() -> None:
    with pytest.raises(ValueError, match="status is invalid"):
        parse_stream_event({
            "type": "tool.output",
            "name": "test_tool",
            "call_id": "call-1",
            "status": "reconciliation_required",
        })


@pytest.mark.parametrize(
    "name",
    ("shell_command", "exec_command", "write_stdin", "apply_patch"),
)
def test_client_tool_call_allows_missing_optional_reason(name) -> None:
    event = parse_stream_event({
        "type": "tool.call",
        "name": name,
        "call_id": f"call_without_reason_{name}",
        "arguments": {},
    })

    assert isinstance(event, ToolCallEvent)
    assert event.reason == ""


def test_turn_reconciliation_required_is_a_typed_terminal_pause() -> None:
    event = parse_stream_event({
        "proto": "mind.chat",
        "type": "turn.reconciliation_required",
        "turn_id": "turn_test",
        "presentation_epoch": 2,
        "status": "reconciliation_required",
        "effect_id": "effect_test",
        "error": "provider succeeded but commit failed",
    })

    assert isinstance(event, TurnReconciliationRequiredEvent)
    assert event.effect_id == "effect_test"
    assert event.status == "reconciliation_required"


def test_stream_event_rejects_fractional_integer_fields() -> None:
    event = parse_stream_event({
        "type": "text.meta",
        "round": 1.5,
        "source_count": 2.5,
    })

    assert isinstance(event, TextMetaEvent)
    assert event.round is None
    assert event.source_count is None


def test_unknown_event_preserves_extension_payload_and_display() -> None:
    event = parse_stream_event({
        "type": "provider.progress",
        "display": {"text": "Loading"},
        "provider_data": {"step": 2},
    })

    assert isinstance(event, UnknownStreamEvent)
    assert event.display == {"text": "Loading"}
    assert event.payload["provider_data"] == {"step": 2}


def test_stream_event_preserves_turn_identity_and_event_sequence() -> None:
    event = parse_stream_event({
        "type": "text.delta",
        "turn_id": "turn_1",
        "event_seq": 41,
        "text": "answer",
    })

    assert event.turn_id == "turn_1"
    assert event.cid == "cid_test"
    assert event.sid == "sid_test"
    assert event.event_seq == 41


@pytest.mark.parametrize("event_type", ("text.delta", "text.done", "text.meta"))
@pytest.mark.parametrize("segment_id", (None, "", "   "))
def test_text_events_require_stable_item_identity(event_type, segment_id) -> None:
    payload = {
        "type": event_type,
        "segment_id": segment_id,
        "text": "answer",
    }

    with pytest.raises(ValueError, match=f"{event_type} segment_id"):
        _parse_stream_event({
            **payload,
            "proto": "mind.chat",
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_test",
            "event_seq": 1,
            "presentation_epoch": 1,
        })


def test_text_event_segment_id_is_exposed_as_item_id() -> None:
    event = parse_stream_event({
        "type": "text.delta",
        "segment_id": "item-1",
        "text": "answer",
    })

    assert event.item_id == "item-1"


def test_text_done_preserves_authoritative_final_text() -> None:
    event = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-1",
        "final_text": "complete answer",
    })

    assert event.final_text == "complete answer"


def test_text_done_preserves_authoritative_whitespace_and_empty_text() -> None:
    whitespace = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-1",
        "final_text": "  complete\n",
    })
    empty = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-2",
        "final_text": "",
    })

    assert whitespace.final_text == "  complete\n"
    assert empty.final_text == ""


def test_retrying_can_name_the_item_it_supersedes() -> None:
    event = parse_stream_event({
        "type": "turn.retrying",
        "round": 1,
        "attempt": 2,
        "max_attempts": 3,
        "retry_in_ms": 0,
        "supersedes_item_id": "item-old",
    })

    assert event.supersedes_item_id == "item-old"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("proto", "stream.v2"),
        ("cid", ""),
        ("sid", ""),
        ("turn_id", ""),
        ("event_seq", 0),
        ("presentation_epoch", 0),
    ),
)
def test_stream_event_rejects_invalid_current_envelope(field, value) -> None:
    payload = {
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "type": "text.delta",
        "text": "answer",
    }
    payload[field] = value

    with pytest.raises(ValueError):
        _parse_stream_event(payload)


@pytest.mark.parametrize("field", ("seq", "replace_current_response"))
def test_stream_event_rejects_removed_fields(field) -> None:
    payload = _durable_tool_event()
    payload[field] = 1

    with pytest.raises(ValueError, match="removed protocol field"):
        _parse_stream_event(payload)


@pytest.mark.parametrize("event_seq", [0, -1, True, "41"])
def test_stream_event_rejects_invalid_explicit_event_sequence(event_seq) -> None:
    with pytest.raises(ValueError, match="event_seq"):
        parse_stream_event({
            "type": "text.delta",
            "event_seq": event_seq,
            "text": "answer",
        })


def test_tool_approval_and_output_events_copy_payloads() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "call_id": "call-1",
        "approval_id": "approval-1",
        "kind": "command",
        "environment_id": "workspace-write",
        "started_at_ms": 42,
        "plugin_id": "plugin-1",
        "script_path": "scripts/check.ps1",
        "tty": True,
        "sandbox_permissions": "with_additional_permissions",
        "additional_permissions": {"network": ["example.com"]},
        "proposed_execpolicy_amendment": None,
        "policy_fingerprint": "policy-a",
        "parsed_cmd": [],
        "command": ["pwsh", "-Command", "Get-Date"],
        "cwd": ".",
        "cwd_raw": "C:/workspace",
        "reason": "模型需要运行测试。",
        "available_decisions": ["accept", "decline"],
    })
    output = parse_stream_event({
        "type": "tool.output",
        "name": "remote_tool",
        "call_id": "call-2",
        "status": "completed",
        "result": {"ok": True, "text": "done"},
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.approval_id == "approval-1"
    assert approval.call_id == "call-1"
    assert approval.turn_id == "turn-1"
    assert approval.kind == "command"
    assert approval.environment_id == "workspace-write"
    assert approval.started_at_ms == 42
    assert approval.plugin_id == "plugin-1"
    assert approval.script_path == "scripts/check.ps1"
    assert approval.tty is True
    assert approval.additional_permissions == {"network": ["example.com"]}
    assert approval.policy_fingerprint == "policy-a"
    assert approval.patch_scope == ()
    assert approval.command == ["pwsh", "-Command", "Get-Date"]
    assert approval.cwd == "."
    assert approval.cwd_raw == "C:/workspace"
    assert approval.reason == "模型需要运行测试。"
    assert approval.available_decisions == ("accept", "decline")
    assert isinstance(output, ToolOutputEvent)
    assert output.payload["status"] == "completed"
    assert output.payload["result"] == {"ok": True, "text": "done"}


def test_tool_call_batch_buffer_ignores_completed_batch_replay() -> None:
    start = _parse_stream_event({
        "type": "tool.calls.start",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "presentation_epoch": 1,
        "batch_id": "batch-replay",
        "call_ids": ["call-1"],
        "count": 1,
        "ready": True,
    })
    call = _parse_stream_event({
        "type": "tool.call",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 2,
        "presentation_epoch": 1,
        "call_id": "call-1",
        "name": "test_tool",
        "arguments": {},
        "item_id": "call-1",
        "item_kind": "tool_call",
        "item_status": "waiting_result",
    })
    done = _parse_stream_event({
        "type": "tool.calls.done",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 3,
        "presentation_epoch": 1,
        "batch_id": "batch-replay",
        "call_ids": ["call-1"],
        "count": 1,
    })

    buffer = ToolCallBatchBuffer()
    buffer.begin(start)
    assert buffer.accept(call) == ()
    assert buffer.complete(done) == (call,)
    assert buffer.active is False

    buffer.begin(start)
    assert buffer.active is True
    assert buffer.accept(call) == ()
    assert buffer.complete(done) == ()
    assert buffer.active is False


def test_tool_call_batch_buffer_rejects_call_outside_batch() -> None:
    call = _parse_stream_event({
        "type": "tool.call",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "presentation_epoch": 1,
        "call_id": "call-1",
        "name": "test_tool",
        "arguments": {},
        "item_id": "call-1",
        "item_kind": "tool_call",
        "item_status": "waiting_result",
    })

    with pytest.raises(ValueError, match="without tool.calls.start"):
        ToolCallBatchBuffer().accept(call)


def test_tool_approval_allows_missing_optional_reason() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "call_id": "call-1",
        "kind": "command",
        "command": ["git", "status"],
        "cwd": ".",
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.reason == ""


def test_patch_approval_event_keeps_patch_separate_from_command() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "call_id": "call-patch",
        "approval_id": "approval-patch",
        "kind": "apply_patch",
        "patch": "*** Begin Patch\n*** Update File: app.py\n@@\n-old\n+new\n*** End Patch",
        "files": ["app.py"],
        "cwd": ".",
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.kind == "apply_patch"
    assert approval.patch.startswith("*** Begin Patch")
    assert approval.command == ""


def test_network_approval_event_preserves_policy_proposal() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "call_id": "call-network",
        "approval_id": "approval-network",
        "kind": "network_access",
        "target": "https://api.example.com/v1",
        "host": "api.example.com",
        "protocol": "https",
        "port": 443,
        "command": ["curl", "https://api.example.com/v1"],
        "cwd": "D:/workspace",
        "cwd_raw": ".",
        "proposed_network_policy_amendment": {
            "host": "api.example.com",
            "action": "allow",
        },
        "available_decisions": [
            "accept", "applyNetworkPolicyAmendment", "decline", "cancel"
        ],
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.kind == "network_access"
    assert approval.host == "api.example.com"
    assert approval.protocol == "https"
    assert approval.port == 443
    assert approval.proposed_network_policy_amendment == {
        "host": "api.example.com",
        "action": "allow",
    }


def test_permissions_and_mcp_approval_events_are_strictly_typed() -> None:
    permissions = parse_stream_event({
        "type": "tool.approval_required",
        "call_id": "call-permissions",
        "approval_id": "approval-permissions",
        "kind": "request_permissions",
        "cwd": "D:/workspace",
        "permissions": {"network": {"enabled": True}},
        "available_decisions": [
            "grantForTurn", "grantForTurnWithStrictAutoReview",
            "grantForSession", "decline", "cancel",
        ],
    })
    mcp = parse_stream_event({
        "type": "tool.approval_required",
        "call_id": "call-mcp",
        "approval_id": "approval-mcp",
        "kind": "mcp_tool_call",
        "server": "github",
        "tool_name": "create_issue",
        "arguments": {"title": "Bug"},
        "mcp_request_id": "mcp-request-1",
        "connector_id": "connector-github",
        "connector_name": "GitHub",
        "connector_description": "Repository service.",
        "connected_account_email": "user@example.com",
        "tool_title": "Create issue",
        "tool_description": "Create an issue.",
        "annotations": {
            "destructive_hint": False,
            "open_world_hint": True,
            "read_only_hint": False,
        },
        "available_decisions": ["accept", "decline", "cancel"],
    })

    assert isinstance(permissions, ToolApprovalRequiredEvent)
    assert permissions.permissions == {"network": {"enabled": True}}
    assert isinstance(mcp, ToolApprovalRequiredEvent)
    assert mcp.server == "github"
    assert mcp.arguments == {"title": "Bug"}
    assert mcp.annotations == {
        "destructive_hint": False,
        "open_world_hint": True,
        "read_only_hint": False,
    }


def test_mcp_approval_accepts_null_json_arguments() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "call_id": "call-mcp-null",
        "approval_id": "approval-mcp-null",
        "kind": "mcp_tool_call",
        "arguments": None,
        "server": "github",
        "tool_name": "list_repositories",
        "mcp_request_id": "mcp-request-null",
        "available_decisions": ["accept", "decline", "cancel"],
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.arguments is None


def test_mcp_approval_rejects_policy_extension() -> None:
    with pytest.raises(ValueError, match="unsupported tool approval decision"):
        parse_stream_event({
            "type": "tool.approval_required",
            "call_id": "call-mcp",
            "approval_id": "approval-mcp",
            "kind": "mcp_tool_call",
            "server": "github",
            "tool_name": "create_issue",
            "arguments": {},
            "mcp_request_id": "mcp-request-1",
            "available_decisions": ["acceptWithMcpPolicyAmendment"],
        })


def test_tool_approval_requires_available_decisions() -> None:
    with pytest.raises(ValueError, match="available_decisions"):
        _parse_stream_event({
            "type": "tool.approval_required",
            "proto": "mind.chat",
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_test",
            "event_seq": 1,
            "presentation_epoch": 1,
            "call_id": "call-1",
            "approval_id": "approval-1",
            "kind": "command",
            "started_at_ms": 0,
            "status": "pending",
            "ack": None,
            "environment_id": "workspace-write",
            "command": ["pytest", "-q"],
            "cwd": ".",
            "cwd_raw": ".",
            "reason": "模型需要运行测试。",
            "tty": False,
            "sandbox_permissions": "use_default",
            "additional_permissions": None,
            "proposed_execpolicy_amendment": None,
            "parsed_cmd": [],
        })


def test_turn_control_events_preserve_stable_input_identity() -> None:
    accepted = parse_stream_event({
        "type": "turn.input.accepted",
        "turn_id": "turn_1",
        "client_message_id": "message_1",
    })
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_1",
        "next_input": {
            "client_message_id": "message_2",
            "text": "continue with this",
            "attachments": [{"kind": "image"}],
            "extras": {"source": "steer"},
        },
    })
    done = parse_stream_event({
        "type": "turn.done",
        "turn_id": "turn_1",
        "status": "interrupted",
    })

    assert isinstance(accepted, TurnInputAcceptedEvent)
    assert accepted.turn_id == "turn_1"
    assert accepted.client_message_id == "message_1"
    assert isinstance(settled, TurnLogicalSettledEvent)
    assert settled.next_input is not None
    assert settled.next_input.client_message_id == "message_2"
    assert settled.next_input.attachments == ({"kind": "image"},)
    assert isinstance(done, TurnDoneEvent)
    assert done.status == "interrupted"


def test_turn_terminal_events_preserve_response_metadata() -> None:
    usage = {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache": {"read_tokens": 3},
    }
    done = parse_stream_event({
        "type": "turn.done",
        "status": "incomplete",
        "reason": "max_output_tokens",
        "can_continue": True,
        "response_id": "msg_1",
        "model": "claude-test",
        "route": "messages",
        "request_id": "req_1",
        "service_tier": "standard",
        "usage": usage,
        "stop_reason": "max_tokens",
        "stop_sequence": None,
    })
    failed = parse_stream_event({
        "type": "turn.failed",
        "status": "failed",
        "error": {"message": "pause_turn is not supported"},
        "route": "messages",
        "usage": {"output_tokens": 2},
        "stop_reason": "pause_turn",
    })
    usage["cache"]["read_tokens"] = 99

    assert isinstance(done, TurnDoneEvent)
    assert done.status == "incomplete"
    assert done.reason == "max_output_tokens"
    assert done.can_continue is True
    assert done.response_id == "msg_1"
    assert done.model == "claude-test"
    assert done.route == "messages"
    assert done.request_id == "req_1"
    assert done.service_tier == "standard"
    assert done.usage["cache"] == {"read_tokens": 3}
    assert done.stop_reason == "max_tokens"
    assert done.stop_sequence is None
    assert isinstance(failed, TurnFailedEvent)
    assert failed.status == "failed"
    assert failed.error == "pause_turn is not supported"
    assert failed.error_type == ""
    assert failed.stop_reason == "pause_turn"
    assert failed.usage == {"output_tokens": 2}


def test_failed_event_preserves_provider_error_metadata() -> None:
    failed = parse_stream_event({
        "type": "turn.failed",
        "status": "failed",
        "error_type": "provider_error",
        "error": {
            "type": "provider_error",
            "source": "provider",
            "retryable": False,
            "message": "content rejected",
        },
        "status_code": 422,
    })

    assert isinstance(failed, TurnFailedEvent)
    assert failed.error == "content rejected"
    assert failed.error_type == "provider_error"
    assert failed.error_source == "provider"
    assert failed.status_code == 422
    assert failed.retryable is False


def test_logical_settlement_accepts_null_next_input() -> None:
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_1",
        "next_input": None,
    })

    assert isinstance(settled, TurnLogicalSettledEvent)
    assert settled.next_input is None


@pytest.mark.parametrize("payload", ({}, {"type": ""}))
def test_stream_event_requires_type(payload) -> None:
    with pytest.raises(ValueError, match="type is required"):
        parse_stream_event(payload)


def test_stream_event_requires_object() -> None:
    with pytest.raises(TypeError, match="must be an object"):
        _parse_stream_event([])
