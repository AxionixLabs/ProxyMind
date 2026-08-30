from agent.stores.approval_ledger import ApprovalCallLedger


def test_approval_ledger_only_tracks_approval_consumption() -> None:
    ledger = ApprovalCallLedger()
    key = {
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn",
        "call_id": "call",
    }

    ledger.record_approved(**key)
    assert ledger.consume(**key) == "approved"
    assert ledger.consume(**key) == "consumed"


def test_clear_turn_removes_approval_state() -> None:
    ledger = ApprovalCallLedger()
    key = {
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn",
        "call_id": "call",
    }
    ledger.record_approved(**key)

    ledger.clear_turn(
        cid="cid",
        sid="sid",
        turn_id="turn",
    )
    assert ledger.consume(**key) == "unknown"


def test_terminal_approval_is_not_reopened_by_replayed_event() -> None:
    ledger = ApprovalCallLedger()
    key = {
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn",
        "call_id": "call",
    }

    ledger.record_terminal(**key)

    assert ledger.is_terminal(**key) is True
    assert ledger.is_approved(**key) is False
    assert ledger.consume(**key) == "terminal"
