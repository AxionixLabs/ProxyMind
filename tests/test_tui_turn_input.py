# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
)
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.core.styles import query_block, text_block
from frontends.tui.session import loop as loop_session
from frontends.tui.session import turn_input as turn_input_session
from frontends.tui.session.turn import execute_tui_model_turn
from frontends.tui.session.turn_input import TuiTurnInputControl
from protocol.schema.stream_events import (
    MarkerEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
)
from protocol.schema.turn_inputs import TurnInput


@pytest.fixture
def protocol_client() -> ProtocolCommandClient:
    """返回只记录 TUI 轮次控制命令的协议端口。"""
    async def reconcile(**kwargs):
        client_message_ids = tuple(kwargs["client_message_ids"])
        return SimpleNamespace(
            committed_ids=client_message_ids,
            pending_ids=(),
            retry_ids=(),
            unknown_ids=(),
        )

    client = Mock(spec=ProtocolCommandClient)
    client.steer_turn = AsyncMock(
        return_value=SimpleNamespace(status="accepted")
    )
    client.interrupt_turn = AsyncMock(
        return_value=SimpleNamespace(status="accepted")
    )
    client.get_turn_status = AsyncMock(return_value=SimpleNamespace(
        status="interrupted",
        terminal=True,
    ))
    client.reconcile_turn_inputs = AsyncMock(side_effect=reconcile)
    return client


class _Attachments(object):
    def __init__(self, items=()) -> None:
        self.items = list(items)

    def has_pending_attachments(self) -> bool:
        return bool(self.items)

    def consume_pending_attachments(self):
        items = list(self.items)
        self.items.clear()
        return items

    def replace_pending_attachments(self, items) -> None:
        self.items = list(items)


class _State(object):
    def __init__(self, extras=None) -> None:
        self.extras = dict(extras or {})

    def consume_pending_prompt_extras(self):
        extras = dict(self.extras)
        self.extras.clear()
        return extras

    def replace_pending_prompt_extras(self, extras) -> None:
        self.extras = dict(extras)


def _submission(text: str) -> TuiSubmission:
    return TuiSubmission(
        value=text,
        editable_text=text,
        paste_store={},
        client_message_id=f"message_{text.replace(' ', '_')}",
    )


def _mark_started(
    control: TuiTurnInputControl,
    turn_id: str = "turn_001",
) -> None:
    control.handle_event(MarkerEvent(type="turn.start", turn_id=turn_id))


