# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.presentation import (
    ApplyPatchApprovalPresentation,
    ExecApprovalPresentation,
    McpApprovalPresentation,
    RequestPermissionsApprovalPresentation,
    build_approval_presentation,
    ensure_approval_presentation,
)
from agent.application.approvals.factory import build_approval_request
from agent.application.views.builders.approval import build_approval_view
from frontends.terminal.renderers.approval import render_approval_view


def test_command_payload_is_normalized_to_exec_presentation() -> None:
    presentation = build_approval_presentation({
        "id": "approval-command",
        "tool": "exec_command",
        "arguments": {
            "command": ["pwsh", "-Command", "Get-Date"],
        },
    })

    assert isinstance(presentation, ExecApprovalPresentation)
    assert presentation.context.kind == "command"
    assert presentation.context.approval_id == "approval-command"
    assert presentation.commands == (("pwsh", "-Command", "Get-Date"),)
    assert presentation.context.prompt == (
        "Would you like to run the following command?"
    )
    assert ensure_approval_presentation(presentation) is presentation


def test_canonical_reason_is_projected_to_ui_justification() -> None:
    presentation = build_approval_presentation({
        "id": "approval-command",
        "tool": "exec_command",
        "reason": "需要运行定向测试",
        "arguments": {"command": "pytest -q"},
    })

    assert presentation.context.justification == "需要运行定向测试"


def test_patch_payload_is_normalized_with_structured_preview() -> None:
    presentation = build_approval_presentation({
        "id": "approval-patch",
        "tool": "apply_patch",
        "arguments": {"patch": "*** Begin Patch"},
        "patch": "*** Begin Patch",
        "preview": {
            "delta": {
                "changes": [{
                    "path": "src/app.py",
                    "action": "modify",
                    "old_content": "old\n",
                    "new_content": "new\n",
                    "source_path": None,
                    "hunks": [{"lines": [
                        {
                            "kind": "remove",
                            "text": "old",
                            "old_line": 1,
                            "new_line": None,
                        },
                        {
                            "kind": "add",
                            "text": "new",
                            "old_line": None,
                            "new_line": 1,
                        },
                    ]}],
                }],
            },
            "files": [{"path": "src/app.py"}],
        },
    })

    assert isinstance(presentation, ApplyPatchApprovalPresentation)
    assert presentation.context.kind == "apply_patch"
    assert presentation.patch == "*** Begin Patch"
    assert presentation.patch_view is not None
    assert presentation.patch_view.files[0].new_path == "src/app.py"
    assert presentation.patch_view.files[0].added == 1
    assert presentation.patch_view.files[0].removed == 1


def test_explicit_kind_controls_presentation_title_without_tool_name() -> None:
    presentation = build_approval_presentation({
        "id": "approval-permissions",
        "kind": "permissions",
    })

    assert presentation.context.kind == "request_permissions"
    assert presentation.context.prompt == (
        "Would you like to grant these permissions?"
    )


