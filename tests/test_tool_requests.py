# -*- coding: utf-8 -*-

import httpx
import pytest

from mind_nova.requests import tools
from mind_nova.tool_approval import ToolApprovalAck


def _install_client(monkeypatch, response, captured) -> None:
    class ClientStub:
        def __init__(self, *, timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

    monkeypatch.setattr(tools.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(
        tools.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(
        tools.Channel,
        "make_headers",
        lambda: {"authorization": "test"},
    )


def _response(status_code, body):
    return httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("POST", "https://example.test/tool-approval"),
    )


def _install_snapshot_client(monkeypatch, response, captured) -> None:
    class ClientStub:
        def __init__(self, *, timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

    monkeypatch.setattr(tools.httpx, "AsyncClient", ClientStub)
    monkeypatch.setattr(
        tools.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(
        tools.Channel,
        "make_headers",
        lambda: {"authorization": "test"},
    )


@pytest.mark.anyio
async def test_approval_snapshot_request_parses_pending_record(monkeypatch) -> None:
    captured = {}
    _install_snapshot_client(monkeypatch, httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "cid": "cid_1",
                "sid": "sid_1",
                "turn_id": "turn_001",
                "turn_status": "waiting_approval",
                "turn_settled": False,
                "last_event_seq": 8,
                "approvals": [{
                    "type": "tool.approval_required",
                    "approval_id": "approval_1",
                    "turn_id": "turn_001",
                    "call_id": "call_1",
                    "kind": "command",
                    "started_at_ms": 42,
                    "environment_id": "workspace-write",
                    "command": ["echo", "ready"],
                    "cwd": ".",
                    "cwd_raw": ".",
                    "reason": "需要执行命令",
                    "tty": False,
                    "sandbox_permissions": "use_default",
                    "additional_permissions": None,
                    "proposed_execpolicy_amendment": None,
                    "parsed_cmd": [],
                    "available_decisions": ["accept", "decline", "cancel"],
                    "status": "pending",
                    "ack": None,
                }],
            },
        },
        request=httpx.Request("POST", "https://example.test/turn/approval-snapshot"),
    ), captured)

    snapshot = await tools.reconcile_tool_approval_snapshot(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        timeout=3.0,
    )

    assert snapshot.cid == "cid_1"
    assert snapshot.last_event_seq == 8
    assert snapshot.approvals[0].status == "pending"
    assert snapshot.approvals[0].approval_id == "approval_1"
    assert captured["url"] == "https://example.test/turn/approval-snapshot"
    assert captured["json"] == {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
    }
    assert captured["timeout"] == 3.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (
            {
                "ok": True,
                "tool": "shell_command",
                "source": "client",
                "args": {"command": "echo ready"},
                "text": "completed",
                "attachments": [],
                "data": {
                    "command": "echo ready",
                    "exit_code": 0,
                    "stdout": "ready\n",
                    "target": "local",
                },
            },
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {"command": "echo ready"},
                "text": "completed",
                "attachments": [],
                "data": {
                    "command": "echo ready",
                    "exit_code": 0,
                    "stdout": "ready\n",
                    "target": "local",
                },
            },
        ),
        (
            {
                "ok": True,
                "tool": "device_snapshot",
                "args": {},
                "text": "snapshot ready",
                "attachments": [{"kind": "file", "path": "snapshot.json"}],
                "data": {
                    "serial": "device-1",
                    "battery": 80,
                    "target": "device-1",
                },
            },
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {},
                "text": "snapshot ready",
                "attachments": [{"kind": "file", "path": "snapshot.json"}],
                "data": {
                    "serial": "device-1",
                    "battery": 80,
                    "target": "device-1",
                },
            },
        ),
        (
            {"answer": 42, "source_url": "https://example.test/docs"},
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {},
                "text": "",
                "attachments": [],
                "data": {
                    "answer": 42,
                    "source_url": "https://example.test/docs",
                },
            },
        ),
        (
            {"approval_denied": True, "error": "approval rejected"},
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {},
                "text": "approval rejected",
                "attachments": [],
                "data": {
                    "approval_denied": True,
                    "error": "approval rejected",
                },
            },
        ),
        (
            "plain output",
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {},
                "text": "plain output",
                "attachments": [],
                "data": {"value": "plain output"},
            },
        ),
    ),
)
async def test_tool_result_posts_only_transport_fields(
    monkeypatch,
    result,
    expected,
) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "data": {
            "status": "matched",
            "delivered": True,
            "already_received": False,
            "request_id": "tool_result_request_1",
        },
    }), captured)

    await tools.post_tool_result(
        "cid_1",
        "sid_1",
        "call_1",
        "test_tool",
        True,
        result,
        additional_context=(" inspect policy ", "verify output"),
        request_id="tool_result_request_1",
    )

    assert captured["url"] == "https://example.test/tool-result"
    assert captured["json"] == {
        "request_id": "tool_result_request_1",
        "cid": "cid_1",
        "sid": "sid_1",
        "call_id": "call_1",
        "name": "test_tool",
        "ok": True,
        "result": expected,
        "additional_context": ["inspect policy", "verify output"],
    }


