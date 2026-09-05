# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.ports import (
    ApprovalCompleted,
    ApprovalReviewCompleted,
    ApprovalReviewStarted,
    ApprovalStarted,
    AssistantBuffered,
    AssistantSegmentCompleted,
    AssistantSettled,
    AssistantTextDelta,
    AssistantVisible,
    ModelWaitRequested,
    OutputSession,
    OutputSurfaceContext,
    PassiveOutputActivity,
    PresentationSuperseded,
    RecoveryChanged,
    ResponseIdentity,
    RetryChanged,
    SurfaceClosed,
    SurfaceTurnStarted,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)
from frontends.tui.runtime.turn_surface import (
    SurfaceProjection,
    TuiTurnSurfaceCoordinator,
    TurnSurfaceTiming,
    initial_turn_surface_state,
    project_turn_surface,
    reduce_turn_surface,
)
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.runtime import TuiRuntime


def _context(*, surface_id: str = "surface_test") -> OutputSurfaceContext:
    """构造固定的输出会话身份。"""
    return OutputSurfaceContext(
        surface_id=surface_id,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        agent_id="root",
    )


def _scope(context: OutputSurfaceContext) -> dict[str, str]:
    """返回 typed activity event 的公共 scope 参数。"""
    return {
        "surface_id": context.surface_id,
        "turn_id": context.turn_id,
    }


def _identity() -> ResponseIdentity:
    """构造固定 provider response 身份。"""
    return ResponseIdentity("turn_test", 1, 1, 1)


def _activity_text(runtime: TuiRuntime) -> str:
    """返回当前 TUI 活动表面的纯文本。"""
    block = runtime.screen.activity_block
    if block is None:
        return ""
    return "".join(fragment[1] for fragment in block.fragments)


def _active_state(context: OutputSurfaceContext):
    """创建已经开始观察 Turn 的 reducer 状态。"""
    state = initial_turn_surface_state(context)
    return reduce_turn_surface(state, SurfaceTurnStarted(**_scope(context)))


def test_reducer_tracks_content_wait_and_terminal_idempotently() -> None:
    context = _context()
    state = _active_state(context)
    wait = ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    )

    state = reduce_turn_surface(state, wait)
    assert project_turn_surface(state) == SurfaceProjection(
        "thinking",
        title="Thinking",
        revision=2,
    )
    assert reduce_turn_surface(state, wait) is state

    with pytest.raises(ValueError, match="revision was reused"):
        reduce_turn_surface(state, ModelWaitRequested(
            **_scope(context),
            revision=1,
            reason="tool_result",
        ))

    buffered = AssistantBuffered(
        **_scope(context),
        identity=_identity(),
        item_id="item_answer",
    )
    visible = AssistantVisible(
        **_scope(context),
        identity=_identity(),
        item_id="item_answer",
    )
    settled = AssistantSettled(
        **_scope(context),
        identity=_identity(),
        item_id="item_answer",
    )
    state = reduce_turn_surface(state, buffered)
    state = reduce_turn_surface(state, visible)
    assert project_turn_surface(state).indicator == "hidden"
    state = reduce_turn_surface(state, settled)
    assert reduce_turn_surface(state, visible) is state
    assert reduce_turn_surface(state, settled) is state

    terminal = TurnTerminal(**_scope(context), status="completed")
    state = reduce_turn_surface(state, terminal)
    assert project_turn_surface(state).indicator == "hidden"
    assert reduce_turn_surface(state, terminal) is state
    assert reduce_turn_surface(state, wait) is state

    state = reduce_turn_surface(state, SurfaceClosed(**_scope(context)))
    assert state.lifecycle == "closed"


def test_reducer_rejects_wrong_scope_and_invalid_content_order() -> None:
    context = _context()
    state = _active_state(context)

    with pytest.raises(ValueError, match="does not match surface scope"):
        reduce_turn_surface(state, ModelWaitRequested(
            surface_id="surface_other",
            turn_id="turn_test",
            revision=1,
            reason="initial",
        ))

    with pytest.raises(ValueError, match="was not buffered"):
        reduce_turn_surface(state, AssistantVisible(
            **_scope(context),
            identity=_identity(),
            item_id="item_missing",
        ))


