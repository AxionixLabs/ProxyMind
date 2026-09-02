# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.ports import (
    AssistantBuffered,
    AssistantSettled,
    AssistantVisible,
    LogicalSettled,
    ModelWaitRequested,
    OutputSession,
    OutputSurfaceContext,
    PassiveOutputActivity,
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

    state = reduce_turn_surface(state, LogicalSettled(**_scope(context)))
    assert state.logical_settled
    assert reduce_turn_surface(
        state,
        LogicalSettled(**_scope(context)),
    ) is state
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
    assert project_turn_surface(state).indicator == "working"
    assert reduce_turn_surface(state, first_done) is state
    assert reduce_turn_surface(state, first) is state

    with pytest.raises(ValueError, match="identity was reused"):
        reduce_turn_surface(state, ToolStarted(
            **_scope(context),
            tool_id="call_first",
            tool_kind="builtin",
            name="web_search",
        ))


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
    state = reduce_turn_surface(state, RetryChanged(
        **_scope(context),
        source="provider",
        state="started",
        presentation_epoch=1,
        round=1,
        attempt=2,
    ))

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


@pytest.mark.anyio
async def test_coordinator_cancels_stale_generation_and_closes_scope() -> None:
    context = _context()
    projections: list[SurfaceProjection] = []

    async def apply(projection: SurfaceProjection) -> None:
        projections.append(projection)

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(assistant_settled_sec=0.02),
    )
    await coordinator.open()
    assert projections == []
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="assistant_settled",
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
async def test_coordinator_joins_timer_when_close_projection_fails() -> None:
    context = _context()

    async def apply(projection: SurfaceProjection) -> None:
        if projection.indicator == "hidden":
            raise RuntimeError("projection close failed")

    coordinator = TuiTurnSurfaceCoordinator(
        context,
        apply,
        timing=TurnSurfaceTiming(assistant_settled_sec=1.0),
    )
    await coordinator.open()
    await coordinator.emit(ModelWaitRequested(
        **_scope(context),
        revision=1,
        reason="assistant_settled",
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
        close_error: Exception | None = None,
    ) -> None:
        self.operations = operations
        self.name = name
        self.close_error = close_error

    async def open(self) -> None:
        self.operations.append(f"{self.name}.open")

    async def close(self) -> None:
        self.operations.append(f"{self.name}.close")
        if self.close_error is not None:
            raise self.close_error

    async def stop(self, *, blink: bool = True) -> None:
        self.operations.append(f"{self.name}.stop:{blink}")


@pytest.mark.anyio
async def test_output_session_owns_idempotent_resource_lifecycle() -> None:
    operations: list[str] = []
    activity = _LifecyclePort(operations, "activity")
    control = _LifecyclePort(operations, "control")
    session = OutputSession(
        context=_context(),
        activity=activity,
        control=control,
        status=control,
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
        "activity.close",
        "control.stop:False",
    ]


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
        status=control,
        content=PassiveOutputActivity(),
        presentation=PassiveOutputActivity(),
    )

    await session.open()
    with pytest.raises(RuntimeError, match="activity close failed"):
        await session.close()

    assert operations[-2:] == ["activity.close", "control.stop:True"]
