# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_app.controller import Mind
from mind_app.runtime.support.conversation import ConversationState
from mind_core.permissions import preset_permissions


@pytest.mark.anyio
async def test_controller_stops_subagents_before_shared_resources() -> None:
    timeline = []
    controller = Mind.__new__(Mind)

    async def step(name):
        timeline.append(name)

    controller.cancel_service_runtime_startup = (
        lambda: step("service_startup")
    )
    controller.subagents = SimpleNamespace(
        shutdown=lambda: step("subagents"),
    )
    controller.hook_registry = SimpleNamespace(
        close=lambda: step("hooks"),
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
        "service_startup",
        "subagents",
        "hooks",
        "native_coding",
        "external_mcp",
        "config_service",
        "keepalive",
        "report",
    ]


@pytest.mark.anyio
async def test_controller_session_end_uses_current_root_snapshot() -> None:
    controller = Mind.__new__(Mind)
    controller.conversation = ConversationState(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        turn_count=2,
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

    call.kwargs["before_dispatch"]()

    transcript.open.assert_called_once_with()
    transcript.append.assert_called_once_with(
        "session.ended",
        actor="system",
        payload={"reason": "exit"},
    )
    transcript.close.assert_called_once_with()


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