def test_turn_running_and_status_visibility_are_independent() -> None:
    """验证 Turn running、状态请求和临时可见性互不替代。"""
    context = _context(surface_id="surface_visibility")
    state = _active_state(context)
    assert state.lifecycle == "active"
    assert not state.status_requested
    assert not project_turn_surface(state).visible

    state = reduce_turn_surface(state, ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    assert state.status_requested
    assert project_turn_surface(state).visible

    identity = _identity()
    state = reduce_turn_surface(state, AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    state = reduce_turn_surface(state, AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    assert state.lifecycle == "active"
    assert not state.status_requested
    assert not project_turn_surface(state).visible

    state = reduce_turn_surface(state, AssistantSettled(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    state = reduce_turn_surface(state, ToolStarted(
        **_scope(context),
        tool_id="call_1",
        tool_kind="nested",
        name="shell_command",
    ))
    assert state.status_requested
    assert project_turn_surface(state).visible

    state = reduce_turn_surface(state, ToolCompleted(
        **_scope(context),
        tool_id="call_1",
        tool_kind="nested",
        name="shell_command",
    ))
    assert state.lifecycle == "active"
    assert state.status_requested
    assert project_turn_surface(state).visible

    state = reduce_turn_surface(state, ApprovalStarted(
        **_scope(context),
        approval_id="approval_1",
        call_id="call_approval",
    ))
    assert state.lifecycle == "active"
    assert state.status_requested
    assert not project_turn_surface(state).visible
    state = reduce_turn_surface(state, ApprovalCompleted(
        **_scope(context),
        approval_id="approval_1",
        call_id="call_approval",
    ))
    assert project_turn_surface(state).visible

    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="replaying",
        event_seq=8,
    ))
    assert state.lifecycle == "active"
    assert state.status_requested
    assert not project_turn_surface(state).visible
    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="caught_up",
        event_seq=9,
    ))
    assert project_turn_surface(state).visible

    state = reduce_turn_surface(state, TurnTerminal(
        **_scope(context),
        status="completed",
    ))
    assert state.lifecycle == "terminal"
    assert not state.status_requested
    assert not project_turn_surface(state).visible
    assert reduce_turn_surface(state, ToolStarted(
        **_scope(context),
        tool_id="call_late",
        tool_kind="nested",
        name="shell_command",
    )) is state


def test_reducer_preserves_named_tool_leases_and_completion_history() -> None:
    context = _context()
    state = _active_state(context)
    first = ToolStarted(
        **_scope(context),
        tool_id="call_first",
        tool_kind="client",
        name="read_file",
    )
    second = ToolStarted(
        **_scope(context),
        tool_id="call_second",
        tool_kind="nested",
        name="delegate",
    )
    first_done = ToolCompleted(
        **_scope(context),
        tool_id="call_first",
        tool_kind="client",
        name="read_file",
    )

    state = reduce_turn_surface(state, first)
    state = reduce_turn_surface(state, second)
    state = reduce_turn_surface(state, first_done)

    assert tuple(tool.tool_id for tool in state.tools) == ("call_second",)
    assert project_turn_surface(state) == SurfaceProjection(
        "thinking",
        title="Thinking",
        revision=4,
    )
    assert reduce_turn_surface(state, first_done) is state
    assert reduce_turn_surface(state, first) is state

    with pytest.raises(ValueError, match="identity was reused"):
        reduce_turn_surface(state, ToolStarted(
            **_scope(context),
            tool_id="call_first",
            tool_kind="builtin",
            name="web_search",
        ))


def test_reducer_projects_parallel_approval_reviews_and_stable_details() -> None:
    context = _context()
    state = _active_state(context)
    reviews = tuple(
        ApprovalReviewStarted(
            **_scope(context),
            review_id=f"review-{index}",
            approval_id=f"approval-{index}",
            call_id=f"call-{index}",
            action_summary=f"run command {index}",
            presentation_epoch=1,
        )
        for index in range(1, 5)
    )
    for review in reviews:
        state = reduce_turn_surface(state, review)

    assert project_turn_surface(state) == SurfaceProjection(
        "reviewing",
        title="Reviewing 4 approval requests",
        detail="run command 1\nrun command 2\nrun command 3\n+1 more",
        revision=5,
    )

    completed = ApprovalReviewCompleted(
        **_scope(context),
        review_id="review-2",
        approval_id="approval-2",
        call_id="call-2",
        action_summary="run command 2",
        presentation_epoch=1,
    )
    state = reduce_turn_surface(state, completed)
    projection = project_turn_surface(state)
    assert projection.title == "Reviewing 3 approval requests"
    assert projection.detail == "run command 1\nrun command 3\nrun command 4"
    assert reduce_turn_surface(state, completed) is state


def test_reducer_clears_review_lease_on_presentation_supersede() -> None:
    context = _context()
    state = _active_state(context)
    review = ApprovalReviewStarted(
        **_scope(context),
        review_id="review-stale",
        approval_id="approval-stale",
        call_id="call-stale",
        action_summary="access https://example.com",
        presentation_epoch=1,
    )
    state = reduce_turn_surface(state, review)
    state = reduce_turn_surface(state, PresentationSuperseded(
        **_scope(context),
        superseded_epoch=1,
        presentation_epoch=2,
    ))

    assert state.approval_reviews == ()
    assert project_turn_surface(state).indicator == "hidden"


def test_reducer_suppresses_replay_and_restores_latest_projection() -> None:
    context = _context()
    state = _active_state(context)
    state = reduce_turn_surface(state, ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="replaying",
        event_seq=8,
    ))
    retry_started = RetryChanged(
        **_scope(context),
        source="provider",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=2,
    )
    state = reduce_turn_surface(state, retry_started)

    assert project_turn_surface(state).indicator == "hidden"

    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="caught_up",
        event_seq=8,
    ))
    assert project_turn_surface(state).indicator == "retrying"
    stale = RecoveryChanged(
        **_scope(context),
        mode="live",
        event_seq=7,
    )
    assert reduce_turn_surface(state, stale) is state


