"""使用有状态 Fake Server 验证跨协议、输入控制与 TUI 的故障窗口。"""

import asyncio
from types import SimpleNamespace

import pytest

from agent.application.turns.run_result import RunResult
from agent.composition import open_turn_application
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
    RunRecoveryRequired,
)
from agent.protocol import SubmitTurnCommand
from frontends.output.application import NullApplicationSink
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session.turn import execute_tui_model_turn
from frontends.tui.session.turn_input import TuiTurnInputControl
from protocol.schema.stream_events import (
    MarkerEvent,
    TurnCompletedEvent,
)
from tests.support.fake_mind_chat_server import (
    CommandFault,
    FakeEventKind,
    FakeInputState,
    FakeMindChatServer,
    SteerBehavior,
)
from tests.support.turn_scenarios import (
    InterruptInputCase,
    InterruptPhase,
    InterruptScenario,
    InterruptTransport,
    ServerResult,
    TurnPhase,
    TurnScenarioHarness,
    interrupt_scenarios,
)


class _Attachments:
    """提供场景测试所需的空附件状态。"""

    def has_pending_attachments(self) -> bool:
        return False

    def consume_pending_attachments(self) -> list[dict[str, str]]:
        return []

    def replace_pending_attachments(
        self,
        items: tuple[dict[str, str], ...],
    ) -> None:
        return None

    def pending_attachments_snapshot(self) -> tuple[dict[str, str], ...]:
        return ()


class _SessionState:
    """提供场景测试所需的空 prompt extras 状态。"""

    def consume_pending_prompt_extras(self) -> dict[str, str]:
        return {}

    def replace_pending_prompt_extras(
        self,
        extras: dict[str, str],
    ) -> None:
        return None


class _Controller:
    """提供输入控制器捕获附件所需的应用边界。"""

    def __init__(self) -> None:
        self.attach = _Attachments()


