# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from composition import ApplicationHost
from agent.domain.transcripts import TranscriptEntry
from agent.harness.process_resources import ProcessResourceOwner
from agent.harness.sessions.conversation import ConversationState
from agent.harness.sessions.root import RootConversationSession
from agent.domain.policies import preset_permissions
from agent.ports import TurnSessionContextPort
from infrastructure.persistence.conversation_history import LocalConversationHistory


def _root_session(
    state: ConversationState | None = None,
    *,
    transcript_entries: tuple[TranscriptEntry, ...] = (),
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
    javascript_cleanup = AsyncMock()
    command_cleanup = Mock()
    event_close = AsyncMock()

    async def fresh_preferences(_ttl_sec):
        return {"primary": {"model": "test-model"}}

    async def await_cleanup(awaitable):
        return await awaitable

    history = LocalConversationHistory(
        store,
        existing_transcript_path_for=lambda _sid: (
            "D:/sessions/session.jsonl" if transcript_entries else ""
        ),
        transcript_entries_for=lambda _path: transcript_entries,
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
        javascript_session_cleanup=javascript_cleanup,
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
        javascript_cleanup=javascript_cleanup,
        command_cleanup=command_cleanup,
        event_close=event_close,
    )


def test_controller_rebuilds_workspace_tools_after_runtime_replacement(
    tmp_path,
) -> None:
    controller = ApplicationHost.__new__(ApplicationHost)
    controller.history_workspace = str(tmp_path / "previous")
    controller.workspace_runtime = SimpleNamespace(replace=Mock())
    controller.command_hook_sessions = SimpleNamespace(clear=Mock())
    controller.execution = SimpleNamespace(rebuild_client_registry=Mock())

    workspace = ApplicationHost.set_history_workspace(
        controller,
        tmp_path / "current",
    )

    normalized = controller.workspace_runtime.replace.call_args.args[0]
    assert workspace == normalized
    assert controller.history_workspace == normalized
    controller.command_hook_sessions.clear.assert_called_once_with()
    controller.execution.rebuild_client_registry.assert_called_once_with()


def test_application_host_exposes_turn_session_context_port() -> None:
    controller = ApplicationHost.__new__(ApplicationHost)
    controller.activity = SimpleNamespace(enabled=False)
    controller.history_workspace = "D:/workspace"
    controller.hook_startup_warnings = ()
    controller.command_hook_sessions = SimpleNamespace()

    assert isinstance(controller, TurnSessionContextPort)
    assert controller.animate is False


def test_controller_keeps_workspace_when_runtime_replacement_fails(
    tmp_path,
) -> None:
    controller = ApplicationHost.__new__(ApplicationHost)
    previous = str(tmp_path / "previous")
    controller.history_workspace = previous
    controller.workspace_runtime = SimpleNamespace(
        replace=Mock(side_effect=RuntimeError("replace failed")),
    )
    controller.command_hook_sessions = SimpleNamespace(clear=Mock())
    controller.execution = SimpleNamespace(rebuild_client_registry=Mock())

    with pytest.raises(RuntimeError, match="replace failed"):
        ApplicationHost.set_history_workspace(controller, tmp_path / "current")

    assert controller.history_workspace == previous
    controller.command_hook_sessions.clear.assert_not_called()
    controller.execution.rebuild_client_registry.assert_not_called()


@pytest.mark.anyio
async def test_controller_stops_subagents_before_shared_resources() -> None:
    timeline = []
    async def step(name):
        timeline.append(name)

    resources = ProcessResourceOwner(
        close_subscription=lambda: step("subscription"),
        cancel_service_startup=lambda: step("service_startup"),
        shutdown_subagents=lambda: step("subagents"),
        close_approvals=lambda: step("approvals"),
        clear_command_hooks=lambda: timeline.append("command_hooks"),
        close_hooks=lambda: step("hooks"),
        close_javascript=lambda: step("javascript"),
        close_workspace=lambda: step("workspace_runtime"),
        close_execution=lambda: step("execution"),
        close_service=lambda: step("service_runtime"),
        observe_failure=Mock(),
    )

    await resources.close()

    assert timeline == [
        "subscription",
        "service_startup",
        "subagents",
        "approvals",
        "command_hooks",
        "hooks",
        "javascript",
        "workspace_runtime",
        "execution",
        "service_runtime",
    ]


@pytest.mark.anyio
async def test_process_resources_resume_at_failed_step() -> None:
    timeline = []
    startup_attempts = []
    observe_failure = Mock()

    async def step(name):
        timeline.append(name)

    async def cancel_startup():
        startup_attempts.append(None)
        timeline.append("service_startup")
        if len(startup_attempts) == 1:
            raise RuntimeError("startup cleanup failed")

    resources = ProcessResourceOwner(
        close_subscription=lambda: step("subscription"),
        cancel_service_startup=cancel_startup,
        shutdown_subagents=lambda: step("subagents"),
        close_approvals=lambda: step("approvals"),
        clear_command_hooks=lambda: timeline.append("command_hooks"),
        close_hooks=lambda: step("hooks"),
        close_javascript=lambda: step("javascript"),
        close_workspace=lambda: step("workspace_runtime"),
        close_execution=lambda: step("execution"),
        close_service=lambda: step("service_runtime"),
        observe_failure=observe_failure,
    )

    with pytest.raises(RuntimeError, match="startup cleanup failed"):
        await resources.close()
    failure = observe_failure.call_args.args[1]
    assert observe_failure.call_args.args[0] == "service_startup"
    assert isinstance(failure, RuntimeError)
    await resources.close()
    await resources.close()

    assert timeline == [
        "subscription",
        "service_startup",
        "service_startup",
        "subagents",
        "approvals",
        "command_hooks",
        "hooks",
        "javascript",
        "workspace_runtime",
        "execution",
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
    session.remember_assistant_reply("  final answer\r\n")

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
    snapshot = session.assistant_reply_snapshot()
    assert snapshot is not None
    assert snapshot.source == "  final answer\r\n"
    resources.shutdown_root.assert_awaited_once_with(
        "sid_test_1_abcdef"
    )
    resources.hook_cleanup.assert_awaited_once_with("sid_child")
    assert [
        item.args[0]
        for item in resources.javascript_cleanup.await_args_list
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


@pytest.mark.anyio
async def test_root_session_restores_latest_final_reply_snapshot() -> None:
    entries = (
        TranscriptEntry(
            timestamp="2026-09-07T00:00:00Z",
            event="message.created",
            session_id="sid_test_1_abcdef",
            turn_id="turn_1",
            actor="assistant",
            payload={"content": "analysis", "phase": "commentary"},
        ),
        TranscriptEntry(
            timestamp="2026-09-07T00:00:01Z",
            event="message.created",
            session_id="sid_test_1_abcdef",
            turn_id="turn_1",
            actor="assistant",
            payload={"content": "  final\r\n", "phase": "final_answer"},
        ),
    )
    session, _resources = _root_session(transcript_entries=entries)
    session.end = AsyncMock()

    await session.bind(
        "cid_test_12345678",
        "sid_test_1_abcdef",
        source="tui:resume",
    )

    snapshot = session.assistant_reply_snapshot()
    assert snapshot is not None
    assert snapshot.content == "final"
    assert snapshot.source == "  final\r\n"
