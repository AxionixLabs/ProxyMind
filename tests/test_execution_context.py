# -*- coding: utf-8 -*-

import pytest

from mind_app.runtime.execution import (
    ROOT_AGENT_ID,
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from mind_core.permissions import preset_permissions


def test_root_agent_and_turn_context_share_session_identity() -> None:
    permissions = preset_permissions("auto")
    agent = AgentContext.root("sid_test")

    turn = TurnContext.create(
        agent=agent,
        cid="cid_test",
        sid="sid_test",
        mode="xtra",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd="D:/workspace",
        permissions=permissions,
        turn_id="turn_test",
        session_started=True,
        session_start_reason="initial",
    )

    assert agent.agent_id == ROOT_AGENT_ID
    assert agent.parent_agent_id is None
    assert agent.depth == 0
    assert turn.agent is agent
    assert turn.turn_id == "turn_test"
    assert turn.model == "test-model"
    assert turn.permissions is permissions
    assert turn.session_started is True
    assert turn.session_start_reason == "initial"


def test_turn_context_rejects_mismatched_root_session() -> None:
    with pytest.raises(ValueError, match="root session"):
        TurnContext.create(
            agent=AgentContext.root("sid_root"),
            cid="cid_test",
            sid="sid_other",
            mode="chat",
            source="test",
            pref_config={},
            cwd=".",
            permissions=preset_permissions("read-only"),
        )


def test_tool_invocation_replaces_arguments_without_losing_context() -> None:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        mode="fast",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        session_start_reason="ignored",
    )
    invocation = ToolInvocation(
        turn=turn,
        call_id="call_test",
        name="shell_command",
        arguments={"command": "rg TODO"},
    )

    updated = invocation.with_arguments({"command": "rg FIXME"})

    assert updated is not invocation
    assert updated.turn is turn
    assert updated.call_id == invocation.call_id
    assert updated.arguments == {"command": "rg FIXME"}
    assert turn.session_started is False
    assert turn.session_start_reason == ""