@pytest.mark.anyio
async def test_steer_requests_wait_for_matching_turn_start(
    protocol_client: ProtocolCommandClient,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    interrupt = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    protocol_client.steer_turn = steer
    protocol_client.interrupt_turn = interrupt
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    assert control.submit(_submission("too early"), False)
    assert runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert not runtime.submissions.rollback_queued_input()
    steer.assert_not_awaited()

    assert control.submit(_submission("queue early"), True)
    assert runtime.submissions.rollback_queued_input()
    interrupt.assert_not_awaited()

    _mark_started(control, "turn_other")
    await asyncio.sleep(0)
    steer.assert_not_awaited()

    _mark_started(control)
    assert control.submit(_submission("ready"), False)
    while steer.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    assert [
        call.kwargs["turn_input"].text
        for call in steer.await_args_list
    ] == ["too early", "ready"]
    interrupt.assert_not_awaited()


@pytest.mark.anyio
async def test_continuation_activation_preserves_local_steer_fifo(
    protocol_client: ProtocolCommandClient,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    protocol_client.steer_turn = steer
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )

    assert control.submit(_submission("first local"), False)
    assert control.submit(_submission("second local"), False)
    control.activate(SimpleNamespace(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_002",
    ))
    _mark_started(control, "turn_002")
    while steer.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    assert [
        call.kwargs["turn_input"].client_message_id
        for call in steer.await_args_list
    ] == ["message_first_local", "message_second_local"]


@pytest.mark.anyio
async def test_unsettled_continuation_retains_sent_steer_as_uncertain(
    protocol_client: ProtocolCommandClient,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    protocol_client.steer_turn = steer
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("old turn")

    assert control.submit(submission, False)
    while steer.await_count < 1:
        await asyncio.sleep(0)
    control.activate(SimpleNamespace(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_002",
    ))

    assert runtime.uncertain_steers_active
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == submission.editable_text
    await control.close()


@pytest.mark.anyio
async def test_immediate_input_is_sent_and_late_settlement_precedes_tab_queue(
    protocol_client: ProtocolCommandClient,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    protocol_client.steer_turn = steer

    runtime = TuiRuntime()
    attachments = _Attachments([{"kind": "image", "name": "screen.png"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    assert control.submit(_submission("steer now"), False)
    assert control.submit(_submission("tab follow up"), True)
    await asyncio.sleep(0)

    assert runtime.submissions.pending_steers.active
    assert runtime.submissions.queued_messages.active

    sent = steer.await_args.kwargs["turn_input"]
    assert sent.client_message_id == "message_steer_now"
    assert sent.attachments == ({"kind": "image", "name": "screen.png"},)
    assert sent.extras == {"source": "selection"}

    control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_001",
        next_input=TurnInput(
            client_message_id="message_steer_now",
            text="steer now",
            attachments=tuple(dict(item) for item in sent.attachments),
            extras=dict(sent.extras),
        ),
    ))

    assert not runtime.submissions.pending_steers.active
    assert runtime.submissions.rejected_steers.active
    assert "steer now" in "".join(
        text for _style, text in runtime.screen._queued_fragments(width=80)
    )
    await control.close()

    first = await runtime.submissions.read_submission()
    assert first.value == "steer now"
    assert first.attachments == sent.attachments
    assert first.extras == sent.extras
    queued = await runtime.submissions.read_submission()
    assert queued.client_message_id == "message_tab_follow_up"
    assert queued.value == "tab follow up"
    assert queued.attachments == ()
    assert queued.extras == {}


@pytest.mark.anyio
async def test_settled_enter_can_be_restored_after_server_rejection(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("continue next")

    assert control.submit(submission, False)
    await asyncio.sleep(0)
    control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_001",
        next_input=TurnInput(
            client_message_id=submission.client_message_id,
            text=submission.value,
        ),
    ))

    assert not runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.rejected_steers.active
    queue_text = "".join(
        text for _style, text in runtime.screen._queued_fragments(width=80)
    )
    assert "Messages to be submitted at end of turn" in queue_text
    assert submission.visible_text in queue_text
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == submission.editable_text
    assert not runtime.submissions.rejected_steers.active

    await control.close()


@pytest.mark.anyio
async def test_empty_settlement_keeps_local_tab_queue_fifo(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )

    assert control.submit(_submission("first local"), True)
    assert control.submit(_submission("second local"), True)
    assert control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_001",
        next_input=None,
    )) is None

    first = await runtime.submissions.read_submission()
    second = await runtime.submissions.read_submission()
    assert first.value == "first local"
    assert second.value == "second local"


def test_tab_queue_captures_payload_and_restores_editable_draft(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    attachments = _Attachments([{"kind": "image", "name": "screen.png"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    runtime.bind_queued_restore_handler(control.restore_draft)

    assert control.submit(_submission("edit locally"), True)
    assert not attachments.has_pending_attachments()
    assert state.consume_pending_prompt_extras() == {}
    assert runtime.submissions.rollback_queued_input()

    assert runtime.screen.input.buffer.text == "edit locally"
    assert attachments.consume_pending_attachments() == [
        {"kind": "image", "name": "screen.png"}
    ]
    assert state.consume_pending_prompt_extras() == {"source": "selection"}


@pytest.mark.anyio
async def test_model_turn_binds_queued_draft_restore_for_its_lifetime() -> None:
    runtime = TuiRuntime()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def turn() -> None:
        started.set()
        await finished.wait()

    control = SimpleNamespace(
        submit=Mock(return_value=True),
        restore_draft=Mock(),
        interrupt=Mock(),
        close=AsyncMock(),
    )
    task = asyncio.create_task(execute_tui_model_turn(
        SimpleNamespace(emit=Mock()),
        runtime,
        turn(),
        turn_input_control=control,
    ))
    await started.wait()

    runtime.defer_submission(_submission("during turn"))
    assert runtime.submissions.rollback_queued_input()
    control.restore_draft.assert_called_once()

    finished.set()
    await task

    runtime.defer_submission(_submission("after turn"))
    assert runtime.submissions.rollback_queued_input()
    control.restore_draft.assert_called_once()
    control.close.assert_awaited_once()


@pytest.mark.anyio
async def test_model_turn_keeps_restore_handler_for_uncertain_payload() -> None:
    runtime = TuiRuntime()
    submission = TuiSubmission(
        value="uncertain",
        editable_text="uncertain",
        paste_store={},
        client_message_id="message_uncertain",
        attachments=({"kind": "image"},),
        extras={"source": "selection"},
        payload_bound=True,
    )

    async def close() -> None:
        runtime.retain_uncertain_steer(submission)

    control = SimpleNamespace(
        submit=Mock(return_value=True),
        restore_draft=Mock(),
        interrupt=Mock(),
        close=close,
    )

    await execute_tui_model_turn(
        SimpleNamespace(emit=Mock()),
        runtime,
        asyncio.sleep(0),
        turn_input_control=control,
    )

    assert runtime.submissions.rollback_queued_input()
    control.restore_draft.assert_called_once_with(submission)


@pytest.mark.anyio
async def test_not_steerable_input_falls_back_to_local_next_turn(
    protocol_client: ProtocolCommandClient,
) -> None:
    protocol_client.steer_turn = AsyncMock(
        return_value=SimpleNamespace(status="turn_not_steerable")
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("retry next")

    assert control.submit(submission, False)
    await control.close()

    assert not runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.rejected_steers.active
    assert submission.visible_text in "".join(
        text for _style, text in runtime.screen._queued_fragments(width=80)
    )
    assert runtime.submissions.can_rollback_queued_input

    queued = await runtime.submissions.read_submission()
    assert queued.client_message_id == submission.client_message_id


@pytest.mark.anyio
async def test_sampling_acceptance_removes_immediate_input_from_next_turn(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("accepted steer")

    assert control.submit(submission, False)
    await asyncio.sleep(0)
    assert runtime.submissions.pending_steers.active
    accepted = control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id="turn_001",
        client_message_id=submission.client_message_id,
    ))

    assert accepted == TurnInput(
        client_message_id=submission.client_message_id,
        text="accepted steer",
    )
    assert not runtime.submissions.pending_steers.active
    await control.close()
    assert not runtime.submissions.queued_messages.active
    assert runtime.document.blocks[-1].kind == "user"
    assert runtime.document.blocks[-1].turn_id == "turn_001"
    assert runtime.document.blocks[-1].prompt == "accepted steer"


@pytest.mark.anyio
async def test_sampling_acceptance_binds_input_behind_active_tool(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(query_block("original request"), kind="user")
    assert runtime.bind_submitted_turn("turn_001", "original request")
    runtime.set_active_renderable(
        text_block("Running long shell command"),
        kind="operation",
    )
    attachments = _Attachments([{"kind": "image", "name": "screen.png"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("accepted during tool")

    assert control.submit(submission, False)
    await asyncio.sleep(0)

    accepted = control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id="turn_001",
        client_message_id=submission.client_message_id,
    ))

    assert accepted == TurnInput(
        client_message_id=submission.client_message_id,
        text=submission.value,
        attachments=({"kind": "image", "name": "screen.png"},),
        extras={"source": "selection"},
    )
    assert not runtime.submissions.pending_steers.active
    live_tail = runtime.document.transcript_snapshot().live_tail
    assert live_tail is not None
    accepted_cell = live_tail.cells[-1]
    assert accepted_cell.kind == "user"
    assert accepted_cell.turn_id == "turn_001"
    assert accepted_cell.prompt == submission.value
    assert accepted_cell.attachments == accepted.attachments
    assert accepted_cell.extras == accepted.extras
    assert submission.value in fragments_text(
        runtime.screen.transcript_fragments()
    )

    runtime.commit_active_renderable(text_block("Completed long shell command"))
    assert runtime.document.blocks[-1] == accepted_cell
    await control.close()


@pytest.mark.anyio
async def test_response_loss_stays_pending_until_late_acceptance(
    protocol_client: ProtocolCommandClient,
) -> None:
    steer = AsyncMock(side_effect=[
        ProtocolCommandError("response_lost", "response lost", retryable=True),
        ProtocolCommandError("response_lost", "response lost", retryable=True),
    ])
    protocol_client.steer_turn = steer
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("accepted despite response loss")

    assert control.submit(submission, False)
    while steer.await_count < 2:
        await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert runtime.submissions.pending_steers.active
    assert not runtime.submissions.can_rollback_queued_input

    accepted = control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id="turn_001",
        client_message_id=submission.client_message_id,
    ))

    assert accepted == TurnInput(
        client_message_id=submission.client_message_id,
        text=submission.value,
    )
    assert runtime.discard_rejected_steer(submission.client_message_id) is None
    assert runtime.document.blocks[-1].prompt == submission.value
    await control.close()


@pytest.mark.anyio
async def test_closing_turn_clears_unsettled_steer_display(
    protocol_client: ProtocolCommandClient,
) -> None:
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    assert control.submit(_submission("unsettled"), False)
    assert runtime.submissions.pending_steers.active

    await control.close()

    assert not runtime.submissions.pending_steers.active


@pytest.mark.anyio
async def test_settlement_cancels_unfinished_steer_without_local_retry(
    protocol_client: ProtocolCommandClient,
) -> None:
    request_started = asyncio.Event()

    async def steer(**_kwargs):
        request_started.set()
        await asyncio.Future()

    protocol_client.steer_turn = AsyncMock(side_effect=steer)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("in flight")

    assert control.submit(submission, False)
    await request_started.wait()
    control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_001",
        next_input=None,
    ))

    await asyncio.wait_for(control.close(), timeout=1.0)

    assert not runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert not runtime.submissions.can_rollback_queued_input


@pytest.mark.anyio
async def test_unconfirmed_sent_steer_is_retried_with_original_payload(
    protocol_client: ProtocolCommandClient,
) -> None:
    async def retry(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=tuple(kwargs["client_message_ids"]),
            unknown_ids=(),
        )

    protocol_client.reconcile_turn_inputs = AsyncMock(side_effect=retry)
    runtime = TuiRuntime()
    attachments = _Attachments([{"kind": "image", "name": "screen.png"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    submission = _submission("retry safely")

    assert control.submit(submission, False)
    await asyncio.sleep(0)
    control.handle_stream_end("fatal")
    await control.close()

    restored = await runtime.submissions.read_submission()
    assert restored.client_message_id == submission.client_message_id
    assert restored.attachments == ({"kind": "image", "name": "screen.png"},)
    assert restored.extras == {"source": "selection"}


@pytest.mark.anyio
async def test_multiple_retry_ids_restore_separately_in_fifo(
    protocol_client: ProtocolCommandClient,
) -> None:
    async def retry(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=tuple(kwargs["client_message_ids"]),
            unknown_ids=(),
        )

    protocol_client.reconcile_turn_inputs = AsyncMock(side_effect=retry)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    assert control.submit(_submission("first retry"), False)
    assert control.submit(_submission("second retry"), False)
    while protocol_client.steer_turn.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    assert runtime.submissions.rejected_steers.active
    queue_text = "".join(
        text for _style, text in runtime.screen._queued_fragments(width=80)
    )
    assert "first retry" in queue_text
    assert "second retry" in queue_text

    first = await runtime.submissions.read_submission()
    second = await runtime.submissions.read_submission()
    assert first.client_message_id == "message_first_retry"
    assert second.client_message_id == "message_second_retry"


@pytest.mark.anyio
async def test_interrupt_reconciles_multiple_pending_steers_in_fifo(
    protocol_client: ProtocolCommandClient,
) -> None:
    async def retry(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=tuple(kwargs["client_message_ids"]),
            unknown_ids=(),
        )

    protocol_client.reconcile_turn_inputs = AsyncMock(side_effect=retry)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    assert control.submit(_submission("second query"), False)
    assert control.submit(_submission("third query"), False)
    while protocol_client.steer_turn.await_count < 2:
        await asyncio.sleep(0)

    control.request_interrupt()
    await control.close()

    second = await runtime.submissions.read_submission()
    third = await runtime.submissions.read_submission()

    assert second.client_message_id == "message_second_query"
    assert third.client_message_id == "message_third_query"
    protocol_client.interrupt_turn.assert_awaited_once()
    protocol_client.get_turn_status.assert_awaited_once()


@pytest.mark.anyio
async def test_unknown_sent_steer_requires_manual_restore(
    protocol_client: ProtocolCommandClient,
) -> None:
    async def unknown(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=(),
            unknown_ids=tuple(kwargs["client_message_ids"]),
        )

    protocol_client.reconcile_turn_inputs = AsyncMock(side_effect=unknown)
    runtime = TuiRuntime()
    attachments = _Attachments([{"kind": "image"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    runtime.bind_queued_restore_handler(control.restore_draft)
    _mark_started(control)
    submission = _submission("uncertain")

    assert control.submit(submission, False)
    await asyncio.sleep(0)
    await control.close()

    assert runtime.uncertain_steers_active
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == submission.editable_text
    assert attachments.consume_pending_attachments() == [{"kind": "image"}]
    assert state.consume_pending_prompt_extras() == {"source": "selection"}


@pytest.mark.anyio
async def test_pending_reconciliation_retries_until_classified(
    monkeypatch,
    protocol_client: ProtocolCommandClient,
) -> None:
    responses = [
        SimpleNamespace(
            committed_ids=(),
            pending_ids=("message_wait",),
            retry_ids=(),
            unknown_ids=(),
        ),
        SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=("message_wait",),
            unknown_ids=(),
        ),
    ]
    reconcile = AsyncMock(side_effect=responses)
    protocol_client.reconcile_turn_inputs = reconcile
    monkeypatch.setattr(
        TuiTurnInputControl,
        "RECONCILE_RETRY_INTERVAL_SEC",
        0.001,
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    assert control.submit(_submission("wait"), False)
    await asyncio.sleep(0)
    await control.close()

    assert reconcile.await_count == 2
    restored = await runtime.submissions.read_submission()
    assert restored.client_message_id == "message_wait"


@pytest.mark.anyio
async def test_remote_interrupt_uses_bound_turn_once(
    monkeypatch,
    protocol_client: ProtocolCommandClient,
) -> None:
    interrupt = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    protocol_client.interrupt_turn = interrupt
    monkeypatch.setattr(
        turn_input_session,
        "new_request_id",
        lambda _prefix: "interrupt_request_1",
    )
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    control.request_interrupt()
    control.request_interrupt()
    while interrupt.await_count < 1:
        await asyncio.sleep(0)
    await control.close()

    interrupt.assert_awaited_once_with(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        request_id="interrupt_request_1",
    )
@pytest.mark.anyio
async def test_late_remote_interrupt_accepts_turn_not_steerable(
    protocol_client: ProtocolCommandClient,
) -> None:
    interrupt = AsyncMock(
        return_value=SimpleNamespace(status="turn_not_steerable")
    )
    protocol_client.interrupt_turn = interrupt
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    control.request_interrupt()
    while interrupt.await_count < 1:
        await asyncio.sleep(0)
    await control.close()


@pytest.mark.anyio
async def test_remote_interrupt_retries_with_the_same_turn(
    protocol_client: ProtocolCommandClient,
) -> None:
    interrupt = AsyncMock(side_effect=[
        ProtocolCommandError("response_lost", "response lost", retryable=True),
        SimpleNamespace(status="turn_not_steerable"),
    ])
    protocol_client.interrupt_turn = interrupt
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)
    control.request_interrupt()
    while interrupt.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    assert interrupt.await_count == 2
    assert interrupt.await_args_list[0] == interrupt.await_args_list[1]


@pytest.mark.anyio
async def test_remote_interrupt_response_loss_still_waits_for_settlement(
    protocol_client: ProtocolCommandClient,
) -> None:
    status_started = asyncio.Event()
    release_settlement = asyncio.Event()
    interrupt_error = ProtocolCommandError(
        "response_lost",
        "response lost",
        retryable=True,
    )

    async def wait_for_status(**_kwargs):
        status_started.set()
        await release_settlement.wait()
        return SimpleNamespace(status="interrupted", terminal=True)

    protocol_client.interrupt_turn = AsyncMock(side_effect=[
        interrupt_error,
        interrupt_error,
    ])
    protocol_client.get_turn_status = AsyncMock(side_effect=wait_for_status)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    control.request_interrupt()
    await status_started.wait()
    closing = asyncio.create_task(control.close())
    await asyncio.sleep(0)

    assert not closing.done()
    assert protocol_client.interrupt_turn.await_count == 2

    release_settlement.set()
    await asyncio.wait_for(closing, timeout=0.1)

    protocol_client.get_turn_status.assert_awaited_once()


@pytest.mark.anyio
async def test_local_interrupt_waits_for_remote_turn_settlement(
    monkeypatch,
    protocol_client: ProtocolCommandClient,
) -> None:
    remote_started = asyncio.Event()
    status_started = asyncio.Event()
    release_settlement = asyncio.Event()
    turn_started = asyncio.Event()
    turn_cancelled = asyncio.Event()

    async def wait_for_remote(**_kwargs):
        remote_started.set()
        return SimpleNamespace(status="accepted")

    status_results = [
        SimpleNamespace(status="running", terminal=False),
        SimpleNamespace(status="interrupted", terminal=True),
    ]

    async def wait_for_status(**_kwargs):
        result = status_results.pop(0)
        if not result.terminal:
            return result
        status_started.set()
        await release_settlement.wait()
        return result

    async def turn() -> None:
        turn_started.set()
        try:
            await asyncio.Future()
        finally:
            turn_cancelled.set()

    protocol_client.interrupt_turn = AsyncMock(side_effect=wait_for_remote)
    protocol_client.get_turn_status = AsyncMock(side_effect=wait_for_status)
    protocol_client.steer_turn = AsyncMock(
        return_value=SimpleNamespace(status="turn_not_steerable")
    )
    monkeypatch.setattr(
        TuiTurnInputControl,
        "INTERRUPT_STATUS_RETRY_INTERVAL_SEC",
        0.0,
    )

    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    interrupt_notice = loop_session._TurnInterruptNotice(application, runtime)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    _mark_started(control)

    execution = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
        turn_input_control=control,
        on_interrupt_requested=interrupt_notice.acknowledge,
        show_interrupt_notice=lambda: not interrupt_notice.shown,
    ))
    await turn_started.wait()
    await runtime.activity.begin_wait()

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    assert interrupt_notice.shown
    assert runtime.activity.lease("wait") is None
    assert runtime.execution_active
    assert application.emit.call_count == 1
    assert not execution.done()
    await remote_started.wait()

    for _ in range(10):
        await asyncio.sleep(0)
        if status_started.is_set() or execution.done():
            break

    waited_for_settlement = status_started.is_set()
    execution_was_blocked = not execution.done()

    next_turn_started = asyncio.Event()

    async def read_next_turn_after_settlement() -> TuiSubmission:
        await execution
        submission = await runtime.submissions.read_submission()
        next_turn_started.set()
        return submission

    next_turn = asyncio.create_task(read_next_turn_after_settlement())
    buffer = runtime.screen.input.buffer
    buffer.text = "next turn"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    assert buffer.text == ""
    assert runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.pending_steers.active
    assert "next turn" in fragments_text(
        runtime.screen._queued_fragments(width=80)
    )
    protocol_client.steer_turn.assert_not_awaited()
    await asyncio.sleep(0)
    assert not next_turn_started.is_set()

    release_settlement.set()
    next_submission = await asyncio.wait_for(next_turn, timeout=0.1)

    assert turn_cancelled.is_set()
    assert waited_for_settlement
    assert execution_was_blocked
    assert protocol_client.get_turn_status.await_count == 2
    assert next_submission.value == "next turn"
    assert application.emit.call_count == 1

    await runtime.close()


@pytest.mark.anyio
async def test_interrupt_before_turn_start_waits_for_remote_control(
    protocol_client: ProtocolCommandClient,
) -> None:
    turn_started = asyncio.Event()
    release_turn_start = asyncio.Event()
    remote_started = asyncio.Event()
    turn_cancelled = asyncio.Event()
    late_assistant_emitted = asyncio.Event()

    async def interrupt_remote(**_kwargs):
        remote_started.set()
        return SimpleNamespace(status="accepted")

    async def turn() -> SimpleNamespace:
        turn_started.set()
        try:
            await release_turn_start.wait()
            control.handle_event(MarkerEvent(
                type="turn.start",
                turn_id="turn_001",
            ))
            await asyncio.sleep(0)
            application.emit(SimpleNamespace(type="assistant"))
            late_assistant_emitted.set()
            await remote_started.wait()
            return SimpleNamespace(status="interrupted")
        finally:
            turn_cancelled.set()

    protocol_client.interrupt_turn = AsyncMock(side_effect=interrupt_remote)
    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    interrupt_notice = loop_session._TurnInterruptNotice(application, runtime)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        protocol_client=protocol_client,
    )
    execution = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
        turn_input_control=control,
        on_interrupt_requested=interrupt_notice.acknowledge,
        show_interrupt_notice=lambda: not interrupt_notice.shown,
    ))
    await turn_started.wait()

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    assert interrupt_notice.shown
    assert runtime.execution_active
    assert application.emit.call_count == 1
    assert not control.request_interrupt()
    protocol_client.interrupt_turn.assert_not_awaited()
    for _ in range(10):
        await asyncio.sleep(0)
        if execution.done():
            break
    execution_waited_for_start = not execution.done()

    buffer = runtime.screen.input.buffer
    buffer.text = "next after early interrupt"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    assert buffer.text == ""
    assert runtime.submissions.queued_messages.active
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.pending_steers.active
    assert "next after early interrupt" in fragments_text(
        runtime.screen._queued_fragments(width=80)
    )

    release_turn_start.set()
    await asyncio.wait_for(execution, timeout=0.1)
    next_submission = await runtime.submissions.read_submission()

    assert execution_waited_for_start
    assert turn_cancelled.is_set()
    assert not late_assistant_emitted.is_set()
    protocol_client.interrupt_turn.assert_awaited_once()
    protocol_client.steer_turn.assert_not_awaited()
    assert next_submission.value == "next after early interrupt"
    assert application.emit.call_count == 1

    await runtime.close()
