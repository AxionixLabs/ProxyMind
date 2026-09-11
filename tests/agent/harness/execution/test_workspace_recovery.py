from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.harness.execution.observed_turn import observe_frozen_root_turn
from agent.protocol import (
    ModelStreamRequest,
    SubmitTurnCommand,
)


@pytest.mark.anyio
async def test_frozen_turn_in_another_workspace_never_opens_tool_session():
    session = SimpleNamespace(
        workspace_root="D:/current",
        snapshot=lambda: {"cid": "cid_test", "sid": "sid_test"},
    )
    command = SubmitTurnCommand.create(session_id="sid_test", message="continue")
    request = ModelStreamRequest(
        cid="cid_test", sid="sid_test", turn_id="turn_test", message="continue",
        pref_config={}, tools=(), environment_snapshot={"cwd": "D:/original"},
    )
    runtime = SimpleNamespace(with_mcp_session=AsyncMock())
    with pytest.raises(ValueError, match="pending turn.*D:/original"):
        await observe_frozen_root_turn(
            session, command, request, source="recovery", model_capability=None,
            turn_observer=None, protocol_client=None, effect_journal_factory=None,
            tool_execution=None, execution_runtime=runtime,
        )
    runtime.with_mcp_session.assert_not_awaited()