def test_transport_retry_overlays_visible_content_without_replacing_it() -> None:
    """验证断网重连临时恢复活动提示，但不释放当前正文所有权。"""
    context = _context()
    state = _active_state(context)
    identity = _identity()
    buffered = AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    )
    visible = AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    )
    retry_started = RetryChanged(
        **_scope(context),
        source="transport",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=1,
    )
    retry_completed = RetryChanged(
        **_scope(context),
        source="transport",
        state="completed",
        presentation_epoch=1,
        round=1,
        attempt=1,
    )

    state = reduce_turn_surface(state, buffered)
    state = reduce_turn_surface(state, visible)
    state = reduce_turn_surface(state, retry_started)

    assert state.content == "visible"
    assert state.visible_item is not None
    assert state.visible_item.identity == identity
    assert project_turn_surface(state) == SurfaceProjection(
        "retrying",
        title="Retrying",
        detail="transport",
        revision=4,
    )

    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="replaying",
        event_seq=8,
    ))
    assert project_turn_surface(state).indicator == "hidden"
    state = reduce_turn_surface(state, retry_completed)
    state = reduce_turn_surface(state, RecoveryChanged(
        **_scope(context),
        mode="caught_up",
        event_seq=8,
    ))

    assert state.content == "visible"
    assert state.visible_item is not None
    assert state.visible_item.identity == identity
    assert project_turn_surface(state).indicator == "hidden"


def test_provider_retry_atomically_releases_superseded_visible_content() -> None:
    context = _context()
    state = _active_state(context)
    identity = _identity()
    buffered = AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_attempt_1",
    )
    visible = AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_attempt_1",
    )
    state = reduce_turn_surface(state, buffered)
    state = reduce_turn_surface(state, visible)

    retry_started = RetryChanged(
        **_scope(context),
        source="provider",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=2,
    )
    state = reduce_turn_surface(state, retry_started)

    assert state.content == "none"
    assert state.visible_item is None
    assert project_turn_surface(state).indicator == "retrying"
    assert reduce_turn_surface(state, visible) is state
    state = reduce_turn_surface(state, RetryChanged(
        **_scope(context),
        source="provider",
        state="completed",
        presentation_epoch=1,
        round=1,
        attempt=2,
    ))
    assert reduce_turn_surface(state, retry_started) is state


