from dataclasses import replace
from types import SimpleNamespace

from agent.application.approvals.models import ApprovalRequest, ApprovalRequestKey, ApprovalPayload
from agent.application.approvals.presentation import ApprovalPresentationContext, ExecApprovalPresentation
from frontends.subscription.interaction import interaction_payload


def test_approval_snapshot_preserves_exact_decisions_and_oversized_details_require_local_confirmation():
    context = ApprovalPresentationContext(request_id="current",approval_id="runtime-approval",call_id="tool-call",
        kind="exec",tool="shell",prompt="允许执行？",decisions=("accept","decline"))
    presentation = ExecApprovalPresentation(context=context,commands=("pwd",),summary="读取当前目录")
    request = ApprovalRequest(key=ApprovalRequestKey("current","runtime-approval","tool-call","shell","exec"),
        payload=ApprovalPayload(kind="exec",values={}),presentation=presentation,decisions=("accept","decline"))
    snapshot = SimpleNamespace(current=request,revision=3,resolved=())
    payload = interaction_payload("remote-call",SimpleNamespace(snapshot=snapshot))
    assert payload["approval"]["approval_id"] == "current"
    assert payload["approval"]["decisions"] == ["accept","decline"]
    assert not payload["local_required"]
    snapshot.current = replace(request,presentation=replace(presentation,commands=("x"*100001,)))
    payload = interaction_payload("remote-call",SimpleNamespace(snapshot=snapshot))
    assert payload["approval"] is None and payload["local_required"]
    snapshot.current = SimpleNamespace()
    assert interaction_payload("remote-call",SimpleNamespace(snapshot=snapshot))["local_required"]
    snapshot.current = None
    assert not interaction_payload("remote-call",SimpleNamespace(snapshot=snapshot))["local_required"]
