# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import pytest

from mind import create_native_coding
from agent.application.tools.context import ToolHandlerContext
from agent.application.approvals.policy import (
    approval_decisions,
    approval_decision_label,
    approval_from_event,
    approval_from_snapshot,
    approval_prompt,
    approval_reason,
)
from mind_app.runtime.turns.stream_policy import (
    apply_local_patch_approval,
    local_patch_approval,
)
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from mind_app.client_tools.coding.native import (
    coding_tools,
)
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.application.hooks.models import HookDecision
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from agent.domain.policies import (
    PermissionSettings,
    permission_label,
    preset_permissions,
    resolve_permissions,
)
from metadata import const
from protocol.client.payload import build_chat_payload
from protocol.schema.stream_events import (
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    parse_stream_event,
)


def test_approval_from_snapshot_matches_event_shape() -> None:
    approval = approval_from_snapshot({
        "type": "tool.approval_required",
        "approval_id": "approval_1",
        "turn_id": "turn_1",
        "call_id": "call_1",
        "kind": "apply_patch",
        "environment_id": "workspace-write",
        "patch": "*** Update File: a.txt\n+ok\n",
        "files": ["a.txt"],
        "cwd": ".",
        "cwd_raw": ".",
        "permissions_preapproved": False,
        "available_decisions": ["accept", "decline", "cancel"],
        "status": "pending",
        "ack": None,
        "reason": "需要修改文件",
    })

    assert approval["id"] == "approval_1"
    assert approval["tool"] == "apply_patch"
    assert approval["arguments"] == {
        "patch": "*** Update File: a.txt\n+ok\n",
        "cwd": str(Path(".").resolve()),
    }
    assert approval["reason"] == "需要修改文件"
    assert "justification" not in approval


def _client_runtime(
    permissions: PermissionSettings,
) -> ToolHandlerContext:
    turn_context = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={},
        cwd=".",
        permissions=permissions,
    )
    return ToolHandlerContext(
        session=SimpleNamespace(),
        turn_context=turn_context,
        pref_config={},
        call_id="call_test",
    )


def _coding_stub() -> SimpleNamespace:
    result = {
        "ok": True,
        "text": "done",
        "attachments": [],
        "data": {},
        "logs": [],
    }
    return SimpleNamespace(
        root=".",
        agent_id="root",
        shell_command=AsyncMock(return_value=result),
        exec_command=AsyncMock(return_value=result),
        write_stdin=AsyncMock(return_value=result),
    )


@pytest.mark.parametrize(
    ("preset", "settings", "label"),
    [
        ("read-only", PermissionSettings("read-only", "on-request"), "Read Only"),
        (
            "auto",
            PermissionSettings("workspace-write", "on-request"),
            "Ask for approval",
        ),
        (
            "full-access",
            PermissionSettings("danger-full-access", "never"),
            "Full Access",
        ),
    ],
)
def test_permission_presets_map_both_policy_axes(
    preset,
    settings,
    label,
) -> None:
    actual = preset_permissions(preset)

    assert actual == settings
    assert actual.preset == preset
    assert permission_label(actual) == label


def test_permission_defaults_follow_entry_type() -> None:
    assert resolve_permissions({}, interactive=True) == (
        PermissionSettings("workspace-write", "on-request")
    )
    assert resolve_permissions({}, interactive=False) == (
        PermissionSettings("read-only", "never")
    )
    assert resolve_permissions(
        {"sandbox_mode": "read-only", "approval_policy": "never"},
        interactive=True,
    ) == PermissionSettings("read-only", "never")
    assert resolve_permissions(
        {"network_access": "enabled"},
        interactive=True,
    ).network_access == "enabled"


@pytest.mark.anyio
async def test_request_payload_uses_sandbox_and_approval_fields() -> None:
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        permissions=preset_permissions("auto"),
    )

    assert payload["sandbox_mode"] == "workspace-write"
    assert payload["approval_policy"] == "on-request"
    assert payload["approvals_reviewer"] == "user"
    assert payload["network_access"] == "restricted"
    assert "access_mode" not in payload
    assert "mode" not in payload