def test_presentation_supersede_rejects_late_old_epoch_content() -> None:
    context = _context()
    state = _active_state(context)
    identity = _identity()
    buffered = AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_epoch_1",
    )
    visible = AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_epoch_1",
    )
    state = reduce_turn_surface(state, buffered)
    state = reduce_turn_surface(state, visible)
    replacement = PresentationSuperseded(
        **_scope(context),
        superseded_epoch=1,
        presentation_epoch=2,
    )
    state = reduce_turn_surface(state, replacement)

    assert state.content == "none"
    assert project_turn_surface(state).indicator == "hidden"
    assert reduce_turn_surface(state, visible) is state
    assert reduce_turn_surface(state, replacement) is state

    with pytest.raises(ValueError, match="identity was reused"):
        reduce_turn_surface(state, PresentationSuperseded(
            **_scope(context),
            superseded_epoch=1,
            presentation_epoch=3,
        ))

    state = reduce_turn_surface(state, ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="server_thinking",
    ))
    assert project_turn_surface(state).indicator == "thinking"


@pytest.mark.anyio
async def test_coordinator_cancels_stale_generation_and_closes_scope() -> None:
    context = _context()
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(lifecycle_sec=0.02),
    )
    await coordinator.open()
    assert projections == []
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="lifecycle",
    ))
    assert coordinator.pending_timer

    await coordinator.emit(TurnTerminal(
        **_scope(context),
        status="completed",
    ))
    await asyncio.sleep(0.03)

    assert not coordinator.pending_timer
    assert all(item.indicator != "thinking" for item in projections)
    await coordinator.close()
    assert coordinator.state.lifecycle == "closed"
    assert projections[-1].indicator == "hidden"

    with pytest.raises(RuntimeError, match="closed"):
        await coordinator.emit(ModelWaitRequested(
            **_scope(context),
            revision=2,
            reason="initial",
        ))


@pytest.mark.anyio
async def test_visible_content_supersedes_an_inflight_async_projection() -> None:
    context = _context(surface_id="surface_projection_race")
    entered = asyncio.Event()
    release = asyncio.Event()
    block_thinking = asyncio.Event()
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        if block_thinking.is_set() and projection.indicator == "thinking":
            entered.set()
            await release.wait()
        projections.append(projection)

    def apply_immediate(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        apply_immediate_projection=apply_immediate,
    )
    identity = _identity()
    await coordinator.open()
    await coordinator.emit(AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    block_thinking.set()
    pending = asyncio.create_task(coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    )))
    await entered.wait()

    coordinator.emit_assistant_visible(AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    release.set()
    await pending

    assert coordinator.state.content == "visible"
    assert projections[-1].indicator == "hidden"
    await coordinator.close()


@pytest.mark.anyio
async def test_transport_retry_restores_indicator_after_content_is_visible() -> None:
    """验证断网可重新取得活动区，且 replay 不与现有正文争夺画面。"""
    context = _context(surface_id="surface_visible_transport_retry")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    def apply_immediate(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        apply_immediate_projection=apply_immediate,
        timing=TurnSurfaceTiming(transport_retry_min_visible_sec=0.0),
    )
    identity = _identity()
    await coordinator.open()
    await coordinator.emit(AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    coordinator.emit_assistant_visible(AssistantVisible(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))
    assert projections[-1].indicator == "hidden"

    await coordinator.emit(RetryChanged(
        **_scope(context),
        source="transport",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=1,
    ))

    assert coordinator.state.content == "visible"
    assert projections[-1] == SurfaceProjection(
        "retrying",
        title="Retrying",
        detail="transport",
        revision=4,
    )

    await coordinator.emit(RecoveryChanged(
        **_scope(context),
        mode="replaying",
        event_seq=8,
    ))
    assert coordinator.state.content == "visible"
    assert projections[-1].indicator == "hidden"
    await coordinator.close()


@pytest.mark.anyio
async def test_tool_status_is_immediate_and_survives_bare_completion() -> None:
    """验证工具开始立即显示状态且裸完成不撤销 Turn 状态行。"""
    context = _context(surface_id="surface_fast_tool")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
    )
    await coordinator.open()
    await coordinator.emit(ToolStarted(
        **_scope(context),
        tool_id="call_fast",
        tool_kind="client",
        name="read_file",
    ))
    assert not coordinator.pending_timer
    assert [projection.indicator for projection in projections] == ["thinking"]

    await coordinator.emit(ToolCompleted(
        **_scope(context),
        tool_id="call_fast",
        tool_kind="client",
        name="read_file",
    ))

    assert [projection.indicator for projection in projections] == ["thinking"]
    assert project_turn_surface(coordinator.state).indicator == "thinking"
    assert coordinator.state.lifecycle == "active"
    assert coordinator.state.status_requested
    await coordinator.close()


@pytest.mark.anyio
async def test_coordinator_owns_transport_retry_minimum_visibility() -> None:
    """验证传输层只报告事实，最短可见时间和陈旧 timer 属于 coordinator。"""
    context = _context(surface_id="surface_transport_retry")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(transport_retry_min_visible_sec=0.02),
    )
    await coordinator.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    first = RetryChanged(
        **_scope(context),
        source="transport",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=1,
    )
    await coordinator.emit(first)
    assert projections[-1].indicator == "retrying"

    await coordinator.emit(RetryChanged(
        **_scope(context),
        source="transport",
        state="completed",
        presentation_epoch=1,
        round=1,
        attempt=1,
    ))
    assert coordinator.pending_timer
    await coordinator.emit(RetryChanged(
        **_scope(context),
        source="transport",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=2,
    ))
    await asyncio.sleep(0.03)
    assert projections[-1].indicator == "retrying"

    await coordinator.emit(RetryChanged(
        **_scope(context),
        source="transport",
        state="completed",
        presentation_epoch=1,
        round=1,
        attempt=2,
    ))
    assert projections[-1].indicator == "thinking"
    await coordinator.close()