@pytest.mark.anyio
async def test_tool_result_uses_outer_failure_status(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "data": {
            "status": "matched",
            "delivered": True,
            "already_received": False,
            "request_id": "tool_result_9ddb978992517da2c728b8cf787020552b6f47fc",
        },
    }), captured)

    await tools.post_tool_result(
        "cid_1",
        "sid_1",
        "call_1",
        "test_tool",
        False,
        {
            "ok": True,
            "text": "failed",
            "attachments": [],
            "data": {"error": "failed"},
        },
    )

    assert captured["json"]["ok"] is False
    assert captured["json"]["result"] == {
        "ok": False,
        "tool": "test_tool",
        "source": "client",
        "args": {},
        "text": "failed",
        "attachments": [],
        "data": {"error": "failed"},
    }


@pytest.mark.anyio
async def test_tool_result_rejects_invalid_request_id() -> None:
    with pytest.raises(ValueError, match="8-160 ASCII"):
        await tools.post_tool_result(
            "cid_1",
            "sid_1",
            "call_1",
            "test_tool",
            True,
            "ready",
            request_id="invalid request id",
        )


@pytest.mark.anyio
async def test_tool_result_uses_current_call_arguments(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "data": {
            "status": "matched",
            "delivered": True,
            "already_received": False,
            "request_id": "tool_result_9ddb978992517da2c728b8cf787020552b6f47fc",
        },
    }), captured)

    await tools.post_tool_result(
        "cid_1",
        "sid_1",
        "call_1",
        "js_repl",
        True,
        {
            "ok": True,
            "tool": "js_repl",
            "args": {"code": "stale"},
            "text": "42",
            "attachments": [],
            "data": {"output": "42"},
            "target": "native_coding",
        },
        arguments={"code": "6 * 7"},
    )

    assert captured["json"]["result"] == {
        "ok": True,
        "tool": "js_repl",
        "source": "client",
        "args": {"code": "6 * 7"},
        "text": "42",
        "attachments": [],
        "data": {"output": "42", "target": "native_coding"},
    }


@pytest.mark.anyio
async def test_amendment_approval_posts_id_and_parses_ack(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "approval_request_1",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_1",
            "call_id": "call_1",
            "turn_id": "turn_001",
            "kind": "command",
            "status": "resolved",
            "ack": {
                "kind": "command",
                "request_id": "approval_request_1",
                "decision": "acceptWithExecpolicyAmendment",
                "tool_status": "approved",
                "turn_status": "active",
                "additional_context": [],
                "reason": "",
                "execpolicy_amendment_id": "amendment_1",
            },
            "environment_id": "workspace-write",
            "command": ["echo", "ready"],
            "cwd": ".",
            "cwd_raw": ".",
            "reason": "",
            "tty": False,
            "sandbox_permissions": "use_default",
            "additional_permissions": None,
            "proposed_execpolicy_amendment": {
                "command": ["echo"],
            },
            "parsed_cmd": [],
            "available_decisions": [
                "accept", "acceptForSession",
                "acceptWithExecpolicyAmendment", "decline", "cancel",
            ],
            "started_at_ms": 42,
        },
    }), captured)

    ack = await tools.post_tool_approval(
        "cid_1",
        "sid_1",
        "call_1",
        "approval_1",
        "acceptWithExecpolicyAmendment",
        turn_id="turn_001",
        request_id="approval_request_1",
        execpolicy_amendment_id="amendment_1",
        timeout=4.0,
    )

    assert ack == ToolApprovalAck(
        request_id="approval_request_1",
        turn_id="turn_001",
        approval_id="approval_1",
        call_id="call_1",
        decision="acceptWithExecpolicyAmendment",
        tool_status="approved",
        turn_status="active",
    )
    assert captured["url"] == "https://example.test/tool-approval"
    assert captured["headers"] == {"authorization": "test"}
    assert captured["timeout"] == 4.0
    assert captured["json"] == {
        "request_id": "approval_request_1",
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
        "call_id": "call_1",
        "approval_id": "approval_1",
        "kind": "command",
        "decision": "acceptWithExecpolicyAmendment",
        "execpolicy_amendment_id": "amendment_1",
    }