@pytest.mark.anyio
async def test_request_payload_preserves_granular_approval_policy() -> None:
    policy = {
        "granular": {
            "sandbox_approval": True,
            "rules": False,
            "skill_approval": True,
            "request_permissions": True,
            "mcp_elicitations": False,
        },
    }
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        permissions={
            "sandbox_mode": "workspace-write",
            "approval_policy": policy,
            "approvals_reviewer": "user",
            "network_access": "enabled",
        },
    )

    assert payload["approval_policy"] == policy
    assert payload["network_access"] == "enabled"


@pytest.mark.anyio
async def test_request_payload_rejects_invalid_network_access() -> None:
    with pytest.raises(ValueError, match="invalid network access"):
        await build_chat_payload(
            {},
            "inspect",
            [],
            permissions={"network_access": "open"},
        )


@pytest.mark.anyio
async def test_request_payload_enables_only_explicit_hosted_tool_groups() -> None:
    payload = await build_chat_payload(
        {
            "hosted_tools": {
                "groups": {
                    "sandbox_cloud": True,
                    "perf_engine": False,
                    "unknown_group": True,
                },
            },
        },
        "inspect",
        [],
        permissions=preset_permissions("auto"),
    )

    assert payload["hosted_tools"] == {
        "enabled_groups": ["sandbox_cloud"],
    }


@pytest.mark.anyio
async def test_approve_for_me_payload_selects_auto_reviewer() -> None:
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        permissions=PermissionSettings(
            "workspace-write",
            "on-request",
            approvals_reviewer="auto_review",
        ),
    )

    assert payload["approvals_reviewer"] == "auto_review"


@pytest.mark.anyio
async def test_request_payload_rejects_invalid_explicit_turn_id() -> None:
    with pytest.raises(ValueError, match="8-128 ASCII"):
        await build_chat_payload(
            {},
            "inspect",
            [],
            turn_id="invalid id",
        )


@pytest.mark.anyio
async def test_request_payload_normalizes_additional_context() -> None:
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        permissions=preset_permissions("auto"),
        additional_context=[" first ", "", "second"],
    )

    assert payload["additional_context"] == ["first", "second"]


@pytest.mark.anyio
async def test_request_payload_normalizes_system_message() -> None:
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        permissions=preset_permissions("auto"),
        system_message=" keep this focused ",
    )

    assert payload["system_message"] == "keep this focused"