def test_network_presentation_uses_command_surface_and_network_title() -> None:
    presentation = build_approval_presentation({
        "kind": "network_access",
        "approval_id": "approval-network",
        "call_id": "call-network",
        "target": "https://api.example.com/v1",
        "host": "api.example.com",
        "command": ["curl", "https://api.example.com/v1"],
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(presentation, ExecApprovalPresentation)
    assert presentation.context.kind == "network_access"
    assert presentation.context.prompt == (
        'Do you want to approve network access to "api.example.com"?'
    )
    assert presentation.network_target == "https://api.example.com:443"


def test_permissions_and_mcp_presentation_summaries_are_action_specific() -> None:
    permissions = build_approval_presentation({
        "kind": "request_permissions",
        "approval_id": "approval-permissions",
        "call_id": "call-permissions",
        "permissions": {
            "network": {"enabled": True},
            "file_system": {
                "entries": [{"path": "D:/workspace/out", "access": "write"}]
            },
        },
        "available_decisions": ["grantForTurn", "decline"],
    })
    mcp = build_approval_presentation({
        "kind": "mcp_tool_call",
        "approval_id": "approval-mcp",
        "call_id": "call-mcp",
        "server": "github",
        "tool_name": "create_issue",
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(permissions, RequestPermissionsApprovalPresentation)
    assert permissions.summary == (
        "network; write `D:/workspace/out`"
    )
    assert isinstance(mcp, McpApprovalPresentation)
    assert mcp.summary == "github: create_issue"
    assert mcp.context.prompt == (
        "Would you like to approve the following MCP tool call?"
    )
    assert mcp.risk == "unknown"


def test_mcp_presentation_redacts_and_bounds_structured_arguments() -> None:
    arguments = {
        "authorization": "Bearer private",
        "body": "line\nnext\x1b[2J",
        "_nested": {
            "api_key": "private",
            "items": [{"password": "private", "visible": "value"}],
        },
        "a_wide": "路" * 100,
        **{f"field_{index:02d}": index for index in range(10)},
    }
    presentation = build_approval_presentation({
        "kind": "mcp_tool_call",
        "approval_id": "approval-mcp",
        "call_id": "call-mcp",
        "server": "github",
        "tool_name": "create_issue",
        "tool_title": "Create issue",
        "tool_description": "Create\nan issue.\x1b[31m",
        "connector_name": "GitHub",
        "connected_account_email": "user@example.com",
        "arguments": arguments,
        "annotations": {
            "read_only_hint": False,
            "destructive_hint": False,
            "open_world_hint": False,
        },
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(presentation, McpApprovalPresentation)
    assert presentation.server == "github"
    assert presentation.tool_name == "create_issue"
    assert presentation.title == "Create issue"
    assert presentation.description == "Create an issue.\\u001b[31m"
    assert presentation.risk == "external write"
    assert presentation.connector == "GitHub"
    assert presentation.account == "user@example.com"
    assert presentation.source_verified is True
    assert presentation.argument_count == 14
    assert len(presentation.arguments) == 12
    assert presentation.omitted_arguments == 2
    assert presentation.arguments_truncated is True
    rendered = "\n".join(
        f"{argument.name}: {argument.value}"
        for argument in presentation.arguments
    )
    assert "private" not in rendered
    assert '"authorization":' not in rendered
    assert "[redacted]" in rendered
    assert any(
        argument.name == "a_wide" and argument.value.endswith("…")
        for argument in presentation.arguments
    )
    assert "\\n" in rendered
    assert "\\u001b" in rendered
    assert len(rendered.encode("utf-8")) <= 2048


def test_mcp_argument_budget_includes_field_separators() -> None:
    presentation = build_approval_presentation({
        "kind": "mcp_tool_call",
        "approval_id": "approval-mcp-budget",
        "call_id": "call-mcp-budget",
        "server": "docs",
        "tool_name": "publish",
        "arguments": {
            f"field_{index:02d}_{'n' * 100}": "v" * 500
            for index in range(12)
        },
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(presentation, McpApprovalPresentation)
    rendered = "\n".join(
        f"{argument.name}: {argument.value}"
        for argument in presentation.arguments
    )
    assert len(rendered.encode("utf-8")) <= 2048


def test_mcp_presentation_degrades_invalid_arguments_and_identity() -> None:
    presentation = build_approval_presentation({
        "kind": "mcp_tool_call",
        "approval_id": "approval-mcp-degraded",
        "call_id": "call-mcp-degraded",
        "arguments": object(),
        "annotations": {"read_only_hint": True},
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(presentation, McpApprovalPresentation)
    assert presentation.server == "unknown"
    assert presentation.tool_name == "unknown"
    assert presentation.risk == "unknown"
    assert presentation.degraded is True


@pytest.mark.parametrize(
    ("annotations", "risk"),
    (
        ({"read_only_hint": True}, "read-only"),
        ({"destructive_hint": True}, "destructive"),
        ({"open_world_hint": True}, "open-world"),
        (
            {"destructive_hint": False, "open_world_hint": False},
            "external write",
        ),
        (None, "unknown"),
    ),
)
def test_mcp_presentation_projects_each_risk_level(
    annotations: dict[str, bool] | None,
    risk: str,
) -> None:
    presentation = build_approval_presentation({
        "kind": "mcp_tool_call",
        "approval_id": f"approval-{risk}",
        "call_id": f"call-{risk}",
        "server": "docs",
        "tool_name": "lookup",
        "arguments": {},
        "annotations": annotations,
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(presentation, McpApprovalPresentation)
    assert presentation.risk == risk


def test_local_mcp_remember_decisions_do_not_expand_wire_contract() -> None:
    payload = {
        "kind": "mcp_tool_call",
        "id": "approval-mcp-local",
        "tool": "mcp__docs__publish",
        "server": "docs",
        "tool_name": "publish",
        "available_decisions": [
            "accept",
            "acceptForSession",
            "acceptAndRemember",
            "decline",
        ],
    }

    with pytest.raises(ValueError, match="invalid for kind"):
        build_approval_request(payload)

    request = build_approval_request({
        **payload,
        "_local_mcp_approval": True,
    })
    assert request.decisions == (
        "accept",
        "acceptForSession",
        "acceptAndRemember",
        "decline",
    )


def test_permission_summary_formats_structured_paths() -> None:
    presentation = build_approval_presentation({
        "kind": "request_permissions",
        "permissions": {
            "file_system": {
                "entries": [{
                    "path": {"type": "path", "path": "D:/workspace/out"},
                    "access": "read",
                }]
            }
        },
    })
    assert presentation.summary == "read `D:/workspace/out`"


def test_approval_request_keeps_canonical_kind_and_payload_snapshot() -> None:
    source = {
        "id": "approval-permissions",
        "kind": "request_permissions",
        "permissions": {"network": {"enabled": True}},
        "available_decisions": ["grantForTurn", "decline"],
    }

    request = build_approval_request(source)
    source["kind"] = "command"
    source["permissions"]["network"]["enabled"] = False

    assert request.key.kind == "request_permissions"
    assert request.payload.kind == "request_permissions"
    assert request.payload.get("permissions") == {
        "network": {"enabled": True}
    }


def test_approval_trace_uses_codex_execpolicy_amendment_wording() -> None:
    approval = {
        "tool": "shell_command",
        "arguments": {
            "command": "env GIT_CONFIG_GLOBAL=/dev/null git status",
        },
        "proposed_execpolicy_amendment": {
            "id": "rule-1",
            "command_prefix": [
                "env",
                "GIT_CONFIG_GLOBAL=/dev/null",
                "GIT_CONFIG_SYSTEM=/dev/null",
                "GIT_TERMINAL_PROMPT=0",
            ],
            "display": (
                "env 'GIT_CONFIG_GLOBAL=/dev/null' "
                "'GIT_CONFIG_SYSTEM=/dev/null' 'GIT_TERMINAL_PROMPT=0'"
            ),
        },
    }

    block = render_approval_view(build_approval_view(
        approval,
        decision="acceptWithExecpolicyAmendment",
    ))

    assert block.plain_text == (
        "✔ You approved mind to always run commands that start with "
        "env 'GIT_CONFIG_GLOBAL=/dev/null' 'GIT_CONFIG_SYSTEM=/dev/null' "
        "'GIT_TERMINAL..."
    )


def test_approval_trace_uses_codex_session_wording() -> None:
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "git status"},
    }

    block = render_approval_view(build_approval_view(
        approval,
        decision="acceptForSession",
    ))

    assert block.plain_text == (
        "✔ You approved mind to run git status every time this session"
    )


def test_local_mcp_persistent_approval_trace_uses_tool_scope() -> None:
    block = render_approval_view(build_approval_view(
        {
            "kind": "mcp_tool_call",
            "tool": "mcp__github__create_issue",
            "server": "github",
            "tool_name": "create_issue",
        },
        decision="acceptAndRemember",
    ))

    assert block.plain_text == (
        "✔ You approved mind to call github: create_issue without asking again"
    )


def test_approval_trace_uses_shared_codex_snippet_limit() -> None:
    command = "git " + ("x" * 100)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": command},
    }

    block = render_approval_view(build_approval_view(
        approval,
        decision="accept",
    ))

    assert block.plain_text == (
        "✔ You approved mind to run "
        + command[:77]
        + "... this time"
    )


def test_approval_trace_does_not_split_combining_graphemes() -> None:
    command = "e\u0301" * 100
    approval = {
        "tool": "shell_command",
        "arguments": {"command": command},
    }

    block = render_approval_view(build_approval_view(
        approval,
        decision="accept",
    ))

    assert block.plain_text == (
        "✔ You approved mind to run "
        + command[:77 * 2]
        + "... this time"
    )
