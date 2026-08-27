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
                    "approval_id": "approval_1",
                    "turn_id": "turn_001",
                    "call_id": "call_1",
                    "name": "exec_command",
                    "arguments": {"command": "echo ready", "cwd": "."},
                    "approval": {
                        "type": "tool.approval_required",
                        "kind": "command",
                        "command": "echo ready",
                        "cwd": ".",
                        "available_decisions": ["accept", "decline"],
                    },
                    "status": "pending",
                    "decision": "",
                    "execpolicy_amendment_id": "",
                    "reason": "需要执行命令",
                    "additional_context": [],
                    "ack": None,
                    "expires_at": 100.0,
                    "resolved_at": None,
                    "created_at": 90.0,
                    "updated_at": 90.0,
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
                },
                "target": "local",
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
                },
                "target": "local",
            },
        ),
        (
            {
                "ok": True,
                "tool": "device_snapshot",
                "args": {},
                "text": "snapshot ready",
                "attachments": [{"kind": "file", "path": "snapshot.json"}],
                "data": {"serial": "device-1", "battery": 80},
                "target": "device-1",
            },
            {
                "ok": True,
                "tool": "test_tool",
                "source": "client",
                "args": {},
                "text": "snapshot ready",
                "attachments": [{"kind": "file", "path": "snapshot.json"}],
                "data": {"serial": "device-1", "battery": 80},
                "target": "device-1",
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
    _install_client(monkeypatch, _response(200, {"ok": True}), captured)

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
    _install_client(monkeypatch, _response(200, {"ok": True}), captured)

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
    _install_client(monkeypatch, _response(200, {"ok": True}), captured)

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
        "data": {"output": "42"},
        "target": "native_coding",
    }


@pytest.mark.anyio
async def test_amendment_approval_posts_id_and_parses_ack(monkeypatch) -> None:
    captured = {}
    _install_client(monkeypatch, _response(200, {
        "ok": True,
        "request_id": "approval_request_1",
        "turn_id": "turn_001",
        "approval_id": "approval_1",
        "call_id": "call_1",
        "decision": "acceptWithExecpolicyAmendment",
        "tool_status": "approved",
        "turn_status": "active",
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
        "decision": "acceptWithExecpolicyAmendment",
        "execpolicy_amendment_id": "amendment_1",
    }


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
        "turn_id": "turn_001",
        "approval_id": "approval_1",
        "call_id": "call_1",
        "decision": "decline",
        "tool_status": "declined",
        "turn_status": "active",
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
        "turn_id": "turn_other",
        "approval_id": "approval_1",
        "call_id": "call_1",
        "decision": "decline",
        "tool_status": "declined",
        "turn_status": "active",
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
        "turn_id": "turn_001",
        "approval_id": "approval_1",
        "call_id": "call_1",
        "decision": "accept",
        "tool_status": tool_status,
        "turn_status": turn_status,
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