@pytest.mark.anyio
async def test_request_payload_rejects_removed_system_message_alias() -> None:
    with pytest.raises(ValueError, match="unsupported AgentRequest fields: systemMessage"):
        await build_chat_payload(
            {},
            "inspect",
            [],
            permissions=preset_permissions("auto"),
            systemMessage="removed alias",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", ["apply_patch"])
async def test_read_only_sandbox_rejects_local_mutating_capabilities(
    tool_name,
    tmp_path,
) -> None:
    coding = create_native_coding(root=tmp_path, application_layout=None)
    tool = next(tool for tool in coding_tools(coding) if tool.name == tool_name)
    runtime = _client_runtime(preset_permissions("read-only"))

    arguments = {"patch": "*** Begin Patch\n*** End Patch"}
    result = await tool.handler(arguments, runtime)

    assert result.isError is True
    assert result.structuredContent["data"]["reason"] == "sandbox_read_only"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "original", "updated_input", "expected"),
    [
        (
            "shell_command",
            {
                "command": "echo original",
                "cwd": ".",
                "timeout_sec": 60,
                "output_encoding": "auto",
            },
            {"command": "echo rewritten"},
            {
                "command": "echo rewritten",
                "cwd": ".",
                "timeout_sec": 60,
                "output_encoding": "auto",
            },
        ),
        (
            "exec_command",
            {
                "command": "echo original",
                "cwd": ".",
                "yield_time_ms": 1000,
                "max_output_chars": 24000,
                "timeout_sec": 1800,
                "idle_timeout_sec": 300,
            },
            {"command": "echo rewritten"},
            {
                "command": "echo rewritten",
                "cwd": ".",
                "yield_time_ms": 1000,
                "max_output_chars": 24000,
                "timeout_sec": 1800,
                "idle_timeout_sec": 300,
            },
        ),
        (
            "write_stdin",
            {
                "session_id": "exec_test",
                "stdin": "original\n",
                "wait_ms": 1000,
                "max_output_chars": 12000,
                "control": "none",
            },
            {
                "session_id": "exec_test",
                "stdin": "rewritten\n",
                "wait_ms": 1000,
                "max_output_chars": 12000,
                "control": "none",
            },
            {
                "session_id": "exec_test",
                "stdin": "rewritten\n",
                "wait_ms": 1000,
                "max_output_chars": 12000,
                "control": "none",
            },
        ),
    ],
)
async def test_hook_updated_input_reaches_native_shell_handler(
    tool_name,
    original,
    updated_input,
    expected,
) -> None:
    coding = _coding_stub()
    tool = next(item for item in coding_tools(coding) if item.name == tool_name)
    runtime = _client_runtime(preset_permissions("full-access"))
    invocation = ToolInvocation(
        turn=runtime.turn_context,
        call_id="call_test",
        name=tool_name,
        arguments=dict(original),
    )

    effective = ToolCallCoordinator.effective_invocation(
        invocation,
        HookDecision(allowed=True, updated_input=updated_input),
    )
    result = await tool.handler(effective.arguments, runtime)

    assert result.isError is False
    assert result.structuredContent["args"] == expected
    call = getattr(coding, tool_name).await_args
    for key, value in expected.items():
        assert call.kwargs[key] == value


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "canonical", "arguments"),
    [
        (
            "shell_command",
            {
                "command": "echo canonical",
                "cwd": ".",
                "timeout_sec": 60,
                "output_encoding": "auto",
            },
            {"command": "echo untrusted"},
        ),
        (
            "exec_command",
            {
                "command": "echo canonical",
                "cwd": ".",
                "yield_time_ms": 1000,
                "max_output_chars": 24000,
                "timeout_sec": 1800,
                "idle_timeout_sec": 300,
            },
            {"command": "echo untrusted"},
        ),
        (
            "write_stdin",
            {
                "session_id": "exec_test",
                "stdin": "canonical\n",
                "wait_ms": 1000,
                "max_output_chars": 12000,
                "control": "none",
            },
            {"session_id": "exec_test", "stdin": "untrusted\n"},
        ),
    ],
)
async def test_native_shell_handler_accepts_client_arguments_without_remote_grant(
    tool_name,
    canonical,
    arguments,
) -> None:
    coding = _coding_stub()
    tool = next(item for item in coding_tools(coding) if item.name == tool_name)
    runtime = _client_runtime(preset_permissions("full-access"))

    result = await tool.handler(arguments, runtime)

    assert result.isError is False
    assert result.structuredContent["args"] == arguments
    getattr(coding, tool_name).assert_awaited_once()


def test_justification_is_only_exposed_by_process_start_tools() -> None:
    schemas = {
        tool.name: tool.input_schema
        for tool in coding_tools(_coding_stub())
    }

    assert "justification" in schemas["shell_command"]["properties"]
    assert "justification" in schemas["exec_command"]["properties"]
    assert "justification" not in schemas["write_stdin"]["properties"]
    assert "justification" not in schemas["apply_patch"]["properties"]


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", ("shell_command", "exec_command"))
async def test_command_justification_is_not_passed_to_native_executor(
    tool_name,
) -> None:
    coding = _coding_stub()
    tool = next(item for item in coding_tools(coding) if item.name == tool_name)
    runtime = _client_runtime(preset_permissions("full-access"))
    arguments = {
        "command": "echo ready",
        "sandbox_permissions": "require_escalated",
        "justification": "需要使用宿主 shell",
    }

    result = await tool.handler(arguments, runtime)

    assert result.isError is False
    assert result.structuredContent["args"] == {
        "command": "echo ready",
        "sandbox_permissions": "require_escalated",
    }
    native_arguments = getattr(coding, tool_name).await_args.kwargs
    assert "justification" not in native_arguments
    assert arguments["justification"] == "需要使用宿主 shell"


