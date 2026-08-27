# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_app.controller import Mind
from mind_app.runtime.support.conversation import ConversationState
from mind_core.permissions import preset_permissions


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

    controller.stop_subscription_listener = lambda: step("subscription")
    controller.cancel_service_runtime_startup = (
        lambda: step("service_startup")
    )
    controller.subagents = SimpleNamespace(
        shutdown=lambda: step("subagents"),
    )
    controller.command_hook_sessions = SimpleNamespace(
        clear=lambda: timeline.append("command_hooks"),
    )
    controller.hook_registry = SimpleNamespace(
        close=lambda: step("hooks"),
    )
    controller.event_reports = SimpleNamespace(
        close=lambda: step("event_reports"),
    )
    controller.native_coding = SimpleNamespace(
        close=lambda: step("native_coding"),
    )
    controller._native_coding_close_tasks = set()
    controller.stop_external_mcp_runtime = lambda: step("external_mcp")
    controller.stop_config_service = lambda: step("config_service")
    controller.stop_keepalive_supervisor = lambda: step("keepalive")
    controller.server_manager = None
    controller.stop_runtime_on_exit = False
    controller.report = SimpleNamespace(close=lambda: timeline.append("report"))

    await Mind.close_runtime_resources(controller)

    assert timeline == [
        "subscription",
        "service_startup",
        "subagents",
        "command_hooks",
        "hooks",
        "event_reports",
        "native_coding",
        "external_mcp",
        "config_service",
        "keepalive",
        "report",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("turn_count", [0, 2])
async def test_controller_session_end_uses_current_root_snapshot(
    turn_count: int,
) -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=turn_count,
    )
    controller._conversation_lifecycle_id = 4
    controller.last_assistant_reply = "final answer"
    controller.history_workspace = "D:/workspace"
    controller.pref = SimpleNamespace(
        to_config=lambda: {"primary": {"model": "test-model"}},
    )
    controller.permissions = preset_permissions("auto")
    controller.report = SimpleNamespace(output_record_path="D:/logs/output.log")
    transcript = SimpleNamespace(
        open=Mock(),
        append=Mock(),
        close=Mock(),
    )
    controller.transcripts = SimpleNamespace(
        path_for_session=lambda _sid: "D:/sessions/session.jsonl",
        writer=Mock(return_value=transcript),
    )
    controller.session_lifecycle = SimpleNamespace(
        end=AsyncMock(return_value=True),
    )
    controller.event_reports = SimpleNamespace(
        close_session=AsyncMock(),
    )
    controller.subagents = SimpleNamespace(
        shutdown_root=AsyncMock(return_value=(SimpleNamespace(
            thread=SimpleNamespace(sid="sid_child"),
        ),)),
    )
    controller.hook_registry = SimpleNamespace(
        cleanup_session=AsyncMock(),
    )
    controller.command_hook_sessions = SimpleNamespace(
        clear_root=Mock(),
    )
    controller.native_coding = SimpleNamespace(
        close_js_repl_session=AsyncMock(return_value=True),
    )

    ended = await Mind.end_conversation(controller, reason="exit")

    assert ended is None
    call = controller.session_lifecycle.end.await_args
    assert call.args[0] == 4
    context = call.args[1]
    assert context.session_id == "sid_test_1_abcdef"
    assert context.root_session_id == "sid_test_1_abcdef"
    assert context.conversation_id == "cid_test_12345678"
    assert context.model == "test-model"
    assert call.kwargs["reason"] == "exit"
    assert call.kwargs["transcript_path"] == "D:/sessions/session.jsonl"
    assert call.kwargs["last_assistant_message"] == "final answer"
    controller.subagents.shutdown_root.assert_awaited_once_with(
        "sid_test_1_abcdef"
    )
    controller.hook_registry.cleanup_session.assert_awaited_once_with(
        "sid_child"
    )
    assert [
        item.args[0]
        for item in controller.native_coding.close_js_repl_session.await_args_list
    ] == ["sid_child", "sid_test_1_abcdef"]
    controller.command_hook_sessions.clear_root.assert_called_once_with(
        "sid_test_1_abcdef"
    )

    call.kwargs["before_dispatch"]()

    transcript.open.assert_called_once_with()
    transcript.append.assert_called_once_with(
        "session.ended",
        actor="system",
        payload={"reason": "exit"},
    )
    transcript.close.assert_called_once_with()
    controller.event_reports.close_session.assert_awaited_once_with(
        "cid_test_12345678",
        "sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_controller_archive_migrates_then_ends_current_session() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=1,
    )
    events = []
    controller.end_conversation = AsyncMock(
        side_effect=lambda **_kwargs: events.append("end")
    )
    controller.history_store = SimpleNamespace(
        archive_session=Mock(
            side_effect=lambda **_kwargs: (
                events.append("archive")
                or {
                    "cid": "cid_test_12345678",
                    "sid": "sid_test_1_abcdef",
                    "status": "archived",
                }
            ),
        ),
        unarchive_session=Mock(),
    )

    result = await Mind.archive_conversation(controller)

    assert result["status"] == "archived"
    assert events == ["archive", "end"]
    controller.end_conversation.assert_awaited_once_with(reason="archive")
    controller.history_store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_controller_archive_rolls_back_when_lifecycle_end_fails() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=1,
    )
    controller.end_conversation = AsyncMock(
        side_effect=RuntimeError("end failed")
    )
    controller.history_store = SimpleNamespace(
        archive_session=Mock(return_value={"status": "archived"}),
        unarchive_session=Mock(return_value={"status": "active"}),
    )

    with pytest.raises(RuntimeError, match="end failed"):
        await Mind.archive_conversation(controller)

    controller.history_store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )
    controller.history_store.unarchive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )


@pytest.mark.anyio
async def test_controller_archive_rejects_unstarted_session() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState()
    controller.conversation.snapshot()
    controller.end_conversation = AsyncMock()
    controller.history_store = SimpleNamespace(archive_session=Mock())

    with pytest.raises(LookupError, match="session is not started"):
        await Mind.archive_conversation(controller)

    controller.end_conversation.assert_not_awaited()
    controller.history_store.archive_session.assert_not_called()


@pytest.mark.anyio
async def test_controller_archive_allows_resumed_session_without_local_turn() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )
    controller.end_conversation = AsyncMock()
    controller.history_store = SimpleNamespace(
        archive_session=Mock(return_value={
            "cid": "cid_test_12345678",
            "sid": "sid_test_1_abcdef",
            "status": "archived",
        }),
        unarchive_session=Mock(),
    )

    result = await Mind.archive_conversation(controller)

    assert result["status"] == "archived"
    controller.history_store.archive_session.assert_called_once_with(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
    )
    controller.end_conversation.assert_awaited_once_with(reason="archive")


@pytest.mark.anyio
async def test_controller_reuses_binding_for_same_session() -> None:
    controller = Mind.__new__(Mind)
    conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=2,
    )
    controller.conversation = conversation
    controller._conversation_lifecycle_id = 4
    controller.last_assistant_reply = "final answer"
    controller.end_conversation = AsyncMock()
    controller._touch_history_session = Mock()

    metadata = await Mind.bind_conversation(
        controller,
        "cid_test_12345678",
        "sid_test_1_abcdef",
        source="mcp_server",
    )

    assert metadata == {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    assert controller.conversation is conversation
    assert controller.conversation.turn_count == 2
    assert controller._conversation_lifecycle_id == 4
    controller.end_conversation.assert_not_awaited()


@pytest.mark.anyio
async def test_controller_marks_new_binding_as_forkable_history() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState()
    controller._conversation_lifecycle_id = 0
    controller.last_assistant_reply = ""
    controller.end_conversation = AsyncMock()
    controller._touch_history_session = Mock()

    metadata = await Mind.bind_conversation(
        controller,
        "cid_test_12345678",
        "sid_test_1_abcdef",
        source="tui:resume",
    )

    assert metadata == {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    assert controller.conversation.turn_count == 0
    assert controller.conversation.session_bound is True
    assert controller.conversation.fork_source_available is True