async def _wait_for_steer_count(
    server: FakeMindChatServer,
    expected: int,
) -> None:
    """等待 Fake Server 收到指定数量的 steer 请求。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if len(server.steer_requests) >= expected:
            return None
        await asyncio.sleep(0)
    raise AssertionError(
        f"expected {expected} steer requests, got {len(server.steer_requests)}"
    )


async def _never_finishes(started: asyncio.Event) -> None:
    """模拟只会由本地中断取消的活动模型流。"""
    started.set()
    await asyncio.Future()


_INTERRUPT_PHASE_STATUS = {
    InterruptPhase.BEFORE_START: "queued",
    InterruptPhase.THINKING: "running",
    InterruptPhase.ASSISTANT_STREAMING: "running",
    InterruptPhase.TOOL: "waiting_tool",
    InterruptPhase.APPROVAL: "waiting_approval",
    InterruptPhase.PROVIDER_RETRY: "running",
    InterruptPhase.TRANSPORT_RETRY: "running",
    InterruptPhase.REPLAY: "running",
}

_INTERRUPT_FAULT = {
    InterruptTransport.SUCCESS: CommandFault.NORMAL,
    InterruptTransport.RESPONSE_LOST: CommandFault.RESPONSE_LOST,
    InterruptTransport.TIMEOUT: CommandFault.TIMEOUT,
    InterruptTransport.RETRYABLE_ERROR: CommandFault.RETRYABLE_ERROR,
}

_INTERRUPT_INPUTS = {
    InterruptInputCase.ZERO: (),
    InterruptInputCase.ONE_STEER: ("second query",),
    InterruptInputCase.MANY_STEERS: ("second query", "third query"),
    InterruptInputCase.NEXT_TURN_INPUT: ("queued follow up",),
}


def _control(
    server: FakeMindChatServer,
    runtime: TuiRuntime,
) -> TuiTurnInputControl:
    """把真实 TUI 输入控制器绑定到 Fake Server。"""
    assert isinstance(server, ProtocolCommandClient)
    return TuiTurnInputControl(
        _Controller(),
        runtime,
        _SessionState(),
        cid=server.cid,
        sid=server.sid,
        turn_id=server.turn_id,
        protocol_client=server,
    )


def _durable_command(
    *,
    session_id: str,
    run_id: str,
    turn_id: str,
) -> SubmitTurnCommand:
    """构造带远端 Turn 绑定的持久化测试命令。"""
    return SubmitTurnCommand.create(
        session_id=session_id,
        run_id=run_id,
        command_id=f"command-{run_id}",
        idempotency_key=f"intent-{run_id}",
        message=run_id,
        trace_context={
            "remote_turn": {
                "cid": "cid-test",
                "sid": "sid-test",
                "turn_id": turn_id,
            },
        },
    )


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.parametrize(
    "scenario",
    interrupt_scenarios(),
    ids=lambda scenario: scenario.identifier,
)
@pytest.mark.anyio
async def test_production_interrupt_matrix_preserves_gate_and_input_ownership(
    scenario: InterruptScenario,
) -> None:
    """以生产输入控制器覆盖完整中断时机、输入和传输矩阵。"""
    server = FakeMindChatServer(
        interrupt_fault=_INTERRUPT_FAULT[scenario.transport],
    )
    await server.post_mind_chat(
        turn_id="turn-1",
        initial_event_seq=0,
        status=_INTERRUPT_PHASE_STATUS[scenario.phase],
    )
    runtime = TuiRuntime()
    control = _control(server, runtime)
    started = scenario.phase is not InterruptPhase.BEFORE_START
    if started:
        control.handle_event(MarkerEvent(
            type="turn.started",
            turn_id="turn-1",
        ))

    values = _INTERRUPT_INPUTS[scenario.inputs]
    queue_only = scenario.inputs is InterruptInputCase.NEXT_TURN_INPUT
    submissions = tuple(
        TuiSubmission(
            value=value,
            editable_text=value,
            paste_store={},
            client_message_id=f"message-{index}",
        )
        for index, value in enumerate(values, start=1)
    )
    for submission in submissions:
        assert control.submit(submission, queue_only)

    if started and not queue_only:
        await _wait_for_steer_count(server, len(submissions))

    assert control.request_interrupt()
    if not started:
        control.handle_event(MarkerEvent(
            type="turn.started",
            turn_id="turn-1",
        ))

    expected_interrupt_requests = (
        1
        if scenario.transport is InterruptTransport.SUCCESS
        else 2
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while len(server.interrupt_requests) < expected_interrupt_requests:
        if loop.time() >= deadline:
            raise AssertionError("interrupt request did not finish")
        await asyncio.sleep(0)

    assert server.terminal is None
    with pytest.raises(ProtocolCommandError, match="still active"):
        await server.post_mind_chat(turn_id="turn-2", initial_event_seq=0)
    assert server.accepted_interrupt_count == 1
    assert len(set(server.interrupt_requests)) == 1

    server.settle("interrupted", last_event_seq=18)
    control.handle_event(TurnCompletedEvent(
        type="turn.completed",
        turn_id="turn-1",
        event_seq=18,
        status="interrupted",
        last_event_seq=18,
        completed_at=1.0,
    ))
    await control.close()
    runtime.finish_interrupted_presentation()
    restored = runtime.restore_interrupted_submissions()

    assert restored is bool(submissions)
    assert not runtime.submissions.pending_steers.active
    if scenario.inputs in {
        InterruptInputCase.ONE_STEER,
        InterruptInputCase.MANY_STEERS,
    }:
        assert runtime.screen.input.buffer.text == "\n".join(values)
        assert runtime.submissions.message_queue.empty()
    elif scenario.inputs is InterruptInputCase.NEXT_TURN_INPUT:
        assert runtime.screen.input.buffer.text == "queued follow up"
        assert runtime.submissions.message_queue.empty()
    else:
        assert runtime.screen.input.buffer.text == ""
        assert runtime.submissions.message_queue.empty()

    await server.post_mind_chat(turn_id="turn-2", initial_event_seq=18)
    assert server.mind_chat_requests[-1].initial_event_seq == 18
    await runtime.close()


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_escape_interrupt_response_loss_submits_only_sampled_steers(
) -> None:
    """验证 Esc 在中断回执丢失时仍只提交按键时的 pending steer。"""
    server = FakeMindChatServer(
        interrupt_fault=CommandFault.RESPONSE_LOST,
    )
    await server.post_mind_chat(
        turn_id="turn-1",
        initial_event_seq=0,
        status="running",
    )
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    control = _control(server, runtime)
    control.handle_event(MarkerEvent(type="turn.started", turn_id="turn-1"))

    pending = tuple(
        TuiSubmission(
            value=value,
            editable_text=value,
            paste_store={},
            client_message_id=f"message-{index}",
        )
        for index, value in enumerate(("second query", "third query"), start=1)
    )
    for submission in pending:
        assert control.submit(submission, False)
    await _wait_for_steer_count(server, len(pending))

    queued = TuiSubmission(
        value="tab follow up",
        editable_text="tab follow up",
        paste_store={},
        client_message_id="message-tab",
    )
    assert control.submit(queued, True)
    runtime.screen.input.buffer.text = "draft remains editable"

    def interrupt_turn() -> InterruptDisposition:
        assert control.request_interrupt()
        runtime.begin_interrupt_settlement()
        return InterruptDisposition.CONSUMED

    runtime.bind_interrupt_handler(interrupt_turn)
    assert runtime.submissions.interrupt_turn() is InterruptDisposition.CONSUMED

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while len(server.interrupt_requests) < 2:
        if loop.time() >= deadline:
            raise AssertionError("interrupt response-loss retry did not finish")
        await asyncio.sleep(0)

    assert server.accepted_interrupt_count == 1
    assert len(set(server.interrupt_requests)) == 1
    with pytest.raises(ProtocolCommandError, match="still active"):
        await server.post_mind_chat(turn_id="turn-2", initial_event_seq=0)

    server.settle("interrupted", last_event_seq=18)
    control.handle_event(TurnCompletedEvent(
        type="turn.completed",
        turn_id="turn-1",
        event_seq=18,
        status="interrupted",
        last_event_seq=18,
        completed_at=1.0,
    ))
    await control.close()
    runtime.finish_interrupted_presentation()
    assert runtime.restore_interrupted_submissions()

    immediate = await runtime.submissions.read_submission()
    assert immediate.value == "second query\nthird query"
    assert runtime.submissions.queued_messages.active
    assert runtime.screen.input.buffer.text == "draft remains editable"

    await server.post_mind_chat(turn_id="turn-2", initial_event_seq=18)
    assert server.mind_chat_requests[-1].initial_event_seq == 18
    await runtime.close()


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_approval_rejection_interrupt_loss_preserves_gate_and_fifo(
) -> None:
    """复现审批拒绝、回执丢失、排队输入和延迟终态的组合。"""
    server = FakeMindChatServer(
        interrupt_fault=CommandFault.RESPONSE_LOST,
        steer_behavior=SteerBehavior.REJECT,
    )
    await server.post_mind_chat(
        turn_id="turn-1",
        initial_event_seq=0,
        status="waiting_approval",
    )
    runtime = TuiRuntime()
    control = _control(server, runtime)
    control.handle_event(MarkerEvent(type="turn.started", turn_id="turn-1"))
    turn_started = asyncio.Event()

    release_terminal = asyncio.Event()

    async def turn() -> SimpleNamespace:
        turn_started.set()
        await release_terminal.wait()
        control.handle_event(TurnCompletedEvent(
            type="turn.completed",
            turn_id="turn-1",
            event_seq=18,
            status="interrupted",
            last_event_seq=18,
            completed_at=1.0,
        ))
        return SimpleNamespace(status="interrupted")

    execution = asyncio.create_task(execute_tui_model_turn(
        NullApplicationSink(),
        runtime,
        turn(),
        turn_input_control=control,
    ))
    await turn_started.wait()

    buffer = runtime.screen.input.buffer
    for value in ("second query", "third query"):
        buffer.text = value
        buffer.cursor_position = len(value)
        assert runtime.submissions.accept_input(buffer)
    await _wait_for_steer_count(server, 2)
    assert runtime.submissions.rejected_steers.active

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    while len(server.interrupt_requests) < 2:
        await asyncio.sleep(0)

    assert runtime.execution_active
    assert not execution.done()
    assert server.accepted_interrupt_count == 1
    assert len(server.interrupt_requests) == 2
    assert len(set(server.interrupt_requests)) == 1
    assert not server.status_read.is_set()

    server.settle("interrupted", last_event_seq=18)
    release_terminal.set()
    await asyncio.wait_for(execution, timeout=0.2)

    assert runtime.screen.input.buffer.text == "second query\nthird query"
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.rejected_steers.active
    assert not runtime.submissions.queued_messages.active

    snapshot = await server.get_turn_status(
        cid=server.cid,
        sid=server.sid,
        turn_id="turn-1",
    )
    assert snapshot.terminal is not None
    assert snapshot.terminal.status == "interrupted"
    assert snapshot.terminal.last_event_seq == 18

    buffer.validate_and_handle()
    next_submission = await runtime.submissions.read_submission()
    await server.post_mind_chat(
        turn_id="turn-2",
        initial_event_seq=server.client_cursor,
    )

    assert next_submission.value == "second query\nthird query"
    assert server.client_cursor == 18
    assert server.mind_chat_requests[-1].turn_id == "turn-2"
    assert server.mind_chat_requests[-1].initial_event_seq == 18
    assert not runtime.execution_active

    await runtime.close()


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_durable_gate_waits_for_terminal_then_dispatches_fifo(
    tmp_path,
) -> None:
    """验证响应丢失后终态快照是下一轮派发的唯一开门事实。"""
    server = FakeMindChatServer()
    application = open_turn_application(tmp_path / "runtime.db")
    session_id = "session-durable-fault"
    first = _durable_command(
        session_id=session_id,
        run_id="run-first",
        turn_id="turn-1",
    )
    second = _durable_command(
        session_id=session_id,
        run_id="run-second",
        turn_id="turn-2",
    )
    third = _durable_command(
        session_id=session_id,
        run_id="run-third",
        turn_id="turn-3",
    )

    async def response_lost(_command: SubmitTurnCommand) -> RunResult:
        await server.post_mind_chat(
            turn_id="turn-1",
            initial_event_seq=server.client_cursor,
        )
        raise TimeoutError("mind-chat response was lost after acceptance")

    with pytest.raises(TimeoutError):
        await application.submit(first, response_lost)

    dispatched: list[str] = []

    async def complete_remote(command: SubmitTurnCommand) -> RunResult:
        turn_id = (
            "turn-2" if command.run_id == "run-second" else "turn-3"
        )
        dispatched.append(command.run_id)
        await server.post_mind_chat(
            turn_id=turn_id,
            initial_event_seq=server.client_cursor,
        )
        server.settle(
            "completed",
            last_event_seq=server.last_event_seq + 1,
        )
        await server.get_turn_status(
            cid=server.cid,
            sid=server.sid,
            turn_id=turn_id,
        )
        return RunResult(status="completed", assistant_text=turn_id)

    with pytest.raises(RunRecoveryRequired):
        await application.submit(second, complete_remote)
    with pytest.raises(RunRecoveryRequired):
        await application.submit(third, complete_remote)
    assert dispatched == []
    assert [request.turn_id for request in server.mind_chat_requests] == [
        "turn-1",
    ]

    pending = await application.reconcile_remote_session(
        session_id,
        server,
    )
    assert [snapshot.command.run_id for snapshot in pending.pending] == [
        "run-first",
    ]
    assert dispatched == []

    server.settle("interrupted", last_event_seq=18)
    settled = await application.reconcile_remote_session(
        session_id,
        server,
    )
    assert settled.pending == ()

    await application.submit(second, complete_remote)
    await application.submit(third, complete_remote)
    await application.close()

    assert dispatched == ["run-second", "run-third"]
    assert [
        (request.turn_id, request.initial_event_seq)
        for request in server.mind_chat_requests
    ] == [
        ("turn-1", 0),
        ("turn-2", 18),
        ("turn-3", 20),
    ]


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_committed_steer_response_loss_reconciles_without_resubmit() -> None:
    """验证 steer 已提交但回执丢失时只保留一份服务端事实。"""
    server = FakeMindChatServer(
        steer_behavior=SteerBehavior.COMMIT_RESPONSE_LOST,
    )
    await server.post_mind_chat(turn_id="turn-1", initial_event_seq=0)
    runtime = TuiRuntime()
    control = _control(server, runtime)
    control.handle_event(MarkerEvent(type="turn.started", turn_id="turn-1"))
    submission = TuiSubmission(
        value="follow up",
        editable_text="follow up",
        paste_store={},
        client_message_id="message-follow-up",
    )

    assert control.submit(submission, False)
    await _wait_for_steer_count(server, 2)
    control.handle_stream_end("protocol_error")
    await control.close()

    assert len(server.steer_requests) == 2
    assert server.steer_requests[0][0] == server.steer_requests[1][0]
    assert server.input_states == {
        "message-follow-up": FakeInputState.COMMITTED,
    }
    assert not runtime.submissions.pending_steers.active
    assert not runtime.submissions.rejected_steers.active
    assert not runtime.submissions.queued_messages.active

    await runtime.close()


@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_fake_sse_duplicate_gap_and_late_event_feed_same_oracle() -> None:
    """验证 Fake SSE trace 可直接驱动身份、gap 和 cursor 不变量。"""
    server = FakeMindChatServer()
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.REPLAY)
    await server.post_mind_chat(turn_id="turn-1", initial_event_seq=0)
    await server.emit_event(
        FakeEventKind.ASSISTANT_DELTA,
        event_seq=2,
        text="hello",
    )
    await server.emit_event(
        FakeEventKind.ASSISTANT_DELTA,
        event_seq=2,
        text="duplicate",
    )
    await server.emit_event(
        FakeEventKind.ASSISTANT_DELTA,
        event_seq=4,
        text="after gap",
    )
    await server.emit_event(
        FakeEventKind.ASSISTANT_DELTA,
        event_seq=5,
        turn_id="turn-old",
        text="late",
    )
    await server.emit_event(FakeEventKind.STREAM_CLOSED, event_seq=5)

    async for event in server.stream_events():
        harness.observe_event(
            turn_id=event.turn_id,
            epoch=harness.epoch,
            event_seq=event.event_seq,
        )

    server.settle("completed", last_event_seq=6)
    snapshot = await server.get_turn_status(
        cid=server.cid,
        sid=server.sid,
        turn_id="turn-1",
    )
    harness.observe_status(
        ServerResult.COMPLETED,
        last_event_seq=snapshot.last_event_seq,
    )

    assert harness.last_event_seq == 4
    assert harness.cursor == 6
    assert server.client_cursor == 6
    harness.assert_invariants()
