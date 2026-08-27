# -*- coding: utf-8 -*-

from mind_app.approval.presentation import (
    ApplyPatchApprovalPresentation,
    ExecApprovalPresentation,
    ToolApprovalPresentation,
    build_approval_presentation,
    ensure_approval_presentation,
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.renderers.approval import render_approval_view


def test_command_payload_is_normalized_to_exec_presentation() -> None:
    presentation = build_approval_presentation({
        "id": "approval-command",
        "tool": "exec_command",
        "arguments": {
            "command": ["pwsh", "-Command", "Get-Date"],
        },
    })

    assert isinstance(presentation, ExecApprovalPresentation)
    assert presentation.context.kind == "exec"
    assert presentation.context.approval_id == "approval-command"
    assert presentation.commands == (("pwsh", "-Command", "Get-Date"),)
    assert presentation.context.prompt == (
        "Would you like to run the following command?"
    )
    assert ensure_approval_presentation(presentation) is presentation


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

    assert presentation.context.kind == "permissions"
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
    assert presentation.context.kind == "exec"
    assert presentation.context.prompt == (
        'Do you want to approve network access to "api.example.com"?'
    )


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

    assert isinstance(permissions, ToolApprovalPresentation)
    assert permissions.summary == (
        "network access; file access: D:/workspace/out"
    )
    assert isinstance(mcp, ToolApprovalPresentation)
    assert mcp.summary == "github: create_issue"


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
