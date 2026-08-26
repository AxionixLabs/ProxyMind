from mind_app.approval.ledger import ApprovalCallLedger


def test_pending_tool_result_is_replayable_until_committed() -> None:
    ledger = ApprovalCallLedger()
    key = {
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn",
        "call_id": "call",
    }

    ledger.record_result_pending(
        **key,
        name="shell_command",
        ok=False,
        result={"data": {"error": "transport failed"}},
        arguments={"command": "printf test"},
        additional_context=("retry result",),
    )

    pending = ledger.result_for(**key)
    assert pending is not None
    assert pending.state == "pending"
    assert pending.result == {"data": {"error": "transport failed"}}

    ledger.mark_result_committed(**key)
    committed = ledger.result_for(**key)
    assert committed is not None
    assert committed.state == "committed"


def test_failed_turn_can_preserve_pending_tool_result() -> None:
    ledger = ApprovalCallLedger()
    key = {
        "cid": "cid",
        "sid": "sid",
        "turn_id": "turn",
        "call_id": "call",
    }
    ledger.record_result_pending(
        **key,
        name="test_tool",
        ok=True,
        result={"ok": True},
        arguments={},
    )

    ledger.clear_turn(
        cid="cid",
        sid="sid",
        turn_id="turn",
        preserve_pending_results=True,
    )
    assert ledger.result_for(**key) is not None

    ledger.clear_turn(cid="cid", sid="sid", turn_id="turn")
    assert ledger.result_for(**key) is None
