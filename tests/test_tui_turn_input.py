# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.interrupt import InterruptDisposition
from mind_app.tui.core.styles import query_block, text_block
from mind_app.tui.session import turn_input as turn_input_session
from mind_app.tui.session.turn import execute_tui_model_turn
from mind_app.tui.session.turn_input import TuiTurnInputControl
from mind_nova.requests.turn_control import TurnControlRequestError
from mind_nova.stream_events import (
    MarkerEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
)
from mind_nova.turn_inputs import TurnInput


@pytest.fixture(autouse=True)
def _committed_reconciliation(monkeypatch):
    async def reconcile(**kwargs):
        client_message_ids = tuple(kwargs["client_message_ids"])
        return SimpleNamespace(
            committed_ids=client_message_ids,
            pending_ids=(),
            retry_ids=(),
            unknown_ids=(),
        )

    monkeypatch.setattr(turn_input_session, "reconcile_turn_inputs", reconcile)


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
async def test_control_requests_wait_for_matching_turn_start(monkeypatch) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    interrupt = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    monkeypatch.setattr(turn_input_session, "interrupt_turn", interrupt)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    assert control.submit(_submission("too early"), False)
    assert runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert not runtime.submissions.rollback_queued_input()
    steer.assert_not_awaited()

    assert control.submit(_submission("queue early"), True)
    assert runtime.submissions.rollback_queued_input()
    control.request_interrupt()
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
    monkeypatch,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
    monkeypatch,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
    monkeypatch,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)

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
            attachments=sent.attachments,
            extras=sent.extras,
        ),
    ))

    assert not runtime.submissions.pending_steers.active
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == submission.editable_text

    await control.close()


@pytest.mark.anyio
async def test_empty_settlement_keeps_local_tab_queue_fifo() -> None:
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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


def test_tab_queue_captures_payload_and_restores_editable_draft() -> None:
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="turn_not_steerable")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)
    submission = _submission("retry next")

    assert control.submit(submission, False)
    await control.close()

    assert not runtime.submissions.pending_steers.active
    assert not runtime.submissions.queued_messages.active
    assert runtime.submissions.can_rollback_queued_input

    queued = await runtime.submissions.read_submission()
    assert queued.client_message_id == submission.client_message_id


@pytest.mark.anyio
async def test_sampling_acceptance_removes_immediate_input_from_next_turn(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
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
    monkeypatch,
) -> None:
    steer = AsyncMock(side_effect=[
        TurnControlRequestError("response lost"),
        TurnControlRequestError("response lost"),
    ])
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
async def test_closing_turn_clears_unsettled_steer_display(monkeypatch) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)

    assert control.submit(_submission("unsettled"), False)
    assert runtime.submissions.pending_steers.active

    await control.close()

    assert not runtime.submissions.pending_steers.active


@pytest.mark.anyio
async def test_settlement_cancels_unfinished_steer_without_local_retry(
    monkeypatch,
) -> None:
    request_started = asyncio.Event()

    async def steer(**_kwargs):
        request_started.set()
        await asyncio.Future()

    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )

    async def retry(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=tuple(kwargs["client_message_ids"]),
            unknown_ids=(),
        )

    monkeypatch.setattr(turn_input_session, "reconcile_turn_inputs", retry)
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )

    async def retry(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=tuple(kwargs["client_message_ids"]),
            unknown_ids=(),
        )

    monkeypatch.setattr(turn_input_session, "reconcile_turn_inputs", retry)
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)

    assert control.submit(_submission("first retry"), False)
    assert control.submit(_submission("second retry"), False)
    while turn_input_session.steer_turn.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    first = await runtime.submissions.read_submission()
    second = await runtime.submissions.read_submission()
    assert first.client_message_id == "message_first_retry"
    assert second.client_message_id == "message_second_retry"


@pytest.mark.anyio
async def test_unknown_sent_steer_requires_manual_restore(monkeypatch) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )

    async def unknown(**kwargs):
        return SimpleNamespace(
            committed_ids=(),
            pending_ids=(),
            retry_ids=(),
            unknown_ids=tuple(kwargs["client_message_ids"]),
        )

    monkeypatch.setattr(turn_input_session, "reconcile_turn_inputs", unknown)
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
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "steer_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
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
    monkeypatch.setattr(turn_input_session, "reconcile_turn_inputs", reconcile)
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
) -> None:
    interrupt = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "interrupt_turn", interrupt)
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
    monkeypatch,
) -> None:
    interrupt = AsyncMock(
        return_value=SimpleNamespace(status="turn_not_steerable")
    )
    monkeypatch.setattr(turn_input_session, "interrupt_turn", interrupt)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)

    control.request_interrupt()
    while interrupt.await_count < 1:
        await asyncio.sleep(0)
    await control.close()


@pytest.mark.anyio
async def test_remote_interrupt_retries_with_the_same_turn(
    monkeypatch,
) -> None:
    interrupt = AsyncMock(side_effect=[
        TurnControlRequestError("response lost"),
        SimpleNamespace(status="turn_not_steerable"),
    ])
    monkeypatch.setattr(turn_input_session, "interrupt_turn", interrupt)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)
    control.request_interrupt()
    while interrupt.await_count < 2:
        await asyncio.sleep(0)
    await control.close()

    assert interrupt.await_count == 2
    assert interrupt.await_args_list[0] == interrupt.await_args_list[1]


@pytest.mark.anyio
async def test_local_interrupt_does_not_wait_for_remote_request(
    monkeypatch,
) -> None:
    remote_started = asyncio.Event()
    release_remote = asyncio.Event()
    turn_started = asyncio.Event()
    turn_cancelled = asyncio.Event()

    async def wait_for_remote(**_kwargs):
        remote_started.set()
        await release_remote.wait()
        return SimpleNamespace(status="accepted")

    async def turn() -> None:
        turn_started.set()
        try:
            await asyncio.Future()
        finally:
            turn_cancelled.set()

    monkeypatch.setattr(turn_input_session, "interrupt_turn", wait_for_remote)

    runtime = TuiRuntime()
    application = SimpleNamespace(emit=Mock())
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    _mark_started(control)

    execution = asyncio.create_task(execute_tui_model_turn(
        application,
        runtime,
        turn(),
        turn_input_control=control,
    ))
    await turn_started.wait()

    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    await remote_started.wait()
    await asyncio.wait_for(execution, timeout=0.1)

    assert turn_cancelled.is_set()
    assert not release_remote.is_set()

    release_remote.set()
    await runtime.close()
