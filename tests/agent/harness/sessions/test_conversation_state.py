# -*- coding: utf-8 -*-

from agent.harness.sessions.conversation import ConversationState
from protocol.schema.identifiers import new_cid, new_sid


def test_conversation_turn_marks_only_initial_boundary() -> None:
    state = ConversationState()

    assert state.fork_source_available is False

    first = state.begin_turn(start_reason="calling")
    second = state.begin_turn(start_reason="calling")

    assert first.turn_index == 1
    assert first.session_started is True
    assert first.session_mode == "create"
    assert first.start_reason == "initial"
    assert state.fork_source_available is True
    assert first.metadata() == {"cid": first.cid, "sid": first.sid}
    assert second.turn_index == 2
    assert second.session_started is False
    assert second.session_mode == "existing"
    assert second.start_reason == ""
    assert second.metadata() == first.metadata()


def test_conversation_reset_defers_boundary_until_next_turn() -> None:
    state = ConversationState()
    previous = state.begin_turn()

    state.reset(reason="command:/new")
    assert state.fork_source_available is False
    started = state.begin_turn()

    assert started.turn_index == 1
    assert started.session_started is True
    assert started.start_reason == "command:/new"
    assert state.fork_source_available is True
    assert started.metadata() != previous.metadata()


def test_snapshot_preserves_initial_boundary_for_first_turn() -> None:
    state = ConversationState()
    metadata = state.snapshot()

    assert state.session_bound is False
    started = state.begin_turn()

    assert started.metadata() == metadata
    assert started.session_started is True
    assert started.start_reason == "initial"


def test_bound_conversation_starts_on_first_model_turn() -> None:
    cid = new_cid()
    sid = new_sid(cid)
    state = ConversationState(
        cid=cid,
        sid=sid,
        start_reason="tui:resume",
        fork_source_available=True,
    )

    assert state.session_bound is True
    started = state.begin_turn()

    assert started.metadata() == {"cid": cid, "sid": sid}
    assert started.turn_index == 1
    assert started.session_started is True
    assert started.start_reason == "tui:resume"
    assert started.session_mode == "existing"


def test_changed_external_session_creates_new_boundary() -> None:
    cid = new_cid()
    first_sid = new_sid(cid)
    state = ConversationState(cid=cid, sid=first_sid)
    state.begin_turn()
    next_sid = new_sid(cid)

    started = state.begin_turn(
        cid=cid,
        sid=next_sid,
        start_reason="calling",
    )

    assert started.metadata() == {"cid": cid, "sid": next_sid}
    assert started.turn_index == 1
    assert started.session_started is True
    assert started.start_reason == "calling"


def test_queued_context_is_consumed_by_next_turn_only() -> None:
    state = ConversationState()
    state.snapshot()
    state.queue_turn_context(
        [" first ", "", "second"],
        system_message=" compact guidance ",
    )

    first = state.begin_turn()
    second = state.begin_turn()

    assert first.additional_context == ("first", "second")
    assert first.system_message == "compact guidance"
    assert second.additional_context == ()
    assert second.system_message == ""


def test_reset_discards_queued_context() -> None:
    state = ConversationState()
    state.queue_turn_context(["stale"], system_message="stale system")

    state.reset(reason="command:/new")
    started = state.begin_turn()

    assert started.additional_context == ()
    assert started.system_message == ""