@pytest.mark.anyio
async def test_tui_transport_retry_uses_typed_surface_until_terminal() -> None:
    """验证真实 TUI 仅根据 typed retry 投影 Retrying 并由终态立即收束。"""
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_retry_frame")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    activity = TurnActivityProjector(context, session.activity)
    await activity.request_model_wait("initial")

    await activity.transport_recovery_changed("reconnecting", 3)
    assert "Retrying" in _activity_text(runtime)
    await activity.transport_recovery_changed("replaying", 3)
    assert runtime.screen.activity_block is None
    await activity.transport_recovery_changed("caught_up", 5)
    assert "Thinking" in _activity_text(runtime)

    await activity.turn_terminal("interrupted")
    assert runtime.screen.activity_block is None
    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_review_activity_uses_wait_family_and_parallel_details() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_review_frame")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    activity = TurnActivityProjector(context, session.activity)

    await activity.approval_review_started(
        "review-1",
        "approval-1",
        "call-1",
        action_summary="run git status",
        presentation_epoch=1,
    )
    await activity.approval_review_started(
        "review-2",
        "approval-2",
        "call-2",
        action_summary="access https://example.com",
        presentation_epoch=1,
    )

    visible = _activity_text(runtime)
    assert "Reviewing 2 approval requests" in visible
    assert "run git status" in visible
    assert "access https://example.com" in visible
    assert "Thinking" not in visible

    await activity.approval_review_completed(
        "review-1",
        "approval-1",
        "call-1",
        action_summary="run git status",
        presentation_epoch=1,
    )
    assert "Reviewing approval request" in _activity_text(runtime)

    await activity.turn_terminal("interrupted")
    assert runtime.screen.activity_block is None
    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_coordinator_joins_timer_when_close_projection_fails() -> None:
    context = _context()

    async def apply(projection: SurfaceProjection) -> None:
        if projection.indicator == "hidden":
            raise RuntimeError("projection close failed")

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(lifecycle_sec=1.0),
    )
    await coordinator.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="lifecycle",
    ))
    assert coordinator.pending_timer

    with pytest.raises(RuntimeError, match="projection close failed"):
        await coordinator.close()

    assert not coordinator.pending_timer


