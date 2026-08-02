# -*- coding: utf-8 -*-

import pytest

from mind_app.runtime.execution import (
    ROOT_AGENT_ID,
    ROOT_AGENT_TYPE,
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from mind_app.runtime.hooks.scope import HookExecutionContext
from mind_core.permissions import preset_permissions


def test_root_agent_and_turn_context_share_session_identity() -> None:
    permissions = preset_permissions("auto")
    agent = AgentContext.root("sid_test")

    turn = TurnContext.create(
        agent=agent,
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd="D:/workspace",
        permissions=permissions,
        transcript_path="D:/logs/transcript.log",
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
    assert turn.transcript_path == "D:/logs/transcript.log"
    assert turn.session_started is True
    assert turn.session_start_reason == "initial"


def test_turn_context_rejects_mismatched_root_session() -> None:
    with pytest.raises(ValueError, match="root session"):
        TurnContext.create(
            agent=AgentContext.root("sid_root"),
            cid="cid_test",
            sid="sid_other",
            source="test",
            pref_config={},
            cwd=".",
            permissions=preset_permissions("read-only"),
        )


def test_turn_context_rejects_invalid_explicit_turn_id() -> None:
    with pytest.raises(ValueError, match="8-128 ASCII"):
        TurnContext.create(
            agent=AgentContext.root("sid_test"),
            cid="cid_test",
            sid="sid_test",
            source="test",
            pref_config={},
            cwd=".",
            permissions=preset_permissions("read-only"),
            turn_id="short",
        )


def test_turn_context_generates_protocol_compatible_turn_id() -> None:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("read-only"),
    )

    assert 8 <= len(turn.turn_id) <= 128
    assert turn.turn_id.replace("_", "").replace("-", "").isalnum()


def test_child_agent_preserves_root_identity_and_advances_depth() -> None:
    root = AgentContext.root("sid_root")
    child = root.child("explore", "inspect", agent_id="agent_child")
    nested = child.child("review", "review", agent_id="agent_nested")

    assert child.root_session_id == "sid_root"
    assert child.parent_agent_id == ROOT_AGENT_ID
    assert child.depth == 1
    assert nested.root_session_id == "sid_root"
    assert nested.parent_agent_id == "agent_child"
    assert nested.depth == 2


def test_agent_context_normalizes_identity_fields() -> None:
    child = AgentContext.root(" sid_root ").child(
        " explore ",
        " Inspect_Work ",
        agent_id=" agent_child ",
    )

    assert child.agent_id == "agent_child"
    assert child.agent_type == "explore"
    assert child.root_session_id == "sid_root"
    assert child.task_name == "inspect_work"
    assert child.task_path == "/root/inspect_work"
    assert child.parent_agent_id == ROOT_AGENT_ID


def test_child_turn_can_use_an_independent_session() -> None:
    child = AgentContext.root("sid_root").child(
        "explore",
        "inspect",
        agent_id="agent_child",
    )

    turn = TurnContext.create(
        agent=child,
        cid="cid_child",
        sid="sid_child",
        source="subagent",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        transcript_path="D:/logs/subagent.log",
    )

    assert turn.sid == "sid_child"
    assert turn.agent.root_session_id == "sid_root"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"depth": 1}, "parent id"),
        ({"parent_agent_id": "parent"}, "cannot have a parent"),
        ({"depth": True}, "non-negative integer"),
        ({}, "reserved root identity"),
    ],
)
def test_agent_context_rejects_invalid_hierarchy(values, message) -> None:
    with pytest.raises(ValueError, match=message):
        AgentContext(
            agent_id="agent",
            agent_type="explore",
            root_session_id="sid_root",
            task_name="task",
            task_path="/root/task",
            **values,
        )


def test_child_agent_rejects_reserved_root_type() -> None:
    with pytest.raises(ValueError, match="reserved root identity"):
        AgentContext.root("sid_root").child(
            ROOT_AGENT_TYPE,
            "root_task",
            agent_id="agent_child",
        )


def test_child_hook_context_distinguishes_current_and_root_sessions() -> None:
    child = AgentContext.root("sid_root").child(
        "explore",
        "inspect",
        agent_id="agent_child",
    )
    turn = TurnContext.create(
        agent=child,
        cid="cid_child",
        sid="sid_child",
        source="subagent",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        transcript_path="D:/logs/subagent.log",
    )

    context = HookExecutionContext.from_turn(turn)

    assert context.session_id == "sid_child"
    assert context.root_session_id == "sid_root"
    payload = context.payload("PreToolUse", {
        "tool_name": "Bash",
        "tool_use_id": "call_test",
        "tool_input": {"command": "rg TODO"},
    })
    assert payload["session_id"] == "sid_root"
    assert payload["agent_id"] == "agent_child"
    assert payload["agent_type"] == "explore"
    assert payload["transcript_path"] == "D:/logs/subagent.log"
    assert "root_session_id" not in payload


def test_tool_invocation_replaces_arguments_without_losing_context() -> None:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
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
