"""把生产 Turn activity event 和真实 renderer commit 验证为稳定帧契约。"""

import asyncio
import time
from dataclasses import (
    dataclass,
    replace,
)
from unittest.mock import patch

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.output import DummyOutput

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.application.views.builders.tools import (
    build_native_tool_result_view,
    build_tool_start_view,
)
from agent.application.views import ContextCompactionView
from agent.ports import (
    ApprovalPresentationChanged,
    AssistantBuffered,
    AssistantSegmentCompleted,
    AssistantSettled,
    AssistantTextDelta,
    AssistantVisible,
    CompactionActivitySnapshot,
    ContextCompactionChanged,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    SurfaceTurnStarted,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.adapters.application import TuiApplicationSink
from agent.ports.presentation import ApplicationView
from frontends.terminal.renderers.lifecycle import render_compaction_completed
from frontends.tui.core.activity import ActivityLease
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.rendering.fragments import fragments_text
from frontends.tui.runtime.turn_surface import (
    SurfaceProjection,
    TuiTurnSurfaceCoordinator,
    TurnSurfaceState,
    initial_turn_surface_state,
    project_turn_surface,
    reduce_turn_surface,
)
from tests.scenarios.frames import (
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


@dataclass(frozen=True, slots=True)
class _RenderedFrame:
    """记录一次 prompt_toolkit renderer 已提交的可观察帧。"""

    stage: str
    revision: int
    lifecycle: str
    content: str
    indicator: str
    status_visible: bool
    input_row: int
    wait_lease: ActivityLease | None
    wait_started_at: float | None
    transcript_text: str
    text: str


class _RenderedFrameTrace:
    """从 renderer commit 采集状态行、输入锚点和组件身份。"""

    def __init__(
        self,
        runtime: TuiRuntime,
        coordinator: TuiTurnSurfaceCoordinator,
        *,
        terminal_rows: int,
    ) -> None:
        self.runtime = runtime
        self.coordinator = coordinator
        self.terminal_rows = terminal_rows
        self.stage = "initial"
        self.frames: list[_RenderedFrame] = []

    def capture(self, _application: Application[None]) -> None:
        """采集 renderer 完成当前 commit 后的完整可见事实。"""
        screen = self.runtime.screen.application.renderer.last_rendered_screen
        positions = screen.visible_windows_to_write_positions
        input_position = positions.get(self.runtime.screen.input.window)
        if input_position is None:
            return None
        status_position = positions.get(self.runtime.screen.status_window)
        transcript_position = positions.get(
            self.runtime.screen.transcript_window
        )
        projection = project_turn_surface(self.coordinator.state)
        self.frames.append(_RenderedFrame(
            stage=self.stage,
            revision=self.runtime.screen.application.render_counter,
            lifecycle=self.coordinator.state.lifecycle,
            content=self.coordinator.state.content,
            indicator=projection.indicator,
            status_visible=(
                status_position is not None and status_position.height > 0
            ),
            input_row=(
                self.terminal_rows
                - self.runtime.screen._visible_height()
                + input_position.ypos
            ),
            wait_lease=self.runtime.activity.lease("wait"),
            wait_started_at=self.runtime.activity._wait_started_at,
            transcript_text=_rendered_window_text(
                screen,
                transcript_position,
            ),
            text=_rendered_screen_text(screen),
        ))

    def assert_contract(self) -> None:
        """检查实际帧的互斥、终态收束和输入框几何不变量。"""
        assert self.frames
        assert len({frame.input_row for frame in self.frames}) == 1
        revisions = tuple(frame.revision for frame in self.frames)
        assert all(
            current > previous
            for previous, current in zip(revisions, revisions[1:])
        )

        terminal_seen = False
        for frame in self.frames:
            if frame.content == "visible":
                assert not frame.status_visible
            if frame.lifecycle == "active":
                assert frame.status_visible or frame.transcript_text.strip()
            if frame.status_visible:
                assert frame.indicator != "hidden"
                assert frame.wait_lease is not None
                assert frame.wait_started_at is not None
            if frame.lifecycle == "terminal":
                terminal_seen = True
                assert not frame.status_visible
            elif terminal_seen:
                raise AssertionError("activity returned after terminal frame")


def _rendered_window_text(
    screen: Screen,
    position: WritePosition | None,
) -> str:
    """返回指定真实 Window 区域内的可见纯文本。"""
    if position is None:
        return ""
    return "\n".join(
        "".join(
            cells[column].char
            for column in sorted(cells)
        ).rstrip()
        for row, cells in sorted(screen.data_buffer.items())
        if position.ypos <= row < position.ypos + position.height
    )


def _rendered_screen_text(screen: Screen) -> str:
    """返回一帧真实 Screen 数据中的可见纯文本。"""
    return "\n".join(
        "".join(
            cells[column].char
            for column in sorted(cells)
        ).rstrip()
        for _row, cells in sorted(screen.data_buffer.items())
    )


async def _render_next_frame(runtime: TuiRuntime) -> Screen:
    """请求并等待 prompt_toolkit renderer 提交下一帧。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(100):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("TUI frame was not rendered")


async def _wait_for_screen_text(runtime: TuiRuntime, expected: str) -> None:
    """等待动画正文通过正式 renderer 出现在可见帧。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while loop.time() < deadline:
        screen = await _render_next_frame(runtime)
        if expected in _rendered_screen_text(screen):
            return None
        await asyncio.sleep(0.005)
    raise AssertionError(f"TUI text was not rendered: {expected}")


@pytest.mark.anyio
@pytest.mark.runtime_frame
@pytest.mark.parametrize("columns", (50, 120))
@pytest.mark.parametrize("terminals", (0, 1, 2))
@pytest.mark.parametrize(("manual", "continuation"), ((False, False), (False, True), (True, False)))
async def test_compaction_completion_replaces_activity_in_same_renderer_row(columns, terminals, manual, continuation) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
        frames = []

        def capture(_application):
            screen = runtime.screen.application.renderer.last_rendered_screen
            rows = [
                (18 - runtime.screen._visible_height() + row, "".join(cells[column].char for column in sorted(cells)))
                for row, cells in sorted(screen.data_buffer.items())
            ]
            frames.append(rows)

        with patch.object(runtime.screen.application.output, "get_size", return_value=Size(rows=18, columns=columns)):
            await runtime.open()
            try:
                runtime.set_execution_active(not manual)
                runtime.set_foreground_active(manual)
                if terminals:
                    runtime.set_process_status_label(
                        f"{terminals} background terminal{'s' if terminals != 1 else ''} running · /ps to view · /stop to close",
                    )
                if manual:
                    progress = CompactionActivitySnapshot(time.perf_counter(), False, "Context compacting")
                    await runtime.begin_compact_status(lambda: progress)
                else:
                    await session.open()
                    if continuation:
                        await session.activity.emit(ModelWaitRequested(**_scope(), revision=1, reason="initial"))
                    await session.activity.emit(ContextCompactionChanged(
                        **_scope(), item_id="compact_1", status="started", event_seq=1, presentation_epoch=1,
                    ))
                await _wait_for_screen_text(runtime, "Context compacting")
                runtime.screen.application.after_render += capture
                await _render_next_frame(runtime)
                if manual:
                    TuiApplicationSink(runtime).emit(ApplicationView(
                        type="tui.compact.status", renderable=render_compaction_completed(86420),
                    ))
                    assert not runtime.execution_active
                    assert runtime.submission_deferred
                else:
                    await session.presentation.emit(ContextCompactionView(
                        turn_id=CONTEXT.turn_id, item_id="compact_1", event_seq=2,
                        presentation_epoch=1, status="completed", phase="mid_turn",
                        trigger="automatic", reason="context_limit", latency_ms=86420,
                    ))
                    await session.activity.emit(ContextCompactionChanged(
                        **_scope(), item_id="compact_1", status="completed", event_seq=2, presentation_epoch=1,
                    ))
                    if continuation:
                        await session.activity.emit(ModelWaitRequested(**_scope(), revision=2, reason="lifecycle"))
                await _wait_for_screen_text(runtime, "Context compacted")
                matching = [
                    [(row, text) for row, text in frame if "Context compact" in text]
                    for frame in frames
                ]
                assert all(len(frame) == 1 for frame in matching), matching
                assert len({frame[0][0] for frame in matching}) == 1, matching
                assert "1m26s" in matching[-1][0][1]
                assert all("Making room" not in text for _row, text in frames[-1])
            finally:
                runtime.screen.application.after_render -= capture
                runtime.set_execution_active(False)
                runtime.set_foreground_active(False)
                await session.close()
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.runtime_frame
async def test_compaction_pending_commit_resize_and_consecutive_items_preserve_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
        with patch.object(runtime.screen.application.output, "get_size", return_value=Size(rows=24, columns=120)) as size:
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                await session.open()
                draft = runtime.screen.input.buffer
                draft.text = "继续核对中文草稿\n第二行暂不发送"
                draft.cursor_position = 4
                draft.start_selection()
                draft.cursor_position = 8
                selection = draft.selection_state
                focus = runtime.screen.application.layout.current_control
                await session.activity.emit(ContextCompactionChanged(
                    **_scope(), item_id="compact_1", status="started", event_seq=1, presentation_epoch=1,
                ))
                for columns in (120, 80, 50, 25, 120):
                    size.return_value = Size(rows=24, columns=columns)
                    for _ in range(2):
                        await _render_next_frame(runtime)
                    assert draft.text == "继续核对中文草稿\n第二行暂不发送"
                    assert draft.cursor_position == 8
                    assert draft.selection_state is selection
                    assert runtime.screen.application.layout.current_control is focus
                completed = ContextCompactionView(
                    turn_id=CONTEXT.turn_id, item_id="compact_1", event_seq=2,
                    presentation_epoch=1, status="completed", phase="mid_turn",
                    trigger="automatic", reason="context_limit", latency_ms=86420,
                )
                entered, release = asyncio.Event(), asyncio.Event()
                original = session.control.prepare_active_presentation

                async def prepare():
                    entered.set()
                    await release.wait()
                    await original()

                with patch.object(session.control, "prepare_active_presentation", side_effect=prepare):
                    pending = asyncio.create_task(session.presentation.emit(completed))
                    await entered.wait()
                    frame = await _render_next_frame(runtime)
                    assert "Context compacting" in _rendered_screen_text(frame)
                    assert "Context compacted" not in _rendered_screen_text(frame)
                    release.set()
                    await pending
                await session.activity.emit(ContextCompactionChanged(
                    **_scope(), item_id="compact_1", status="completed", event_seq=2, presentation_epoch=1,
                ))
                await session.activity.emit(ModelWaitRequested(**_scope(), revision=1, reason="lifecycle"))
                await _wait_for_screen_text(runtime, "Thinking")
                await session.activity.emit(ContextCompactionChanged(
                    **_scope(), item_id="compact_2", status="started", event_seq=3, presentation_epoch=1,
                ))
                await session.presentation.emit(completed)
                assert runtime.activity.compaction_matches("compact_2", 1)
                await session.presentation.emit(replace(completed, item_id="compact_2", event_seq=4, latency_ms=1000))
                await session.activity.emit(ContextCompactionChanged(
                    **_scope(), item_id="compact_2", status="completed", event_seq=4, presentation_epoch=1,
                ))
                frame = await _render_next_frame(runtime)
                text = _rendered_screen_text(frame)
                assert text.count("Context compacted") == 1
                completed_cells = [cell for cell in runtime.document.blocks if isinstance(cell.source, ContextCompactionView)]
                assert [cell.source.item_id for cell in completed_cells] == ["compact_1", "compact_2"]
                assert "Context compacting" not in text
                assert runtime.execution_active
                assert draft.text == "继续核对中文草稿\n第二行暂不发送"
            finally:
                runtime.set_execution_active(False)
                await session.close()
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("started", (False, True))
async def test_compaction_terminal_without_animation_or_after_turn_interruption(started) -> None:
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    runtime.set_execution_active(True)
    try:
        await session.open()
        if started:
            await session.activity.emit(ContextCompactionChanged(
                **_scope(), item_id="compact_1", status="started", event_seq=1, presentation_epoch=1,
            ))
            await session.activity.emit(TurnTerminal(**_scope(), status="interrupted"))
            assert fragments_text(runtime.document.blocks[-1].display_block.fragments) == "• Context compaction · interrupted"
        else:
            await session.presentation.emit(ContextCompactionView(
                turn_id=CONTEXT.turn_id, item_id="compact_1", event_seq=2,
                presentation_epoch=1, status="completed", phase="mid_turn",
                trigger="automatic", reason="context_limit", latency_ms=0,
            ))
        assert runtime.screen.activity_block is None
        assert len(runtime.document.blocks) == 1
    finally:
        runtime.set_execution_active(False)
        await session.close()
        await runtime.close()


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
    if state.approval_presentation_active:
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
        ApprovalPresentationChanged(
            **_scope(),
            active=True,
        ),
        ApprovalPresentationChanged(
            **_scope(),
            active=False,
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
        elif isinstance(event, ApprovalPresentationChanged) and not event.active:
            kind = FrameKind.APPROVAL_COMPLETED
        elif isinstance(event, TurnTerminal):
            kind = FrameKind.TERMINAL
        trace.append(LogicalFrame(
            turn_id=CONTEXT.turn_id,
            indicator=_indicator(state, projection),
            assistant_text=active_assistant,
            kind=kind,
            tool_leases=len(state.tools),
            approval_leases=int(state.approval_presentation_active),
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


@pytest.mark.anyio
@pytest.mark.runtime_p0
@pytest.mark.runtime_frame
@pytest.mark.parametrize("animate", (False, True))
@pytest.mark.parametrize("columns", (24, 60))
async def test_real_renderer_preserves_shell_handoff_frame_contract(
    animate: bool,
    columns: int,
) -> None:
    """验证 commentary、Shell、最终正文和终态的真实帧提交契约。"""
    terminal_rows = 18
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        session = create_tui_output_session(
            "",
            context=CONTEXT,
            runtime=runtime,
            animate=animate,
        )
        coordinator = session.activity
        assert isinstance(coordinator, TuiTurnSurfaceCoordinator)
        activity = TurnActivityProjector(CONTEXT, coordinator)
        trace = _RenderedFrameTrace(
            runtime,
            coordinator,
            terminal_rows=terminal_rows,
        )
        trace_registered = False

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=columns),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                await session.open()
                await activity.request_model_wait("initial")
                waiting_screen = await _render_next_frame(runtime)
                waiting_position = (
                    waiting_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                waiting_input_row = (
                    terminal_rows
                    - runtime.screen._visible_height()
                    + waiting_position.ypos
                )

                runtime.screen.application.after_render += trace.capture
                trace_registered = True

                trace.stage = "commentary_stream"
                await activity.assistant_buffered(
                    IDENTITY,
                    "item-commentary",
                    phase="commentary",
                )
                await session.content.emit(AssistantTextDelta(
                    "Commentary.\n",
                    IDENTITY,
                    item_id="item-commentary",
                    phase="commentary",
                ))
                await _wait_for_screen_text(runtime, "Commentary")

                trace.stage = "commentary_settled"
                await session.content.emit(AssistantSegmentCompleted(
                    IDENTITY,
                    item_id="item-commentary",
                    phase="commentary",
                ))
                await activity.assistant_settled(
                    IDENTITY,
                    "item-commentary",
                    phase="commentary",
                )
                await _render_next_frame(runtime)
                wait_lease = runtime.activity.lease("wait")
                wait_started_at = runtime.activity._wait_started_at
                assert wait_lease is not None
                assert wait_started_at is not None

                trace.stage = "shell_running"
                await activity.tool_started(
                    "call-shell",
                    "client",
                    name="shell_command",
                )
                await session.presentation.emit(build_tool_start_view(
                    "shell_command",
                    {"command": "echo ready"},
                    call_id="call-shell",
                ))
                await _wait_for_screen_text(runtime, "echo ready")

                trace.stage = "shell_completed"
                await session.presentation.emit(build_native_tool_result_view(
                    "shell_command",
                    {"command": "echo ready"},
                    ok=True,
                    data={
                        "command": "echo ready",
                        "output_lines": ["ready"],
                    },
                    call_id="call-shell",
                ))
                await activity.tool_completed_and_wait(
                    "call-shell",
                    "client",
                    name="shell_command",
                )
                await _render_next_frame(runtime)

                shell_frames = tuple(
                    frame
                    for frame in trace.frames
                    if frame.stage in {"shell_running", "shell_completed"}
                )
                assert shell_frames
                assert all(frame.status_visible for frame in shell_frames)
                assert all(
                    frame.wait_lease == wait_lease
                    and frame.wait_started_at == wait_started_at
                    for frame in shell_frames
                )

                trace.stage = "final_stream"
                await activity.assistant_buffered(
                    IDENTITY,
                    "item-final",
                    phase="final_answer",
                )
                await session.content.emit(AssistantTextDelta(
                    "Final OK.\n",
                    IDENTITY,
                    item_id="item-final",
                    phase="final_answer",
                ))
                await _wait_for_screen_text(runtime, "Final OK")

                trace.stage = "final_settled"
                await session.content.emit(AssistantSegmentCompleted(
                    IDENTITY,
                    item_id="item-final",
                    phase="final_answer",
                ))
                await activity.assistant_settled(
                    IDENTITY,
                    "item-final",
                    phase="final_answer",
                )
                await _render_next_frame(runtime)

                trace.stage = "terminal"
                await activity.turn_terminal("completed")
                await _render_next_frame(runtime)

                trace.stage = "late_activity"
                await activity.tool_started(
                    "call-late",
                    "client",
                    name="shell_command",
                )
                await _render_next_frame(runtime)

                trace.assert_contract()
                assert all(
                    frame.input_row == waiting_input_row
                    for frame in trace.frames
                )
                final_frames = tuple(
                    frame
                    for frame in trace.frames
                    if frame.stage in {
                        "final_stream",
                        "final_settled",
                        "terminal",
                        "late_activity",
                    }
                )
                assert final_frames
                assert all(not frame.status_visible for frame in final_frames), [
                    (frame.stage, frame.content, frame.indicator, frame.status_visible)
                    for frame in final_frames
                ]
                assert "Final OK" in final_frames[-1].text
            finally:
                if trace_registered:
                    runtime.screen.application.after_render -= trace.capture
                runtime.set_execution_active(False)
                await session.close()
                await runtime.close()


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


@pytest.mark.runtime_p0
@pytest.mark.runtime_frame
def test_terminal_surface_ignores_late_content_and_activity() -> None:
    """验证终态后的迟到正文、工具和审批不能复活生产 Surface。"""
    state = initial_turn_surface_state(CONTEXT)
    state = reduce_turn_surface(state, SurfaceTurnStarted(**_scope()))
    state = reduce_turn_surface(state, TurnTerminal(
        **_scope(),
        status="interrupted",
    ))
    terminal_state = state

    late_events = (
        AssistantBuffered(
            **_scope(),
            identity=IDENTITY,
            item_id="late-answer",
        ),
        AssistantVisible(
            **_scope(),
            identity=IDENTITY,
            item_id="late-answer",
        ),
        ToolStarted(
            **_scope(),
            tool_id="late-tool",
            tool_kind="client",
            name="network",
        ),
        ApprovalPresentationChanged(
            **_scope(),
            active=True,
        ),
        ModelWaitRequested(**_scope(), revision=99, reason="lifecycle"),
    )
    for event in late_events:
        state = reduce_turn_surface(state, event)
        assert state is terminal_state

    projection = project_turn_surface(state)
    assert projection.indicator == "hidden"
    assert state.tools == ()
    assert not state.approval_presentation_active


@pytest.mark.runtime_frame
def test_frame_contract_rejects_hidden_content_after_terminal() -> None:
    """验证逻辑帧门禁不会遗漏无动画的终态后正文复活。"""
    trace = FrameTrace()
    trace.append(LogicalFrame(
        turn_id="turn-1",
        indicator=FrameIndicator.HIDDEN,
        kind=FrameKind.TERMINAL,
    ))

    with pytest.raises(RuntimeInvariantError, match="after terminal"):
        trace.append(LogicalFrame(
            turn_id="turn-1",
            indicator=FrameIndicator.HIDDEN,
            assistant_text="late answer",
        ))