class _LifecyclePort:
    """记录 OutputSession 打开和关闭调用的测试端口。"""

    def __init__(
        self,
        operations: list[str],
        name: str,
        *,
        open_error: Exception | None = None,
        close_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.operations = operations
        self.name = name
        self.open_error = open_error
        self.close_error = close_error
        self.stop_error = stop_error

    async def open(self) -> None:
        self.operations.append(f"{self.name}.open")
        if self.open_error is not None:
            raise self.open_error

    async def close(self) -> None:
        self.operations.append(f"{self.name}.close")
        if self.close_error is not None:
            raise self.close_error

    async def stop(self, *, blink: bool = True) -> None:
        self.operations.append(f"{self.name}.stop:{blink}")
        if self.stop_error is not None:
            raise self.stop_error


@pytest.mark.anyio
async def test_output_session_owns_idempotent_resource_lifecycle() -> None:
    operations: list[str] = []
    activity = _LifecyclePort(operations, "activity")
    control = _LifecyclePort(operations, "control")
    session = OutputSession(
        context=_context(),
        activity=activity,
        control=control,
        content=PassiveOutputActivity(),
        presentation=PassiveOutputActivity(),
    )

    await session.open()
    await session.open()
    await session.close(blink=False)
    await session.close()

    assert operations == [
        "activity.open",
        "control.open",
        "control.stop:False",
        "activity.close",
    ]


@pytest.mark.anyio
async def test_output_session_open_failure_closes_activity_and_stays_unavailable() -> None:
    operations: list[str] = []
    activity = _LifecyclePort(operations, "activity")
    control = _LifecyclePort(
        operations,
        "control",
        open_error=RuntimeError("control open failed"),
    )
    session = OutputSession(
        context=_context(),
        activity=activity,
        control=control,
        content=PassiveOutputActivity(),
        presentation=PassiveOutputActivity(),
    )

    with pytest.raises(RuntimeError, match="control open failed"):
        await session.open()

    assert not session.is_open
    assert operations == [
        "activity.open",
        "control.open",
        "activity.close",
    ]


@pytest.mark.anyio
async def test_terminal_projection_is_recorded_only_after_activity_accepts_it() -> None:
    context = _context(surface_id="surface_terminal_retry")

    class Activity(_LifecyclePort):
        def __init__(self) -> None:
            super().__init__([], "activity")
            self.fail = True

        async def emit(self, event) -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("terminal projection failed")

    activity = Activity()
    projector = TurnActivityProjector(context, activity)

    with pytest.raises(RuntimeError, match="terminal projection failed"):
        await projector.turn_terminal("failed")
    assert projector.terminal_status is None

    await projector.turn_terminal("failed")
    assert projector.terminal_status == "failed"


@pytest.mark.anyio
async def test_output_session_continues_cleanup_after_activity_failure() -> None:
    operations: list[str] = []
    activity = _LifecyclePort(
        operations,
        "activity",
        close_error=RuntimeError("activity close failed"),
    )
    control = _LifecyclePort(operations, "control")
    session = OutputSession(
        context=_context(),
        activity=activity,
        control=control,
        content=PassiveOutputActivity(),
        presentation=PassiveOutputActivity(),
    )

    await session.open()
    with pytest.raises(RuntimeError, match="activity close failed"):
        await session.close()

    assert operations[-2:] == ["control.stop:True", "activity.close"]


@pytest.mark.anyio
async def test_output_session_closes_activity_after_output_failure() -> None:
    operations: list[str] = []
    activity = _LifecyclePort(operations, "activity")
    control = _LifecyclePort(
        operations,
        "control",
        stop_error=RuntimeError("output close failed"),
    )
    session = OutputSession(
        context=_context(),
        activity=activity,
        control=control,
        content=PassiveOutputActivity(),
        presentation=PassiveOutputActivity(),
    )

    await session.open()
    with pytest.raises(RuntimeError, match="output close failed"):
        await session.close()

    assert operations[-2:] == ["control.stop:True", "activity.close"]


@pytest.mark.anyio
async def test_tui_visible_content_atomically_replaces_activity_surface() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context()
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    coordinator = session.activity
    assert isinstance(coordinator, TuiTurnSurfaceCoordinator)
    identity = _identity()

    await runtime.begin_wait_status()
    await session.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    await coordinator.emit(AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_answer",
    ))

    with patch.object(runtime.screen, "_invalidate_now") as invalidate:
        await session.content.emit(AssistantTextDelta(
            "answer\n",
            identity,
            item_id="item_answer",
        ))

    invalidate.assert_called_once_with()
    assert coordinator.state.content == "visible"
    assert runtime.activity.lease("wait") is None
    assert runtime.document.active_kind == "assistant"

    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_tool_approval_and_terminal_leases_restore_parent_surface() -> None:
    """验证正交工具、审批和终端等待不丢失父活动。"""
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_tools")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    await session.open()
    activity = TurnActivityProjector(context, session.activity)

    await activity.request_model_wait("initial")
    assert "Thinking" in _activity_text(runtime)

    await activity.tool_batch_started("batch_1")
    await activity.tool_started("call_1", "client", name="read_file")
    await activity.tool_started("call_2", "nested", name="shell_command")
    await activity.tool_batch_completed("batch_1")
    await asyncio.sleep(0.13)
    assert "Thinking" in _activity_text(runtime)
    assert "shell_command" not in _activity_text(runtime)

    await activity.tool_completed("call_1", "client", name="read_file")
    assert "Thinking" in _activity_text(runtime)

    await activity.approval_started("approval_2", "call_2")
    assert runtime.screen.activity_block is None
    await activity.approval_completed("approval_2", "call_2")
    assert "Thinking" in _activity_text(runtime)

    await activity.terminal_wait_started(
        "poll_1",
        "terminal_1",
        command="python -m pytest -q",
    )
    assert "Waiting for background terminal" in _activity_text(runtime)
    await activity.terminal_wait_completed(
        "poll_1",
        "terminal_1",
        command="python -m pytest -q",
    )
    assert "Thinking" in _activity_text(runtime)

    await activity.tool_completed("call_2", "nested", name="shell_command")
    await activity.request_model_wait("tool_result")
    assert "Thinking" in _activity_text(runtime)

    await session.close()
    assert runtime.screen.activity_block is None
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tool_completion_atomically_hands_surface_to_model_wait() -> None:
    """验证最后一个工具释放时不会提交临时 hidden 帧。"""
    context = _context(surface_id="surface_tool_handoff")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(
            tool_result_sec=0.0,
            lifecycle_sec=0.0,
        ),
    )
    await coordinator.open()
    activity = TurnActivityProjector(context, coordinator)
    await activity.tool_started("call_1", "client", name="read_file")
    await activity.tool_started("call_2", "nested", name="shell_command")
    await activity.tool_completed("call_1", "client", name="read_file")
    projections.clear()

    await activity.tool_completed_and_wait(
        "call_2",
        "nested",
        name="shell_command",
    )

    assert projections == []
    assert project_turn_surface(coordinator.state).indicator == "thinking"
    assert coordinator.state.tools == ()
    assert coordinator.state.model_wait_reason == "tool_result"

    await coordinator.close()