@pytest.mark.anyio
async def test_network_approval_posts_action_fields_and_nested_ack(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "request_network_1",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_network_1",
            "call_id": "call_network_1",
            "turn_id": "turn_001",
            "kind": "network_access",
            "started_at_ms": 42,
            "environment_id": "workspace-write",
            "target": "https://api.example.com/v1",
            "host": "api.example.com",
            "protocol": "https",
            "port": 443,
            "command": ["curl", "https://api.example.com/v1"],
            "cwd": "D:/workspace",
            "cwd_raw": ".",
            "reason": "",
            "proposed_network_policy_amendment": {
                "host": "api.example.com",
                "action": "allow",
            },
            "available_decisions": [
                "accept", "applyNetworkPolicyAmendment", "decline", "cancel"
            ],
            "status": "resolved",
            "ack": {
                "kind": "network_access",
                "request_id": "request_network_1",
                "decision": "applyNetworkPolicyAmendment",
                "tool_status": "approved",
                "turn_status": "active",
                "additional_context": [],
                "target": "https://api.example.com/v1",
                "host": "api.example.com",
                "protocol": "https",
                "port": 443,
                "network_policy_amendment": {
                    "host": "api.example.com",
                    "action": "allow",
                },
                "reason": "",
            },
        },
    }), captured)

    permission_ack = await tools.post_tool_approval(
        "cid_1", "sid_1", "call_network_1", "approval_network_1",
        "applyNetworkPolicyAmendment",
        turn_id="turn_001",
        kind="network_access",
        approval={
            "target": "https://api.example.com/v1",
            "host": "api.example.com",
            "protocol": "https",
            "port": 443,
            "proposed_network_policy_amendment": {
                "host": "api.example.com",
                "action": "allow",
            },
        },
        request_id="request_network_1",
    )

    assert captured["json"]["kind"] == "network_access"
    assert captured["json"]["target"] == "https://api.example.com/v1"
    assert captured["json"]["network_policy_amendment"] == {
        "host": "api.example.com",
        "action": "allow",
    }


