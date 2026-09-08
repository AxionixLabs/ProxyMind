# -*- coding: utf-8 -*-

import pytest

from agent.adapters import ProtocolEventCursorStore


def test_session_event_cursor_is_monotonic_and_isolated() -> None:
    cursors = ProtocolEventCursorStore()

    assert cursors.current(cid="cid-1", sid="sid-1") == 0
    assert cursors.advance(cid="cid-1", sid="sid-1", event_seq=7) == 7
    assert cursors.advance(cid="cid-1", sid="sid-1", event_seq=5) == 7
    assert cursors.current(cid="cid-1", sid="sid-1") == 7
    assert cursors.current(cid="cid-1", sid="sid-2") == 0


@pytest.mark.parametrize("event_seq", (-1, True, 1.5, "1"))
def test_session_event_cursor_rejects_invalid_sequence(event_seq) -> None:
    cursors = ProtocolEventCursorStore()

    with pytest.raises(ValueError, match="event_seq"):
        cursors.advance(cid="cid-1", sid="sid-1", event_seq=event_seq)
