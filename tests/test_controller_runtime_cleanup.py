# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_app.controller import Mind
from agent.harness.sessions.conversation import ConversationState
from agent.harness.sessions.root import RootConversationSession
from agent.domain.policies import preset_permissions
from infrastructure.persistence.conversation_history import LocalConversationHistory


def _root_session(
    state: ConversationState | None = None,
) -> tuple[RootConversationSession, SimpleNamespace]:
    transcript = SimpleNamespace(open=Mock(), append=Mock(), close=Mock())
    store = SimpleNamespace(
        ttl_ms=1000,
        max_items=10,
        touch_session=Mock(),
        archive_session=Mock(return_value={"status": "archived"}),
        unarchive_session=Mock(return_value={"status": "active"}),
    )
    lifecycle = SimpleNamespace(end=AsyncMock(return_value=True))
    shutdown_root = AsyncMock(return_value=(SimpleNamespace(
        thread=SimpleNamespace(sid="sid_child"),
    ),))
    hook_cleanup = AsyncMock()
    execution_cleanup = AsyncMock(return_value=True)
    command_cleanup = Mock()
    event_close = AsyncMock()

    async def fresh_preferences(_ttl_sec):
        return {"primary": {"model": "test-model"}}

    async def await_cleanup(awaitable):
        return await awaitable

    history = LocalConversationHistory(
        store,
        existing_transcript_path_for=lambda _sid: "",
        transcript_entries_for=lambda _path: (),
    )
    session = RootConversationSession(
        history,
        workspace=lambda: "D:/workspace",
        permissions=lambda: preset_permissions("auto"),
        preference_config=lambda: {"primary": {"model": "test-model"}},
        fresh_preferences=fresh_preferences,
        permission_grants=None,
        approval_ledger=None,
        output_record_path="D:/logs/output.log",
        transcript_factory=Mock(return_value=transcript),
        transcript_path_for=lambda _sid: "D:/sessions/session.jsonl",
        hook_scope_provider=SimpleNamespace(),
        session_lifecycle=lifecycle,
        subagent_shutdown=shutdown_root,
        hook_session_cleanup=hook_cleanup,
        execution_session_cleanup=execution_cleanup,
        command_hook_cleanup=command_cleanup,
        event_session_close=event_close,
        await_cleanup=await_cleanup,
    )
    if state is not None:
        session._state = state
    return session, SimpleNamespace(
        transcript=transcript,
        store=store,
        lifecycle=lifecycle,
        shutdown_root=shutdown_root,
        hook_cleanup=hook_cleanup,
        execution_cleanup=execution_cleanup,
        command_cleanup=command_cleanup,
        event_close=event_close,
    )


def test_controller_tracks_helix_tool_profile_with_link_state() -> None:
    controller = Mind.__new__(Mind)
    controller.service_mcp_linked = False
    controller.service_tool_profile = None
    controller.service_exec_env = None

    Mind.link_service_mcp(controller, {"paths": ["helix"]})

    assert Mind.tool_profile_for_turn(controller) == "app"
    assert controller.service_exec_env == {"paths": ["helix"]}

    Mind.set_service_tool_profile(controller, "api")
    assert Mind.tool_profile_for_turn(controller) == "api"

    Mind.unlink_service_mcp(controller)
    assert Mind.tool_profile_for_turn(controller) is None
    assert controller.service_tool_profile is None


def test_controller_rebuilds_workspace_tools_after_runtime_replacement(
    tmp_path,
) -> None:
    controller = Mind.__new__(Mind)
    controller.history_workspace = str(tmp_path / "previous")
    controller.workspace_runtime = SimpleNamespace(replace=Mock())
    controller.command_hook_sessions = SimpleNamespace(clear=Mock())
    client_tools = object()
    controller._build_client_tools = Mock(return_value=client_tools)

    workspace = Mind.set_history_workspace(controller, tmp_path / "current")

    normalized = controller.workspace_runtime.replace.call_args.args[0]
    assert workspace == normalized
    assert controller.history_workspace == normalized
    assert controller.client_tools is client_tools
    controller.command_hook_sessions.clear.assert_called_once_with()
    controller._build_client_tools.assert_called_once_with()


def test_controller_keeps_workspace_when_runtime_replacement_fails(
    tmp_path,
) -> None:
    controller = Mind.__new__(Mind)
    previous = str(tmp_path / "previous")
    controller.history_workspace = previous
    controller.workspace_runtime = SimpleNamespace(
        replace=Mock(side_effect=RuntimeError("replace failed")),
    )
    controller.command_hook_sessions = SimpleNamespace(clear=Mock())
    controller._build_client_tools = Mock()

    with pytest.raises(RuntimeError, match="replace failed"):
        Mind.set_history_workspace(controller, tmp_path / "current")

    assert controller.history_workspace == previous
    controller.command_hook_sessions.clear.assert_not_called()
    controller._build_client_tools.assert_not_called()


@pytest.mark.anyio
async def test_repeated_cancellation_forces_shielded_cleanup() -> None:
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_cancelled.set()

    async def wait_after_prior_cancellation() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        await Mind.await_cleanup(cleanup())

    waiting = asyncio.create_task(wait_after_prior_cancellation())
    await cleanup_started.wait()

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert cleanup_cancelled.is_set()


