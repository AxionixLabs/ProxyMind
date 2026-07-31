# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.modes.result import RunResult
from mind_app.runtime.execution import AgentContext
from mind_app.runtime.subagents.control import AgentSnapshot
from mind_app.runtime.subagents.thread import AgentThreadContext
from mind_app.tui.features.agents import (
    agent_detail_menu,
    agent_list_menu,
    append_agent_stream_snapshot,
    manage_agents,
)
from mind_core.permissions import preset_permissions
from mind_nova.identifiers import new_cid, new_sid


def _snapshot(
    status,
    *,
    agent_id="agent_review",
    agent_type="review",
    turn_count=1,
    queued_count=0,
    result=None,
    error="",
    parent_agent_id=None,
):
    cid = new_cid()
    root = AgentContext.root("sid_root")
    parent = (
        root.child("parent", agent_id=parent_agent_id)
        if parent_agent_id
        else root
    )
    thread = AgentThreadContext(
        agent=parent.child(
            agent_type,
            agent_id=agent_id,
        ),
        cid=cid,
        sid=new_sid(cid),
        mode="xtra",
        source="subagent",
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        pref_config={},
        spawn_turn_id="turn_root",
    )
    return AgentSnapshot(
        thread=thread,
        status=status,
        submission_id="submission",
        turn_count=turn_count,
        queued_count=queued_count,
        result=result,
        error=error,
    )


def test_agent_list_menu_displays_status_and_queue_counts() -> None:
    snapshots = (
        _snapshot("running", queued_count=2),
        _snapshot(
            "completed",
            agent_id="agent_test",
            agent_type="test",
            turn_count=3,
        ),
    )

    request = agent_list_menu(snapshots)

    assert request.title == "Agents"
    assert request.status == "active=1 queued=2 total=2"
    assert request.options[0].label == "review"
    assert request.options[0].detail == (
        "running · agent_review · turns=1 queued=2"
    )
    assert request.options[1].detail == (
        "completed · agent_test · turns=3 queued=0"
    )


def test_agent_list_menu_orders_nested_threads_under_parent() -> None:
    parent = _snapshot("running", agent_id="agent_parent")
    sibling = _snapshot(
        "completed",
        agent_id="agent_sibling",
        agent_type="test",
    )
    child = _snapshot(
        "completed",
        agent_id="agent_child",
        agent_type="review-child",
        parent_agent_id="agent_parent",
    )

    request = agent_list_menu((parent, sibling, child))

    assert [option.value for option in request.options] == [
        "agent_parent",
        "agent_child",
        "agent_sibling",
    ]
    assert request.options[1].label == "  review-child"


def test_agent_detail_menu_exposes_state_specific_actions() -> None:
    running = agent_detail_menu(_snapshot("running"))
    closed = agent_detail_menu(_snapshot(
        "closed",
        result=RunResult(status="completed", assistant_text="done"),
    ))

    assert [option.label for option in running.options] == [
        "Back",
        "Interrupt",
        "Close",
    ]
    assert [option.label for option in closed.options] == [
        "Back",
        "Resume",
    ]
    assert "Result: done" in closed.body


@pytest.mark.anyio
async def test_manage_agents_closes_selected_thread_and_refreshes() -> None:
    running = _snapshot("running")
    closed = _snapshot("closed")
    requests = []

    class Runtime:
        async def select_menu(self, request):
            requests.append(request)
            if request.title == "Agents" and len(requests) == 1:
                return running.agent_id
            if request.title == "Agent Thread":
                return next(
                    option.value
                    for option in request.options
                    if option.label == "Close"
                )
            return None

    views = []
    subagents = SimpleNamespace(
        snapshots=AsyncMock(side_effect=[(running,), (closed,)]),
        close=AsyncMock(return_value=running),
        get=AsyncMock(return_value=closed),
    )
    mind = SimpleNamespace(
        conversation=SimpleNamespace(sid="sid_root"),
        subagents=subagents,
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await manage_agents(Runtime(), mind)

    subagents.close.assert_awaited_once_with("sid_root", running.agent_id)
    subagents.get.assert_awaited_once_with("sid_root", running.agent_id)
    assert [request.title for request in requests] == [
        "Agents",
        "Agent Thread",
        "Agents",
    ]
    assert [view.type for view in views] == [
        "tui.agents.status",
        "tui.gap",
    ]


@pytest.mark.anyio
async def test_manage_agents_without_root_session_has_no_side_effects() -> None:
    views = []
    snapshots = AsyncMock()
    mind = SimpleNamespace(
        conversation=SimpleNamespace(sid=None),
        subagents=SimpleNamespace(snapshots=snapshots),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    await manage_agents(SimpleNamespace(), mind)

    snapshots.assert_not_awaited()
    assert views[0].type == "tui.agents.status"
    assert "No agents in this conversation" in "".join(
        text for _style, text in views[0].renderable.fragments
    )


@pytest.mark.anyio
async def test_stream_agent_snapshot_uses_background_block_queue() -> None:
    running = _snapshot("running", queued_count=1)
    queued = Mock()
    runtime = SimpleNamespace(queue_background_block=queued)
    mind = SimpleNamespace(
        conversation=SimpleNamespace(sid="sid_root"),
        subagents=SimpleNamespace(
            snapshots=AsyncMock(return_value=(running,)),
        ),
    )

    await append_agent_stream_snapshot(runtime, mind)

    block = queued.call_args.args[0]
    text = "".join(fragment for _style, fragment in block.fragments)
    assert text.startswith("/agent · running=1")
    assert "review · agent_review · running · turns=1 queued=1" in text