def test_approval_card_presentation_is_owned_by_client() -> None:
    approval = {
        "tool": "shell_command",
        "prompt": "server prompt",
        "show_timer": False,
        "decision_labels": {"accept": "server label"},
    }

    assert approval_prompt(approval) == "Would you like to run the following command?"
    assert approval_decision_label("accept") == "Yes, proceed"
    assert approval_decision_label(
        "acceptForSession"
    ) == "Yes, for this session"
    assert approval_decision_label("decline") == (
        f"No, and tell {const.APP_DESC} what to do differently"
    )
    assert approval_decisions() == [
        "accept", "acceptForSession", "decline"
    ]


def test_approval_uses_amendment_as_second_visible_option() -> None:
    approval = {
        "proposed_execpolicy_amendment": {
            "id": "amendment_1",
            "command_prefix": ["git", "clone"],
            "display": "git clone",
        },
    }

    assert approval_decisions(approval) == [
        "accept", "acceptWithExecpolicyAmendment", "decline"
    ]
    assert approval_decision_label(
        "acceptWithExecpolicyAmendment",
        approval,
    ) == (
        "Yes, and don't ask again for commands that start with `git clone`"
    )


def test_server_available_decisions_are_authoritative() -> None:
    assert approval_decisions({
        "available_decisions": ["accept", "decline"],
    }) == ["accept", "decline"]


def test_server_amendment_option_requires_matching_proposal() -> None:
    assert approval_decisions({
        "available_decisions": [
            "accept",
            "acceptWithExecpolicyAmendment",
            "decline",
        ],
    }) == ["accept", "decline"]


@pytest.mark.parametrize(
    "proposal",
    (
        None,
        {},
        {"id": "amendment_1", "command_prefix": [], "display": "git clone"},
        {"id": "", "command_prefix": ["git", "clone"], "display": "git clone"},
        {"id": "amendment_1", "command_prefix": ["git", "clone"], "display": ""},
    ),
)
def test_invalid_amendment_keeps_session_option(proposal) -> None:
    approval = {"proposed_execpolicy_amendment": proposal}

    assert approval_decisions(approval) == [
        "accept", "acceptForSession", "decline"
    ]


def test_approval_from_event_uses_direct_command_fields() -> None:
    approval = approval_from_event(ToolApprovalRequiredEvent(
        type="tool.approval_required",
        call_id="call-1",
        approval_id="approval-1",
        kind="command",
        command="pytest -q",
        cwd=".",
        reason="需要检查命令输出",
    ))

    assert approval["id"] == "approval-1"
    assert approval["call_id"] == "call-1"
    assert approval["tool"] == "exec_command"
    assert approval["command"] == "pytest -q"
    assert approval["cwd"] == str(Path(".").resolve())
    assert approval["cwd_raw"] == "."
    assert approval["turn_id"] == ""
    assert approval["reason"] == "需要检查命令输出"
    assert "justification" not in approval


def test_approval_from_event_preserves_network_permissions_and_mcp_fields() -> None:
    network = approval_from_event(ToolApprovalRequiredEvent(
        type="tool.approval_required",
        call_id="call-network",
        approval_id="approval-network",
        kind="network_access",
        target="https://api.example.com/v1",
        host="api.example.com",
        protocol="https",
        port=443,
        command=["curl", "https://api.example.com/v1"],
        proposed_network_policy_amendment={
            "host": "api.example.com",
            "action": "allow",
        },
        available_decisions=(
            "accept", "applyNetworkPolicyAmendment", "decline"
        ),
    ))
    mcp = approval_from_event(ToolApprovalRequiredEvent(
        type="tool.approval_required",
        call_id="call-mcp",
        approval_id="approval-mcp",
        kind="mcp_tool_call",
        server="github",
        tool_name="create_issue",
        arguments={"title": "Bug"},
        mcp_request_id="mcp-request-1",
        annotations={"read_only_hint": False},
        available_decisions=("accept", "decline"),
    ))

    assert network["tool"] == "exec_command"
    assert network["host"] == "api.example.com"
    assert network["proposed_network_policy_amendment"]["action"] == "allow"
    assert mcp["tool"] == ""
    assert mcp["server"] == "github"
    assert mcp["arguments"] == {"title": "Bug"}