@pytest.mark.anyio
async def test_controller_stops_subagents_before_shared_resources() -> None:
    timeline = []
    controller = Mind.__new__(Mind)

    async def step(name):
        timeline.append(name)

    controller.subscription = SimpleNamespace(
        close=lambda: step("subscription"),
    )
    controller.service_runtime = SimpleNamespace(
        cancel_startup=lambda: step("service_startup"),
        close=lambda: step("service_runtime"),
    )
    controller.subagents = SimpleNamespace(
        shutdown=lambda: step("subagents"),
    )
    controller.command_hook_sessions = SimpleNamespace(
        clear=lambda: timeline.append("command_hooks"),
    )
    controller.hooks = SimpleNamespace(
        close=lambda: step("hooks"),
    )
    controller.event_reporting = SimpleNamespace(
        close=lambda: step("event_reporting"),
    )
    controller.workspace_runtime = SimpleNamespace(
        close=lambda: step("workspace_runtime"),
    )
    controller.external_mcp = SimpleNamespace(
        close=lambda: step("external_mcp"),
    )

    await Mind.close_runtime_resources(controller)

    assert timeline == [
        "subscription",
        "service_startup",
        "subagents",
        "command_hooks",
        "hooks",
        "event_reporting",
        "workspace_runtime",
        "external_mcp",
        "service_runtime",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("turn_count", [0, 2])
async def test_root_session_end_uses_current_snapshot(
    turn_count: int,
) -> None:
    session, resources = _root_session(ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=turn_count,
    ))
    session._lifecycle_id = 4
    session.remember_assistant_reply("final answer")

    ended = await session.end(reason="exit")

    assert ended is None
    call = resources.lifecycle.end.await_args
    assert call.args[0] == 4
    context = call.args[1]
    assert context.session_id == "sid_test_1_abcdef"
    assert context.root_session_id == "sid_test_1_abcdef"
    assert context.conversation_id == "cid_test_12345678"
    assert context.model == "test-model"
    assert call.kwargs["reason"] == "exit"
    assert call.kwargs["transcript_path"] == "D:/sessions/session.jsonl"
    assert call.kwargs["last_assistant_message"] == "final answer"
    resources.shutdown_root.assert_awaited_once_with(
        "sid_test_1_abcdef"
    )
    resources.hook_cleanup.assert_awaited_once_with("sid_child")
    assert [
        item.args[0]
        for item in resources.execution_cleanup.await_args_list
    ] == ["sid_child", "sid_test_1_abcdef"]
    resources.command_cleanup.assert_called_once_with(
        "sid_test_1_abcdef"
    )

    call.kwargs["before_dispatch"]()

    resources.transcript.open.assert_called_once_with()
    resources.transcript.append.assert_called_once_with(
        "session.ended",
        actor="system",
        payload={"reason": "exit"},
    )
    resources.transcript.close.assert_called_once_with()
    resources.event_close.assert_awaited_once_with(
        "cid_test_12345678",
        "sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_root_session_archive_migrates_then_ends_current_session() -> None:
    session, resources = _root_session(ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=1,
    ))
    events = []
    session.end = AsyncMock(
        side_effect=lambda **_kwargs: events.append("end")
    )
    resources.store.archive_session.side_effect = lambda **_kwargs: (
        events.append("archive")
        or {
            "cid": "cid_test_12345678",
            "sid": "sid_test_1_abcdef",
            "status": "archived",
        }
    )

    result = await session.archive_current()

    assert result["status"] == "archived"
    assert events == ["archive", "end"]
    session.end.assert_awaited_once_with(reason="archive")
    resources.store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_root_session_archive_rolls_back_when_lifecycle_end_fails() -> None:
    session, resources = _root_session(ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=1,
    ))
    session.end = AsyncMock(
        side_effect=RuntimeError("end failed")
    )

    with pytest.raises(RuntimeError, match="end failed"):
        await session.archive_current()

    resources.store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )
    resources.store.unarchive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_root_session_archive_rejects_unstarted_session() -> None:
    session, resources = _root_session()
    session.snapshot()
    session.end = AsyncMock()

    with pytest.raises(LookupError, match="session is not started"):
        await session.archive_current()

    session.end.assert_not_awaited()
    resources.store.archive_session.assert_not_called()


@pytest.mark.anyio
async def test_root_session_archive_allows_resumed_without_local_turn() -> None:
    session, resources = _root_session(ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    ))
    session.end = AsyncMock()
    resources.store.archive_session.return_value = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
        "status": "archived",
    }

    result = await session.archive_current()

    assert result["status"] == "archived"
    resources.store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )
    session.end.assert_awaited_once_with(reason="archive")


@pytest.mark.anyio
async def test_root_session_reuses_binding_for_same_session() -> None:
    conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=2,
    )
    session, _resources = _root_session(conversation)
    session._lifecycle_id = 4
    session.remember_assistant_reply("final answer")
    session.end = AsyncMock()

    metadata = await session.bind(
        "cid_test_12345678",
        "sid_test_1_abcdef",
        source="mcp_server",
    )

    assert metadata == {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    assert session._state is conversation
    assert session.turn_count == 2
    assert session._lifecycle_id == 4
    session.end.assert_not_awaited()


@pytest.mark.anyio
async def test_root_session_marks_new_binding_as_forkable_history() -> None:
    session, _resources = _root_session()
    session.end = AsyncMock()

    metadata = await session.bind(
        "cid_test_12345678",
        "sid_test_1_abcdef",
        source="tui:resume",
    )

    assert metadata == {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    assert session.turn_count == 0
    assert session.session_bound is True
    assert session.fork_source_available is True
