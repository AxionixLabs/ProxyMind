# -*- coding: utf-8 -*-

from mind_app.approval.permission_grants import PermissionGrantStore
from mind_app.runtime.execution import AgentContext, ToolInvocation, TurnContext
from mind_app.runtime.turns.stream_policy import local_permission_approval
from mind_core.permissions import preset_permissions
from mind_app.runtime.turns.stream_approval import approval_report_kwargs


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


def test_local_permission_request_uses_permission_kind_and_native_decisions() -> None:
    context = TurnContext.create(
        agent=AgentContext.root("sid-1"),
        cid="cid-1",
        sid="sid-1",
        source="test",
        pref_config={"primary": {"model": "test"}},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
    )
    approval = local_permission_approval(ToolInvocation(
        turn=context,
        call_id="call-1",
        name="shell_command",
        arguments={
            "command": "curl https://example.com",
            "sandbox_permissions": "with_additional_permissions",
            "additional_permissions": {"network": {"enabled": True}},
        },
    ))
    assert approval["kind"] == "request_permissions"
    assert approval["available_decisions"] == [
        "grantForTurn",
        "grantForTurnWithStrictAutoReview",
        "grantForSession",
        "decline",
    ]