@pytest.mark.anyio
async def test_tool_handoff_preserves_real_tui_activity_lease() -> None:
    """验证原子工具交接不会重建真实 TUI 状态组件。"""
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_tool_handoff_identity")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=False,
    )
    assert isinstance(session.activity, TuiTurnSurfaceCoordinator)
    session.activity.timing = TurnSurfaceTiming(
        tool_result_sec=0.0,
        lifecycle_sec=0.0,
    )
    await session.open()
    activity = TurnActivityProjector(context, session.activity)
    await activity.tool_started("call_1", "nested", name="shell_command")
    lease_before = runtime.activity.lease("wait")
    started_at_before = runtime.activity._wait_started_at
    assert lease_before is not None
    assert started_at_before is not None

    await activity.tool_completed(
        "call_1",
        "nested",
        name="shell_command",
    )
    assert runtime.activity.lease("wait") == lease_before
    assert runtime.activity._wait_started_at == started_at_before

    await activity.tool_started("call_2", "nested", name="shell_command")
    await activity.tool_completed_and_wait(
        "call_2",
        "nested",
        name="shell_command",
    )

    assert runtime.activity.lease("wait") == lease_before
    assert runtime.activity._wait_started_at == started_at_before
    assert "Thinking" in _activity_text(runtime)

    await session.close()
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tool_handoff_batch_rolls_back_when_second_fact_is_invalid() -> None:
    """验证原子批次归约失败时不提交部分状态或视觉帧。"""
    context = _context(surface_id="surface_tool_handoff_rollback")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(
            tool_result_sec=0.0,
            lifecycle_sec=0.0,
        ),
    )
    await coordinator.open()
    activity = TurnActivityProjector(context, coordinator)
    await activity.tool_started("call_1", "nested", name="shell_command")
    state_before = coordinator.state
    projections.clear()

    with pytest.raises(
        ValueError,
        match="tool completion does not match active tool",
    ):
        await coordinator.emit_batch((
            ToolCompleted(
                **_scope(context),
                tool_id="call_1",
                tool_kind="nested",
                name="shell_command",
            ),
            ToolCompleted(
                **_scope(context),
                tool_id="call_missing",
                tool_kind="nested",
                name="shell_command",
            ),
        ))

    assert coordinator.state == state_before
    assert projections == []

    await coordinator.close()


