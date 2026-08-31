# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.history.transcript import TranscriptWriter
from agent.application.turns.context import AgentContext
from agent.application.agents.views import AgentSnapshot
from agent.application.agents.thread import AgentThreadContext
from mind_app.tui.core.models import (
    FragmentBlock,
    MenuDescriptionLayout,
    STANDARD_MENU_FOOTER_HINT,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.agents import (
    agent_detail_menu,
    agent_list_menu,
    agent_snapshot_block,
    manage_agents,
)
from agent.domain.policies import preset_permissions
from protocol.schema.identifiers import new_cid, new_sid


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
    transcript_path="",
):
    cid = new_cid()
    root = AgentContext.root("sid_root")
    parent = (
        root.child("parent", "parent", agent_id=parent_agent_id)
        if parent_agent_id
        else root
    )
    thread = AgentThreadContext(
        agent=parent.child(
            agent_type,
            agent_type.replace("-", "_") or "child",
            agent_id=agent_id,
        ),
        cid=cid,
        sid=new_sid(cid),
        source="subagent",
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        pref_config={},
        spawn_turn_id="turn_root",
        transcript_path=transcript_path,
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


async def _wait_for_menu(runtime: TuiRuntime, title: str) -> None:
    for _ in range(100):
        state = runtime.screen.menu.state
        if state is not None and state.request.title == title:
            return None
        await asyncio.sleep(0)
    raise AssertionError(f"menu did not open: {title}")


async def _wait_for_calls(mock: AsyncMock) -> None:
    for _ in range(100):
        if mock.await_count:
            return None
        await asyncio.sleep(0)
    raise AssertionError("background menu action did not run")


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

    request = agent_list_menu(snapshots, root_session_id="sid_root")

    assert request.title == "Sub-agents"
    assert request.title_accent_suffix == ""
    assert request.status == "View and manage sub-agent threads."
    assert request.selected == 0
    assert request.options[0].label == "• Main [default]"
    assert request.options[0].detail == "sid_root"
    assert request.options[0].is_current
    assert request.options[0].is_default
    assert request.options[1].label == "• /root/review"
    assert request.options[1].detail == "agent_review · running"
    assert request.options[2].detail == "agent_test · completed"
    assert request.body == ()
    assert request.view_id == "agents:list:sid_root"
    assert request.help_text == ""
    assert request.footer_hint == STANDARD_MENU_FOOTER_HINT
    assert (
        request.description_layout
        is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    )


def test_failed_agent_uses_existing_list_dot_and_square_in_bare_details() -> None:
    failed = _snapshot("failed", error="boom")

    listing = agent_list_menu((failed,), root_session_id="sid_root")
    detail = agent_detail_menu(failed)
    snapshot = agent_snapshot_block(failed)

    assert listing.options[1].label == "• /root/review"
    assert listing.options[1].detail == "agent_review · failed"
    assert "■" not in listing.options[1].detail
    assert detail.status == "Choose an action for this sub-agent thread."
    assert detail.options[1].detail == "return to agent list"
    assert "".join(text for _style, text in snapshot.fragments) == (
        "/agent\n\n"
        "Sub-agents\n\n"
        "  • /root/review · failed · turns=1 queued=0"
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

    assert [option.value for option in request.options[1:]] == [
        "agent_parent",
        "agent_child",
        "agent_sibling",
    ]
    assert request.options[2].label == "  • /root/parent/review_child"


def test_agent_detail_menu_exposes_state_specific_actions() -> None:
    running = agent_detail_menu(_snapshot("running"))
    closed = agent_detail_menu(_snapshot("closed"))

    assert [option.label for option in running.options] == [
        "Show",
        "Back",
        "Interrupt",
        "Close",
    ]
    assert [option.label for option in closed.options] == [
        "Show",
        "Back",
        "Resume",
    ]
    assert running.body == ()
    assert closed.body == ()


@pytest.mark.anyio
async def test_manage_agents_closes_selected_thread_and_returns_to_input() -> None:
    running = _snapshot("running")
    closed = _snapshot("closed")
    runtime = TuiRuntime()
    views = []
    subagents = SimpleNamespace(
        snapshots=AsyncMock(return_value=(running,)),
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

    task = asyncio.create_task(manage_agents(runtime, mind))
    await _wait_for_menu(runtime, "Sub-agents")
    runtime.screen.menu._choose_index(1)
    await _wait_for_menu(runtime, "Agent actions")
    close_index = next(
        index
        for index, option in enumerate(
            runtime.screen.menu.state.request.options
        )
        if option.label == "Close"
    )
    runtime.screen.menu._choose_index(close_index)
    await _wait_for_calls(subagents.close)

    subagents.close.assert_awaited_once_with("sid_root", running.agent_id)
    subagents.get.assert_not_awaited()
    assert views == []
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.anyio
async def test_manage_agents_back_returns_to_agent_list() -> None:
    running = _snapshot("running")
    runtime = TuiRuntime()
    subagents = SimpleNamespace(
        snapshots=AsyncMock(return_value=(running,)),
    )
    mind = SimpleNamespace(
        conversation=SimpleNamespace(sid="sid_root"),
        subagents=subagents,
    )

    task = asyncio.create_task(manage_agents(runtime, mind))
    await _wait_for_menu(runtime, "Sub-agents")
    runtime.screen.menu._choose_index(1)
    await _wait_for_menu(runtime, "Agent actions")
    runtime.screen.menu._choose_index(1)

    await _wait_for_menu(runtime, "Sub-agents")
    assert not task.done()
    runtime.cancel_menu()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.anyio
async def test_manage_agents_show_appends_only_the_refreshed_snapshot() -> None:
    listed = _snapshot("running", turn_count=1)
    current = _snapshot("running", turn_count=2, queued_count=1)
    runtime = TuiRuntime()

    subagents = SimpleNamespace(
        snapshots=AsyncMock(return_value=(listed,)),
        get=AsyncMock(return_value=current),
    )
    mind = SimpleNamespace(
        conversation=SimpleNamespace(sid="sid_root"),
        subagents=subagents,
    )

    task = asyncio.create_task(manage_agents(runtime, mind))
    await _wait_for_menu(runtime, "Sub-agents")
    runtime.screen.menu._choose_index(1)
    await _wait_for_menu(runtime, "Agent actions")
    runtime.screen.menu._choose_index(0)
    await asyncio.wait_for(task, timeout=1.0)

    subagents.get.assert_awaited_once_with("sid_root", listed.agent_id)
    text = "".join(
        text for _style, text in runtime.document.fragments(width=80)
    )
    assert "running · turns=2 queued=1" in "".join(
        text for _style, text in runtime.document.fragments(width=80)
    )


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

    runtime = TuiRuntime()
    task = asyncio.create_task(manage_agents(runtime, mind))
    await _wait_for_menu(runtime, "Sub-agents")

    snapshots.assert_not_awaited()
    assert views == []
    state = runtime.screen.menu.state
    assert state is not None
    assert state.request.body == ()
    assert state.request.options[0].label == "• Main [default]"
    assert state.request.options[0].is_current
    runtime.cancel_menu()
    await task


def test_agent_snapshot_block_shows_recent_shell_activity(tmp_path) -> None:
    path = tmp_path / "agent.jsonl"
    running = _snapshot("running", transcript_path=str(path))
    writer = TranscriptWriter(
        path,
        session_id=running.thread.sid,
        turn_id="turn_child",
    )
    writer.open()
    writer.append(
        "tool.started",
        actor="tool",
        payload={
            "call_id": "call_1",
            "name": "shell_command",
            "arguments": {"command": "rg -n auth mind_app"},
        },
    )
    writer.append(
        "tool.started",
        actor="tool",
        payload={
            "call_id": "call_2",
            "name": "shell_command",
            "arguments": {
                "command": (
                    "rg --files -g '!venv/**' -g '!.git/**' "
                    "| Select-Object -First 200"
                ),
            },
        },
    )
    writer.close()

    block = agent_snapshot_block(running)

    text = "".join(
        text for _style, text in block.fragments
    )
    assert text.startswith(
        "/agent\n\n"
        "Sub-agents\n\n"
        "  • /root/review · running · turns=1 queued=0\n"
        "    ↳ $ rg -n auth mind_app"
    )
    assert "      $ rg --files -g '!venv/**' -g '!.git/**' | Select-Object -First 200" in text
    assert ("bold", "Sub-agents") in block.fragments
    assert ("dim", "  • ") in block.fragments
    assert ("fg:ansicyan", "/root/review") in block.fragments
    assert ("dim", "    ↳ ") in block.fragments
    assert ("dim", "      ") in block.fragments
    assert ("dim", "$ rg -n auth mind_app") in block.fragments


def test_agent_snapshot_block_is_visible_while_assistant_is_streaming() -> None:
    runtime = TuiRuntime()
    runtime.set_active_renderable(
        FragmentBlock((("", "streaming"),)),
        kind="assistant",
    )

    runtime.append_block(
        agent_snapshot_block(_snapshot("running")),
        kind="notice",
    )

    text = "".join(
        value
        for _style, value in runtime.document.fragments(width=80)
    )
    assert text.startswith(
        "streaming\n\n/agent\n\nSub-agents\n\n"
        "  • /root/review · running · turns=1 queued=0"
    )
    assert len(runtime.document.blocks) == 0


def test_agent_snapshot_block_clips_rows_to_terminal_width() -> None:
    block = agent_snapshot_block(
        _snapshot("completed", transcript_path=""),
        terminal_width=24,
    )

    lines = "".join(text for _style, text in block.fragments).splitlines()
    assert all(get_cwidth(line) <= 24 for line in lines)
