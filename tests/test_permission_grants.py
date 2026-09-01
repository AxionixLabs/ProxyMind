# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.application.approvals.models import ApprovalOutcome
from agent.application.tools.authorization import ToolTurnInterrupted
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.permissions import permission_tools
from agent.stores.approvals.permissions import PermissionGrantStore
from agent.application.tools.coding import coding_tools
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from agent.application.turns.context import AgentContext, TurnContext
from agent.application.approvals.local_policy import (
    local_exec_policy_requirement,
)
from agent.domain.policies import preset_permissions
from agent.adapters.protocol.approval_events import approval_report_kwargs


def _profile(path: str = "D:/workspace/out") -> dict[str, object]:
    return {
        "network": {"enabled": True},
        "file_system": {
            "entries": [{"path": path, "access": "write"}],
        },
    }


def test_turn_grant_is_limited_to_identity_environment_and_cwd() -> None:
    store = PermissionGrantStore()
    store.grant(
        scope="turn",
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=_profile(),
        requested_permissions=_profile(),
        strict_auto_review=True,
    )

    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions={"network": {"enabled": True}},
    ) is True
    assert store.strict_auto_review_enabled(
        cid="cid-1", sid="sid-1", turn_id="turn-1"
    ) is True
    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-2",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=_profile(),
    ) is False


def test_grant_intersection_drops_fields_outside_requested_profile() -> None:
    store = PermissionGrantStore()
    requested = {
        "file_system": {
            "entries": [{"path": "D:/workspace/out", "access": "read"}]
        }
    }
    store.grant(
        scope="turn",
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions={
            "file_system": {
                "entries": [{
                    "path": "D:/workspace/out",
                    "access": "read",
                    "missing_path_behavior": "skip",
                }]
            },
            "network": {"enabled": True},
        },
        requested_permissions=requested,
    )
    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=requested,
    ) is True
    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions={"network": {"enabled": True}},
    ) is False
    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="danger-full-access",
        cwd="D:/workspace",
        permissions=_profile(),
    ) is False


def test_session_grant_is_available_to_later_turns_but_not_other_session() -> None:
    store = PermissionGrantStore()
    store.grant(
        scope="session",
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-1",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=_profile(),
    )

    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id="turn-2",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=_profile(),
    ) is True
    assert store.has_grant(
        cid="cid-1",
        sid="sid-2",
        turn_id="turn-2",
        environment_id="workspace-write",
        cwd="D:/workspace",
        permissions=_profile(),
    ) is False


def test_permission_approval_report_contains_only_grant_fields() -> None:
    approval = {
        "kind": "request_permissions",
        "permissions": _profile(),
    }
    fields = approval_report_kwargs(
        approval,
        decision="grantForTurnWithStrictAutoReview",
        source="user",
        turn_id="turn-1",
    )
    assert fields["scope"] == "turn"
    assert fields["permissions"] == _profile()
    assert fields["strict_auto_review"] is True

    declined = approval_report_kwargs(
        approval,
        decision="decline",
        source="user",
        turn_id="turn-1",
    )
    assert "scope" not in declined
    assert "permissions" not in declined
    assert "strict_auto_review" not in declined


@pytest.mark.anyio
async def test_request_permissions_tool_records_turn_grant(tmp_path) -> None:
    store = PermissionGrantStore()
    context = TurnContext.create(
        agent=AgentContext.root("sid-1"),
        cid="cid-1",
        sid="sid-1",
        source="test",
        pref_config={"primary": {"model": "test"}},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        permission_grants=store,
    )
    coordinator = SimpleNamespace(
        request_outcome=AsyncMock(
            return_value=ApprovalOutcome.create(
                "grantForTurn",
                source="user",
                reason="user",
            )
        )
    )
    tool = permission_tools(coordinator, store)[0]
    assert "reason" in tool.input_schema["properties"]
    assert "justification" not in tool.input_schema["properties"]
    runtime = ToolHandlerContext(
        session=SimpleNamespace(),
        turn_context=context,
        pref_config={},
        call_id="call-permission",
    )

    result = await tool.handler({
        "reason": "read generated output",
        "permissions": {
            "file_system": {
                "read": ["output.txt"],
            },
        },
    }, runtime)

    assert result.ok is True
    assert result.data["scope"] == "turn"
    assert store.has_grant(
        cid="cid-1",
        sid="sid-1",
        turn_id=context.turn_id,
        environment_id="",
        cwd=str(tmp_path),
        permissions={
            "file_system": {
                "read": [str(tmp_path / "output.txt")],
            },
        },
    ) is True
    approval = coordinator.request_outcome.await_args.args[0]
    assert approval["kind"] == "request_permissions"
    assert approval["permissions"]["file_system"]["read"] == [
        str(tmp_path / "output.txt")
    ]
    assert approval["reason"] == "read generated output"
    assert "justification" not in approval