@pytest.mark.anyio
async def test_tool_handoff_batch_is_idempotent_for_duplicate_completion() -> None:
    """验证同批重复完成事实不会重复释放工具或产生额外帧。"""
    context = _context(surface_id="surface_tool_handoff_duplicate")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(
            tool_result_sec=0.0,
            lifecycle_sec=0.0,
        ),
    )
    await coordinator.open()
    await coordinator.emit(ToolStarted(
        **_scope(context),
        tool_id="call_1",
        tool_kind="nested",
        name="shell_command",
    ))
    completed = ToolCompleted(
        **_scope(context),
        tool_id="call_1",
        tool_kind="nested",
        name="shell_command",
    )
    projections.clear()

    await coordinator.emit_batch((
        completed,
        completed,
        ModelWaitRequested(
            **_scope(context),
            revision=1,
            reason="tool_result",
        ),
    ))

    assert projections == []
    assert project_turn_surface(coordinator.state).indicator == "thinking"
    assert len(coordinator.state.completed_tools) == 1

    await coordinator.close()


@pytest.mark.anyio
async def test_activity_batch_rejects_empty_or_wrong_scope_atomically() -> None:
    """验证空批次和错误 scope 都不能改变正式状态。"""
    context = _context(surface_id="surface_batch_scope")
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(context, apply)
    await coordinator.open()
    state_before = coordinator.state

    with pytest.raises(ValueError, match="batch cannot be empty"):
        await coordinator.emit_batch(())
    with pytest.raises(ValueError, match="does not match surface scope"):
        await coordinator.emit_batch((ModelWaitRequested(
            surface_id="surface_other",
            turn_id=context.turn_id,
            revision=1,
            reason="tool_result",
        ),))

    assert coordinator.state == state_before
    assert projections == []

    await coordinator.close()


@pytest.mark.anyio
async def test_tui_unterminated_tail_hides_wait_after_text_done() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_tail")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=True,
    )
    coordinator = session.activity
    assert isinstance(coordinator, TuiTurnSurfaceCoordinator)
    identity = _identity()

    await runtime.begin_wait_status()
    await session.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    await coordinator.emit(AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_tail",
    ))
    await session.content.emit(AssistantTextDelta(
        "tail without newline",
        identity,
        item_id="item_tail",
    ))

    assert runtime.activity.lease("wait") is not None
    assert runtime.document.active_block is None

    await session.content.emit(AssistantSegmentCompleted(
        identity,
        item_id="item_tail",
    ))
    await coordinator.emit(AssistantSettled(
        **_scope(context),
        identity=identity,
        item_id="item_tail",
    ))

    assert runtime.activity.lease("wait") is None
    assert runtime.document.active_kind == "assistant"
    assert not coordinator.pending_timer

    await session.close()
    assert not coordinator.pending_timer
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_output_close_flushes_partial_text_before_surface_closes() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    context = _context(surface_id="surface_partial_close")
    session = create_tui_output_session(
        "",
        context=context,
        runtime=runtime,
        animate=True,
    )
    coordinator = session.activity
    assert isinstance(coordinator, TuiTurnSurfaceCoordinator)
    identity = _identity()

    await runtime.begin_wait_status()
    await session.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="initial",
    ))
    await coordinator.emit(AssistantBuffered(
        **_scope(context),
        identity=identity,
        item_id="item_partial",
    ))
    await session.content.emit(AssistantTextDelta(
        "partial response",
        identity,
        item_id="item_partial",
    ))

    await session.close()

    assert coordinator.state.lifecycle == "closed"
    assert runtime.document.blocks[-1].raw_text == "partial response"
    assert runtime.activity.lease("wait") is None
    runtime.set_execution_active(False)
