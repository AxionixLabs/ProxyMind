# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.session import turn_input as turn_input_session
from mind_app.tui.session.turn_input import TuiTurnInputControl
from mind_nova.requests.turn_control import TurnControlRequestError
from mind_nova.stream_events import (
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
)
from mind_nova.turn_inputs import TurnInput


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


@pytest.mark.anyio
async def test_immediate_input_is_sent_and_late_settlement_precedes_tab_queue(
    monkeypatch,
) -> None:
    steer = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    follow_up = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "steer_turn", steer)
    monkeypatch.setattr(turn_input_session, "follow_up_turn", follow_up)

    runtime = TuiRuntime()
    attachments = _Attachments([{"kind": "image", "name": "screen.png"}])
    state = _State({"source": "selection"})
    control = TuiTurnInputControl(
        SimpleNamespace(attach=attachments),
        runtime,
        state,
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )

    assert control.submit(_submission("steer now"), False)
    assert control.submit(_submission("tab follow up"), True)
    await control.close()

    sent = steer.await_args.kwargs["turn_input"]
    assert sent.client_message_id == "message_steer_now"
    assert sent.attachments == ({"kind": "image", "name": "screen.png"},)
    assert sent.extras == {"source": "selection"}
    queued = follow_up.await_args.kwargs["turn_input"]
    assert queued.client_message_id == "message_tab_follow_up"
    assert queued.text == "tab follow up"
    assert queued.attachments == ()
    assert queued.extras == {}

    control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_1",
        next_input=TurnInput(
            client_message_id="message_steer_now",
            text="steer now",
            attachments=sent.attachments,
            extras=sent.extras,
        ),
    ))

    first = await runtime.submissions.read_submission()
    assert first.value == "steer now"
    assert first.attachments == sent.attachments
    assert first.extras == sent.extras
    assert runtime.submissions.queued_messages.waiting_settlement
    assert runtime.submissions.queued_messages.pop_next() is None


@pytest.mark.anyio
async def test_follow_up_settlement_reconciles_local_queue_without_duplicate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "follow_up_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )
    submission = _submission("queued once")

    assert control.submit(submission, True)
    await control.close()
    control.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id="turn_1",
        next_input=TurnInput(
            client_message_id=submission.client_message_id,
            text=submission.value,
        ),
    ))

    queued = await runtime.submissions.read_submission()
    assert queued.client_message_id == submission.client_message_id
    assert not queued.server_queued
    assert not runtime.submissions.queued_messages.active


@pytest.mark.anyio
async def test_rejected_follow_up_remains_local_and_editable(monkeypatch) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "follow_up_turn",
        AsyncMock(return_value=SimpleNamespace(status="turn_not_active")),
    )
    runtime = TuiRuntime()
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        runtime,
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )

    assert control.submit(_submission("edit locally"), True)
    await control.close()

    assert runtime.submissions.rollback_queued_input()
    assert runtime.screen.input.buffer.text == "edit locally"


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
        turn_id="turn_1",
    )
    submission = _submission("accepted steer")

    assert control.submit(submission, False)
    await control.close()
    accepted = control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id="turn_1",
        client_message_id=submission.client_message_id,
    ))

    assert accepted == TurnInput(
        client_message_id=submission.client_message_id,
        text="accepted steer",
    )
    assert not runtime.submissions.queued_messages.active
    assert runtime.document.blocks[-1].kind == "user"
    assert runtime.document.blocks[-1].turn_id == "turn_1"
    assert runtime.document.blocks[-1].prompt == "accepted steer"


@pytest.mark.anyio
async def test_remote_interrupt_uses_bound_turn_without_local_cancel(
    monkeypatch,
) -> None:
    interrupt = AsyncMock(return_value=SimpleNamespace(status="accepted"))
    monkeypatch.setattr(turn_input_session, "interrupt_turn", interrupt)
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )
    fallback = Mock(return_value=True)

    assert control.interrupt(fallback)
    await control.close()

    interrupt.assert_awaited_once_with(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )
    fallback.assert_not_called()


@pytest.mark.anyio
async def test_late_interrupt_keeps_stream_alive_for_logical_settlement(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        turn_input_session,
        "interrupt_turn",
        AsyncMock(return_value=SimpleNamespace(status="turn_not_steerable")),
    )
    control = TuiTurnInputControl(
        SimpleNamespace(attach=_Attachments()),
        TuiRuntime(),
        _State(),
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_1",
    )
    fallback = Mock(return_value=True)

    assert control.interrupt(fallback)
    await control.close()

    fallback.assert_not_called()


@pytest.mark.anyio
async def test_interrupt_retries_with_the_same_turn_before_local_fallback(
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
        turn_id="turn_1",
    )
    fallback = Mock(return_value=True)

    assert control.interrupt(fallback)
    await control.close()

    assert interrupt.await_count == 2
    assert interrupt.await_args_list[0] == interrupt.await_args_list[1]
    fallback.assert_not_called()
