# -*- coding: utf-8 -*-

from unittest.mock import AsyncMock

import pytest

from mind_app.modes.flow import FlowRuntime, _run_flow_turn
from mind_app.runtime.execution import AgentContext
from mind_core.permissions import preset_permissions


@pytest.mark.anyio
async def test_flow_consumes_session_boundary_on_first_model_turn() -> None:
    runner = AsyncMock(return_value=object())
    runtime = FlowRuntime(
        mode="xtra",
        pref_config={"primary": {"model": "test-model"}},
        event_report=object(),
        runner=runner,
        metadata={"cid": "cid_test", "sid": "sid_test"},
        agent=AgentContext.root("sid_test"),
        permissions=preset_permissions("auto"),
        cwd=".",
        pending_session_start_reason="initial",
    )

    await _run_flow_turn(runtime, object(), "first", [])
    await _run_flow_turn(runtime, object(), "second", [])

    first_context = runner.await_args_list[0].kwargs["turn_context"]
    second_context = runner.await_args_list[1].kwargs["turn_context"]
    assert first_context.session_started is True
    assert first_context.session_start_reason == "initial"
    assert second_context.session_started is False
    assert second_context.session_start_reason == ""