@pytest.mark.anyio
async def test_permission_and_mcp_approvals_post_action_fields(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "request_permission_1",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_permission_1",
            "call_id": "call_permission_1",
            "turn_id": "turn_001",
            "kind": "request_permissions",
            "started_at_ms": 42,
            "environment_id": None,
            "cwd": None,
            "reason": "",
            "permissions": {"network": {"enabled": True}},
            "available_decisions": [
                "grantForTurn", "grantForTurnWithStrictAutoReview",
                "grantForSession", "decline", "cancel",
            ],
            "status": "resolved",
            "ack": {
                "kind": "request_permissions",
                "request_id": "request_permission_1",
                "decision": "grantForTurnWithStrictAutoReview",
                "tool_status": "approved",
                "turn_status": "active",
                "additional_context": [],
                "scope": "turn",
                "permissions": {"network": {"enabled": True}},
                "strict_auto_review": True,
                "reason": "",
            },
        },
    }), captured)

    permission_ack = await tools.post_tool_approval(
        "cid_1", "sid_1", "call_permission_1", "approval_permission_1",
        "grantForTurnWithStrictAutoReview",
        turn_id="turn_001",
        kind="request_permissions",
        approval={"permissions": {"network": {"enabled": True}}},
        request_id="request_permission_1",
    )
    assert captured["json"]["scope"] == "turn"
    assert captured["json"]["strict_auto_review"] is True
    assert permission_ack.scope == "turn"
    assert permission_ack.permissions == {"network": {"enabled": True}}
    assert permission_ack.strict_auto_review is True

    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "request_mcp_1",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_mcp_1",
            "call_id": "call_mcp_1",
            "turn_id": "turn_001",
            "kind": "mcp_tool_call",
            "started_at_ms": 42,
            "server": "github",
            "tool_name": "create_issue",
            "arguments": {"title": "Bug"},
            "mcp_request_id": "mcp-request-1",
            "reason": "",
            "available_decisions": ["accept", "decline", "cancel"],
            "status": "resolved",
            "ack": {
                "kind": "mcp_tool_call",
                "request_id": "request_mcp_1",
                "decision": "accept",
                "tool_status": "approved",
                "turn_status": "active",
                "additional_context": [],
                "server": "github",
                "tool_name": "create_issue",
                "arguments": {"title": "Bug"},
                "mcp_request_id": "mcp-request-1",
                "reason": "",
            },
        },
    }), captured)
    await tools.post_tool_approval(
        "cid_1", "sid_1", "call_mcp_1", "approval_mcp_1", "accept",
        turn_id="turn_001",
        kind="mcp_tool_call",
        approval={
            "server": "github",
            "tool_name": "create_issue",
            "arguments": {"title": "Bug"},
            "mcp_request_id": "mcp-request-1",
        },
        request_id="request_mcp_1",
    )
    assert captured["json"]["kind"] == "mcp_tool_call"
    assert captured["json"]["mcp_request_id"] == "mcp-request-1"


@pytest.mark.anyio
async def test_mcp_duplicate_receipt_keeps_current_request_id_and_json_arguments(
    monkeypatch,
) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "request_mcp_duplicate_1",
        "status": "duplicate",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_mcp_duplicate",
            "call_id": "call_mcp_duplicate",
            "turn_id": "turn_001",
            "kind": "mcp_tool_call",
            "started_at_ms": 42,
            "server": "github",
            "tool_name": "list_repositories",
            "arguments": None,
            "mcp_request_id": "mcp-request-duplicate",
            "reason": "",
            "available_decisions": ["accept", "decline", "cancel"],
            "status": "resolved",
            "ack": {
                "kind": "mcp_tool_call",
                "request_id": "request_mcp_duplicate_1",
                "decision": "accept",
                "tool_status": "approved",
                "turn_status": "active",
                "additional_context": [],
                "server": "github",
                "tool_name": "list_repositories",
                "arguments": None,
                "mcp_request_id": "mcp-request-duplicate",
                "reason": "",
            },
        },
    }), captured)

    ack = await tools.post_tool_approval(
        "cid_1", "sid_1", "call_mcp_duplicate", "approval_mcp_duplicate", "accept",
        turn_id="turn_001",
        kind="mcp_tool_call",
        approval={
            "server": "github",
            "tool_name": "list_repositories",
            "arguments": None,
            "mcp_request_id": "mcp-request-duplicate",
        },
        request_id="request_mcp_duplicate_1",
    )

    assert ack.request_id == "request_mcp_duplicate_1"
    assert ack.arguments is None
    assert captured["json"]["arguments"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kwargs",
    (
        {"decision": "acceptWithExecpolicyAmendment"},
        {"decision": "accept", "execpolicy_amendment_id": "amendment_1"},
        {"decision": "accept", "reason": "not allowed"},
        {"decision": "unknown"},
    ),
)
async def test_approval_request_rejects_invalid_decision_fields(kwargs) -> None:
    with pytest.raises(ValueError):
        await tools.post_tool_approval(
            "cid_1",
            "sid_1",
            "call_1",
            "approval_1",
            turn_id="turn_001",
            **kwargs,
        )