def test_patch_approval_uses_patch_operation_and_dedicated_prompt(tmp_path) -> None:
    coding = SimpleNamespace(
        preview_patch=lambda **_kwargs: {
            "ok": True,
            "data": {"files": [{"path": "src/app.py"}]},
        }
    )
    runtime = _client_runtime(preset_permissions("read-only"))
    invocation = ToolInvocation(
        turn=runtime.turn_context,
        call_id="call-patch",
        name="apply_patch",
        arguments={"patch": "*** Begin Patch", "cwd": str(tmp_path)},
        reason="需要更新实现",
    )

    approval = local_patch_approval(
        coding.preview_patch,
        invocation,
    )

    assert approval["kind"] == "apply_patch"
    assert approval["tool"] == "apply_patch"
    assert approval["approval_id"] == "local-patch-call-patch"
    assert approval["turn_id"] == runtime.turn_context.turn_id
    assert approval["started_at_ms"] >= 0
    assert approval["patch_scope"] == ["src/app.py"]
    assert approval["available_decisions"] == [
        "accept",
        "acceptForSession",
        "decline",
    ]
    assert approval["reason"] == "需要更新实现"
    assert "justification" not in approval
    assert approval_prompt(approval) == "Would you like to make the following edits?"
    assert approval_decisions(approval) == [
        "accept",
        "acceptForSession",
        "decline",
    ]


def test_patch_session_approval_updates_file_cache(tmp_path) -> None:
    manager = ExecPolicyManager(workspace_root=tmp_path, rules_paths=())
    approval = {
        "patch_scope": ["src/app.py", "src/lib.py"],
        "cwd": str(tmp_path),
        "environment_id": "env-a",
    }

    assert apply_local_patch_approval(
        manager,
        approval=approval,
        decision="acceptForSession",
    ) is None
    assert manager.patch_scope_approved_for_session(
        ["src/app.py"],
        cwd=tmp_path,
        environment_id="env-a",
    ) is True


def test_approval_event_preserves_identity_and_environment_fields() -> None:
    approval = approval_from_event(ToolApprovalRequiredEvent(
        type="tool.approval_required",
        proto="mind.chat",
        cid="conversation-1",
        sid="session-1",
        turn_id="turn-1",
        call_id="call-1",
        approval_id="approval-1",
        environment_id="workspace-write",
        started_at_ms=42,
        plugin_id="plugin-1",
        script_path="scripts/check.ps1",
        tty=True,
        additional_permissions={"network": ["example.com"]},
        policy_fingerprint="policy-a",
        patch_scope=("src/app.py",),
        kind="command",
        command=["pwsh", "-Command", "Get-Date"],
        cwd=".",
        cwd_raw="C:/workspace",
        reason="需要确认执行环境",
    ))

    assert approval["turn_id"] == "turn-1"
    assert approval["environment_id"] == "workspace-write"
    assert approval["started_at_ms"] == 42
    assert approval["plugin_id"] == "plugin-1"
    assert approval["script_path"] == "scripts/check.ps1"
    assert approval["tty"] is True
    assert approval["additional_permissions"] == {"network": ["example.com"]}
    assert approval["policy_fingerprint"] == "policy-a"
    assert approval["patch_scope"] == ["src/app.py"]
    assert approval["cwd"] == str(Path("C:/workspace").resolve())
    assert approval["cwd_raw"] == "C:/workspace"
    assert approval["command"] == ["pwsh", "-Command", "Get-Date"]
    assert approval["arguments"]["command"] == ["pwsh", "-Command", "Get-Date"]


def test_approval_reason_uses_retry_then_approval_then_justification() -> None:
    assert approval_reason({
        "retry_reason": "retry",
        "approval_reason": "policy",
        "justification": "user",
    }) == "retry"
    assert approval_reason({
        "approval_reason": "policy",
        "justification": "user",
    }) == "policy"
    assert approval_reason({"reason": "policy"}) == "policy"
    assert approval_reason({"justification": "user"}) == "user"
