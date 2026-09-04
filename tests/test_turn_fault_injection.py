"""使用有状态 Fake Server 验证跨协议、输入控制与 TUI 的故障窗口。"""

import asyncio

import pytest

from agent.ports import ProtocolCommandClient
from frontends.output.application import NullApplicationSink
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session import turn_input as turn_input_session
from frontends.tui.session.turn import execute_tui_model_turn
from frontends.tui.session.turn_input import TuiTurnInputControl
from protocol.schema.stream_events import MarkerEvent
from tests.support.fake_mind_chat_server import (
    CommandFault,
    FakeEventKind,
    FakeInputState,
    FakeMindChatServer,
    SteerBehavior,
)
from tests.support.turn_scenarios import (
    ServerResult,
    TurnPhase,
    TurnScenarioHarness,
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


@pytest.mark.runtime_p0
@pytest.mark.runtime_fault
@pytest.mark.anyio
async def test_approval_rejection_interrupt_loss_preserves_gate_and_fifo(
    monkeypatch,
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
    monkeypatch.setattr(
        turn_input_session.TuiTurnInputControl,
        "INTERRUPT_STATUS_RETRY_INTERVAL_SEC",
        0.001,
    )
    runtime = TuiRuntime()
    control = _control(server, runtime)
    control.handle_event(MarkerEvent(type="turn.start", turn_id="turn-1"))
    turn_started = asyncio.Event()
    execution = asyncio.create_task(execute_tui_model_turn(
        NullApplicationSink(),
        runtime,
        _never_finishes(turn_started),
        turn_input_control=control,
        on_interrupt_requested=runtime.finish_interrupted_presentation,
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
    await asyncio.wait_for(server.status_read.wait(), timeout=0.2)

    async def begin_next_turn() -> TuiSubmission:
        await execution
        submission = await runtime.submissions.read_submission()
        await server.post_mind_chat(
            turn_id="turn-2",
            initial_event_seq=server.client_cursor,
        )
        return submission

    next_turn = asyncio.create_task(begin_next_turn())
    await asyncio.sleep(0.01)

    assert runtime.execution_active
    assert not execution.done()
    assert not next_turn.done()
    assert server.accepted_interrupt_count == 1
    assert len(server.interrupt_requests) == 2
    assert len(set(server.interrupt_requests)) == 1

    server.settle("interrupted", last_event_seq=18)
    second = await asyncio.wait_for(next_turn, timeout=0.2)
    third = await runtime.submissions.read_submission()

    assert second.value == "second query"
    assert third.value == "third query"
    assert server.client_cursor == 18
    assert server.mind_chat_requests[-1].turn_id == "turn-2"
    assert server.mind_chat_requests[-1].initial_event_seq == 18
    assert not runtime.execution_active
    assert not runtime.submissions.rejected_steers.active

    await runtime.close()


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
    control.handle_event(MarkerEvent(type="turn.start", turn_id="turn-1"))
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
