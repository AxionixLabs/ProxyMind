# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from mind_app.approval.policy import (
    ApprovalStore,
    approval_decisions,
    approval_decision_label,
    approval_prompt,
    approval_show_timer,
    validate_tool_approval,
)
from mind_app.client_tools.coding.native import (
    coding_tools,
    validate_unsandboxed_process_authorization,
    validate_workspace_write_authorization,
)
from mind_app.client_tools.types import ClientToolRuntime
from mind_app.native_coding.execution_authorization import ExecutionAuthorizationError
from mind_core.permissions import (
    PermissionSettings,
    permission_label,
    preset_permissions,
    resolve_permissions,
)
from mind_nova import const
from mind_nova.requests.payload import build_chat_payload


@pytest.mark.parametrize(
    ("preset", "settings", "label"),
    [
        ("read-only", PermissionSettings("read-only", "on-request"), "Read Only"),
        ("auto", PermissionSettings("workspace-write", "on-request"), "Auto"),
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


@pytest.mark.anyio
async def test_request_payload_uses_sandbox_and_approval_fields() -> None:
    payload = await build_chat_payload(
        "chat",
        {},
        "inspect",
        [],
        permissions=preset_permissions("auto"),
    )

    assert payload["sandbox_mode"] == "workspace-write"
    assert payload["approval_policy"] == "on-request"
    assert "access_mode" not in payload


def test_never_policy_rejects_approval_required_tool_call() -> None:
    decision = validate_tool_approval(
        event={"call_id": "call-1", "approval_required": True},
        name="shell_command",
        arguments={"command": "pytest -q"},
        store=ApprovalStore(),
        approval_policy="never",
    )

    assert decision.action == "reject"
    assert decision.result["error"] == "approval disabled by approval policy"


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", ["shell_command", "exec_command", "apply_patch"])
async def test_read_only_sandbox_rejects_local_mutating_capabilities(
    tool_name,
) -> None:
    tool = next(tool for tool in coding_tools() if tool.name == tool_name)
    runtime = ClientToolRuntime(
        session=SimpleNamespace(),
        permissions=preset_permissions("read-only"),
    )

    result = await tool.handler({}, runtime)

    assert result.isError is True
    assert result.structuredContent["data"]["reason"] == "sandbox_read_only"


@pytest.mark.parametrize(
    ("permissions", "state", "reasons", "error_reason"),
    [
        (
            PermissionSettings("workspace-write", "on-request"),
            "allowed",
            [],
            "unsandboxed_process_approval_required",
        ),
        (
            PermissionSettings("workspace-write", "never"),
            "allowed",
            [],
            "unsandboxed_process_approval_required",
        ),
        (
            PermissionSettings("danger-full-access", "untrusted"),
            "allowed",
            [],
            "untrusted_process_approval_required",
        ),
    ],
)
def test_unsandboxed_process_rejects_unapproved_restricted_modes(
    permissions,
    state,
    reasons,
    error_reason,
) -> None:
    runtime = ClientToolRuntime(
        session=SimpleNamespace(),
        permissions=permissions,
        execution={"state": state, "reasons": reasons},
    )

    with pytest.raises(ExecutionAuthorizationError) as exc_info:
        validate_unsandboxed_process_authorization(runtime)

    assert exc_info.value.reason == error_reason


@pytest.mark.parametrize(
    ("permissions", "state", "reasons"),
    [
        (PermissionSettings("workspace-write", "on-request"), "approved", []),
        (
            PermissionSettings("workspace-write", "on-request"),
            "allowed",
            ["session_approval_matched"],
        ),
        (PermissionSettings("danger-full-access", "never"), "allowed", []),
        (PermissionSettings("danger-full-access", "on-request"), "allowed", []),
    ],
)
def test_unsandboxed_process_accepts_authorized_modes(
    permissions,
    state,
    reasons,
) -> None:
    runtime = ClientToolRuntime(
        session=SimpleNamespace(),
        permissions=permissions,
        execution={"state": state, "reasons": reasons},
    )

    validate_unsandboxed_process_authorization(runtime)


def test_approval_card_presentation_is_owned_by_client() -> None:
    approval = {
        "tool": "shell_command",
        "prompt": "server prompt",
        "show_timer": False,
        "decision_labels": {"accept": "server label"},
    }

    assert approval_prompt(approval) == "Would you like to approve the following command?"
    assert approval_show_timer() is True
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


def test_workspace_write_requires_permission_context() -> None:
    runtime = ClientToolRuntime(session=SimpleNamespace())

    with pytest.raises(ExecutionAuthorizationError) as exc_info:
        validate_workspace_write_authorization(runtime)

    assert exc_info.value.reason == "execution_permissions_required"
