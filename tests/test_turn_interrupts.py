# -*- coding: utf-8 -*-

from unittest.mock import AsyncMock, Mock

import pytest

from agent.adapters.protocol.turn_interrupts import (
    interrupt_approval_cancelled_turn,
)
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
)


@pytest.mark.anyio
async def test_approval_cancel_does_not_retry_deterministic_interrupt() -> None:
    client = Mock(spec=ProtocolCommandClient)
    client.interrupt_turn = AsyncMock(side_effect=ProtocolCommandError(
        "turn_not_active",
        "turn is no longer active",
        retryable=False,
        details={"status_code": 409},
    ))

    interrupted = await interrupt_approval_cancelled_turn(
        client,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        call_id="call_test",
    )

    assert interrupted
    client.interrupt_turn.assert_awaited_once()


@pytest.mark.anyio
async def test_approval_cancel_retries_uncertain_interrupt_with_same_id() -> None:
    client = Mock(spec=ProtocolCommandClient)
    client.interrupt_turn = AsyncMock(side_effect=ProtocolCommandError(
        "turn_control_request_failed",
        "interrupt response was lost",
        retryable=True,
    ))

    interrupted = await interrupt_approval_cancelled_turn(
        client,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        call_id="call_test",
    )

    assert not interrupted
    assert client.interrupt_turn.await_count == 2
    assert client.interrupt_turn.await_args_list[0] == (
        client.interrupt_turn.await_args_list[1]
    )