@pytest.mark.anyio
async def test_decline_request_allows_reason(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "approval_request_2",
        "approval": {
            "type": "tool.approval_required",
            "approval_id": "approval_1",
            "call_id": "call_1",
            "turn_id": "turn_001",
            "kind": "command",
            "status": "resolved",
            "ack": {
                "kind": "command",
                "request_id": "approval_request_2",
                "decision": "decline",
                "tool_status": "declined",
                "turn_status": "active",
                "additional_context": ["Use the safe wrapper."],
                "reason": "user denied",
            },
            "environment_id": "workspace-write",
            "command": ["echo", "ready"],
            "cwd": ".",
            "cwd_raw": ".",
            "reason": "user denied",
            "tty": False,
            "sandbox_permissions": "use_default",
            "additional_permissions": None,
            "proposed_execpolicy_amendment": None,
            "parsed_cmd": [],
            "available_decisions": ["accept", "decline", "cancel"],
            "started_at_ms": 42,
        },
    }), captured)

    await tools.post_tool_approval(
        "cid_1",
        "sid_1",
        "call_1",
        "approval_1",
        "decline",
        turn_id="turn_001",
        request_id="approval_request_2",
        reason="user denied",
        additional_context=("Use the safe wrapper.",),
    )

    assert captured["json"]["reason"] == "user denied"
    assert captured["json"]["additional_context"] == ["Use the safe wrapper."]
    assert "execpolicy_amendment_id" not in captured["json"]


@pytest.mark.anyio
async def test_not_pending_response_raises_request_error(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(404, {
        "detail": {
            "code": "approval_not_pending",
            "message": "approval expired",
        },
    }), captured)

    with pytest.raises(tools.ToolApprovalRequestError, match="^approval expired$"):
        await tools.post_tool_approval(
            "cid_1",
            "sid_1",
            "call_1",
            "approval_1",
            "decline",
            turn_id="turn_001",
        )


@pytest.mark.anyio
async def test_conflict_response_preserves_stable_error_code(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(409, {
        "detail": {
            "code": "approval_decision_conflict",
            "message": "decision conflicts with prior response",
        },
    }), captured)

    with pytest.raises(tools.ToolApprovalRequestError) as caught:
        await tools.post_tool_approval(
            "cid_1",
            "sid_1",
            "call_1",
            "approval_1",
            "decline",
            turn_id="turn_001",
        )

    assert caught.value.code == "approval_decision_conflict"
    assert caught.value.status_code == 409


@pytest.mark.anyio
async def test_approval_ack_rejects_mismatched_coordinates(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "approval_request_3",
        "approval": {
            "type": "tool.approval_required",
            "turn_id": "turn_other",
            "approval_id": "approval_1",
            "call_id": "call_1",
            "kind": "command",
            "status": "resolved",
            "ack": {
                "kind": "command",
                "request_id": "approval_request_3",
                "decision": "decline",
                "tool_status": "declined",
                "turn_status": "active",
                "additional_context": [],
            },
        },
    }), captured)

    with pytest.raises(tools.ToolApprovalRequestError) as caught:
        await tools.post_tool_approval(
            "cid_1",
            "sid_1",
            "call_1",
            "approval_1",
            "decline",
            turn_id="turn_001",
        )

    assert caught.value.code == "approval_ack_mismatch"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_status", "turn_status"),
    [
        ("completed", "active"),
        ("approved", "completed"),
    ],
)
async def test_approval_ack_rejects_invalid_lifecycle_status(
    monkeypatch,
    tool_status,
    turn_status,
) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "approval_request_1",
        "approval": {
            "type": "tool.approval_required",
            "turn_id": "turn_001",
            "approval_id": "approval_1",
            "call_id": "call_1",
            "kind": "command",
            "status": "resolved",
            "ack": {
                "kind": "command",
                "request_id": "approval_request_1",
                "decision": "accept",
                "tool_status": tool_status,
                "turn_status": turn_status,
                "additional_context": [],
            },
        },
    }), captured)

    with pytest.raises(tools.ToolApprovalRequestError) as caught:
        await tools.post_tool_approval(
            "cid_1",
            "sid_1",
            "call_1",
            "approval_1",
            "accept",
            turn_id="turn_001",
            request_id="approval_request_1",
        )

    assert caught.value.code == "approval_ack_invalid"
