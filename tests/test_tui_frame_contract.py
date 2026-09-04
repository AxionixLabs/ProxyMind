"""把生产 Turn activity event 归约为稳定的逻辑帧契约。"""

import pytest

from agent.ports import (
    ApprovalCompleted,
    ApprovalStarted,
    AssistantBuffered,
    AssistantSettled,
    AssistantVisible,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    SurfaceTurnStarted,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)
from frontends.tui.runtime.turn_surface import (
    SurfaceProjection,
    TurnSurfaceState,
    initial_turn_surface_state,
    project_turn_surface,
    reduce_turn_surface,
)
from tests.support.turn_scenarios import (
    FrameIndicator,
    FrameKind,
    FrameTrace,
    LogicalFrame,
    RuntimeInvariantError,
)


CONTEXT = OutputSurfaceContext(
    surface_id="surface-frame-contract",
    cid="cid-frame-contract",
    sid="sid-frame-contract",
    turn_id="turn-frame-contract",
    agent_id="root",
)
IDENTITY = ResponseIdentity("turn-frame-contract", 1, 1, 1)


def _scope() -> dict[str, str]:
    return {
        "surface_id": CONTEXT.surface_id,
        "turn_id": CONTEXT.turn_id,
    }


def _indicator(
    state: TurnSurfaceState,
    projection: SurfaceProjection,
) -> FrameIndicator:
    """把生产活动投影转换为与具体终端无关的逻辑指示器。"""
    if state.approvals:
        return FrameIndicator.APPROVAL
    if projection.indicator == "thinking":
        return (
            FrameIndicator.WORKING
            if state.tools or state.batches
            else FrameIndicator.THINKING
        )
    if projection.indicator == "retrying":
        return FrameIndicator.RETRYING
    return FrameIndicator.HIDDEN


@pytest.mark.runtime_p0
@pytest.mark.runtime_frame
def test_typed_event_trace_satisfies_frame_contract() -> None:
    """验证等待、正文、工具、审批和终态只产生合法逻辑帧。"""
    events = (
        SurfaceTurnStarted(**_scope()),
        ModelWaitRequested(**_scope(), revision=1, reason="initial"),
        AssistantBuffered(
            **_scope(),
            identity=IDENTITY,
            item_id="item-hello",
        ),
        AssistantVisible(
            **_scope(),
            identity=IDENTITY,
            item_id="item-hello",
        ),
        AssistantSettled(
            **_scope(),
            identity=IDENTITY,
            item_id="item-hello",
        ),
        ToolStarted(
            **_scope(),
            tool_id="call-network",
            tool_kind="client",
            name="network",
        ),
        ModelWaitRequested(**_scope(), revision=2, reason="lifecycle"),
        ApprovalStarted(
            **_scope(),
            approval_id="approval-network",
            call_id="call-network",
        ),
        ApprovalCompleted(
            **_scope(),
            approval_id="approval-network",
            call_id="call-network",
        ),
        ToolCompleted(
            **_scope(),
            tool_id="call-network",
            tool_kind="client",
            name="network",
        ),
        ModelWaitRequested(**_scope(), revision=3, reason="tool_result"),
        AssistantBuffered(
            **_scope(),
            identity=IDENTITY,
            item_id="item-done",
        ),
        AssistantVisible(
            **_scope(),
            identity=IDENTITY,
            item_id="item-done",
        ),
        TurnTerminal(**_scope(), status="completed"),
    )
    state = initial_turn_surface_state(CONTEXT)
    trace = FrameTrace()
    active_assistant = ""

    for event in events:
        state = reduce_turn_surface(state, event)
        projection = project_turn_surface(state)
        kind = FrameKind.NORMAL
        if isinstance(event, AssistantVisible):
            active_assistant = (
                "hello" if event.item_id == "item-hello" else "done"
            )
            kind = FrameKind.ASSISTANT_HANDOFF
        elif isinstance(event, AssistantSettled):
            active_assistant = ""
        elif isinstance(event, ApprovalCompleted):
            kind = FrameKind.APPROVAL_COMPLETED
        elif isinstance(event, TurnTerminal):
            kind = FrameKind.TERMINAL
        trace.append(LogicalFrame(
            turn_id=CONTEXT.turn_id,
            indicator=_indicator(state, projection),
            assistant_text=active_assistant,
            kind=kind,
            tool_leases=len(state.tools),
            approval_leases=len(state.approvals),
        ))

    trace.assert_contract()
    handoffs = tuple(
        frame
        for frame in trace.frames
        if frame.kind is FrameKind.ASSISTANT_HANDOFF
    )
    assert tuple(frame.assistant_text for frame in handoffs) == (
        "hello",
        "done",
    )
    approval_completed = next(
        frame
        for frame in trace.frames
        if frame.kind is FrameKind.APPROVAL_COMPLETED
    )
    assert approval_completed.indicator is FrameIndicator.WORKING
    assert approval_completed.tool_leases == 1
    assert trace.frames[-1].kind is FrameKind.TERMINAL
    assert trace.frames[-1].indicator is FrameIndicator.HIDDEN


@pytest.mark.runtime_frame
def test_frame_contract_rejects_old_turn_revival() -> None:
    """验证新 Turn 上屏后旧 Turn 不能重新取得活动画面。"""
    trace = FrameTrace()
    trace.append(LogicalFrame(
        turn_id="turn-1",
        indicator=FrameIndicator.HIDDEN,
        kind=FrameKind.TERMINAL,
    ))
    trace.append(LogicalFrame(
        turn_id="turn-2",
        indicator=FrameIndicator.THINKING,
    ))

    with pytest.raises(RuntimeInvariantError, match="old turn"):
        trace.append(LogicalFrame(
            turn_id="turn-1",
            indicator=FrameIndicator.HIDDEN,
        ))

