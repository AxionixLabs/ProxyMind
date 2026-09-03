# -*- coding: utf-8 -*-

import pytest

from agent.application.approvals.summary import approval_review_action_summary
from agent.application.views import ApprovalReviewView
from agent.application.views.builders.approval import build_approval_view
from frontends.terminal.renderers.dispatch import render_presentation_view


@pytest.mark.parametrize(
    ("kind", "action", "expected"),
    (
        (
            "command",
            {"command": ["curl", "https://example.com"]},
            "run curl https://example.com",
        ),
        (
            "write_stdin",
            {"session_id": "terminal-1", "input": "continue\n"},
            "write to terminal terminal-1",
        ),
        (
            "apply_patch",
            {"files": ["src/app.py", "tests/test_app.py"]},
            "apply a patch to 2 files",
        ),
        (
            "network_access",
            {"target": "https://example.com:443"},
            "access https://example.com:443",
        ),
        (
            "request_permissions",
            {"scope": "turn", "permissions": {"network": True}},
            "use the requested permissions for turn",
        ),
        (
            "mcp_tool_call",
            {"server": "github", "tool_name": "create_issue"},
            "call MCP tool github.create_issue",
        ),
    ),
)
def test_review_action_summary_is_specific_to_action_kind(
    kind,
    action,
    expected: str,
) -> None:
    assert approval_review_action_summary(kind, action) == expected


def _view(status: str) -> ApprovalReviewView:
    decided = status in {"approved", "denied"}
    return ApprovalReviewView(
        review_id="review-1",
        approval_id="approval-1",
        call_id="call-1",
        action_kind="network_access",
        action_summary="access https://example.com:443",
        status=status,
        risk_level="high" if decided else None,
        user_authorization="low" if decided else None,
        rationale=(
            "The request could send data outside the workspace."
            if decided
            else (
                "The automatic review exceeded its budget."
                if status == "timed_out"
                else None
            )
        ),
    )


@pytest.mark.parametrize("status", ("approved", "aborted"))
def test_tui_review_success_and_abort_do_not_create_history(status: str) -> None:
    assert render_presentation_view(_view(status)) == ()


def test_denied_review_renders_warning_and_action_record() -> None:
    blocks = render_presentation_view(_view("denied"))

    assert tuple(block.plain_text for block in blocks) == (
        "⚠ Automatic approval review denied (risk: high): "
        "The request could send data outside the workspace.",
        "• Request denied for mind to access https://example.com:443",
    )


def test_timed_out_review_uses_distinct_fail_closed_wording() -> None:
    blocks = render_presentation_view(_view("timed_out"))

    assert tuple(block.plain_text for block in blocks) == (
        "⚠ Automatic approval review timed out while evaluating the requested "
        "approval.",
        "• Review timed out before mind could access https://example.com:443",
    )


def test_legacy_approval_view_rejects_automatic_review_source() -> None:
    with pytest.raises(ValueError, match="must use ApprovalReviewView"):
        build_approval_view(
            {"kind": "command", "command": ["git", "status"]},
            decision="accept",
            source="auto_review",
        )


if __name__ == '__main__':
    pass