@pytest.mark.anyio
async def test_request_permissions_tool_skips_card_for_never_policy(tmp_path) -> None:
    coordinator = SimpleNamespace(request_outcome=AsyncMock())
    context = TurnContext.create(
        agent=AgentContext.root("sid-1"),
        cid="cid-1",
        sid="sid-1",
        source="test",
        pref_config={"primary": {"model": "test"}},
        cwd=str(tmp_path),
        permissions=preset_permissions("full-access"),
        permission_grants=PermissionGrantStore(),
    )
    store = PermissionGrantStore()
    tool = permission_tools(coordinator, store)[0]
    result = await tool.handler({
        "permissions": {"network": {"enabled": True}},
    }, ToolHandlerContext(
        session=SimpleNamespace(),
        turn_context=context,
        pref_config={},
        call_id="call-never",
    ))

    assert result.data["permissions"] == {}
    coordinator.request_outcome.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("decision", "scope", "strict"),
    [
        ("grantForSession", "session", False),
        ("grantForTurnWithStrictAutoReview", "turn", True),
        ("decline", "turn", False),
    ],
)
async def test_request_permissions_tool_returns_native_decision_result(
    tmp_path,
    decision: str,
    scope: str,
    strict: bool,
) -> None:
    store = PermissionGrantStore()
    context = TurnContext.create(
        agent=AgentContext.root("sid-1"),
        cid="cid-1",
        sid="sid-1",
        source="test",
        pref_config={"primary": {"model": "test"}},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        permission_grants=store,
    )
    coordinator = SimpleNamespace(
        request_outcome=AsyncMock(
            return_value=ApprovalOutcome.create(
                decision,
                source="user",
                reason="user",
            )
        )
    )
    tool = permission_tools(coordinator, store)[0]
    result = await tool.handler(
        {"permissions": {"network": {"enabled": True}}},
        ToolHandlerContext(
            session=SimpleNamespace(),
            turn_context=context,
            pref_config={},
            call_id=f"call-{decision}",
        ),
    )

    data = result.data
    assert data["scope"] == scope
    assert data["strict_auto_review"] is strict
    assert bool(data["permissions"]) is (decision != "decline")
    if decision == "grantForSession":
        assert store.has_grant(
            cid=context.cid,
            sid=context.sid,
            turn_id="turn-2",
            environment_id="",
            cwd=str(tmp_path),
            permissions={"network": {"enabled": True}},
        ) is True


@pytest.mark.anyio
async def test_request_permissions_cancel_interrupts_turn(tmp_path) -> None:
    store = PermissionGrantStore()
    context = TurnContext.create(
        agent=AgentContext.root("sid-cancel"),
        cid="cid-cancel",
        sid="sid-cancel",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        permission_grants=store,
    )
    coordinator = SimpleNamespace(
        request_outcome=AsyncMock(
            return_value=ApprovalOutcome.create(
                "cancel",
                source="user",
                reason="user",
            )
        )
    )
    interrupt = AsyncMock(return_value=True)
    tool = permission_tools(coordinator, store)[0]

    with pytest.raises(ToolTurnInterrupted, match="request_permissions cancelled"):
        await tool.handler(
            {"permissions": {"network": {"enabled": True}}},
            ToolHandlerContext(
                session=SimpleNamespace(),
                turn_context=context,
                pref_config={},
                call_id="call-cancel",
                interrupt_turn=interrupt,
            ),
        )

    interrupt.assert_awaited_once_with("call-cancel")
    assert store.turn_grants == ()


def test_inline_permissions_require_command_approval_until_granted(tmp_path) -> None:
    context = TurnContext.create(
        agent=AgentContext.root("sid-1"),
        cid="cid-1",
        sid="sid-1",
        source="test",
        pref_config={"primary": {"model": "test"}},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        permission_grants=PermissionGrantStore(),
    )
    arguments = {
        "command": "echo ready",
        "sandbox_permissions": "with_additional_permissions",
        "additional_permissions": {
            "file_system": {"read": ["outside.txt"]},
        },
    }
    requirement = local_exec_policy_requirement(
        ExecPolicyManager(workspace_root=tmp_path),
        context,
        tool="shell_command",
        arguments=arguments,
        call_id="call-inline",
    )
    assert requirement is not None
    assert requirement.state == "needs_approval"

    assert context.permission_grants is not None
    context.permission_grants.grant(
        scope="turn",
        cid=context.cid,
        sid=context.sid,
        turn_id=context.turn_id,
        environment_id="",
        cwd=str(tmp_path),
        permissions={
            "file_system": {"read": [str(tmp_path / "outside.txt")]},
        },
        requested_permissions={
            "file_system": {"read": [str(tmp_path / "outside.txt")]},
        },
    )
    granted = local_exec_policy_requirement(
        ExecPolicyManager(workspace_root=tmp_path),
        context,
        tool="shell_command",
        arguments=arguments,
        call_id="call-inline-granted",
    )
    assert granted is not None
    assert granted.state != "needs_approval"
