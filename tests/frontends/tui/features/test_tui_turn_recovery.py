# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.application.turns.commands import RemoteTurnRecovery
from agent.application.turns.commands import SessionRecoveryResult
from agent.application.turns.reviews import create_review_command
from agent.application.turns.run_result import RunResult
from agent.domain import RecoveryAction
from agent.domain import RunStatus
from agent.ports import ProtocolCommandClient
from agent.ports import RunSnapshot
from agent.protocol import (
    ModelStreamRequest,
    SubmitTurnCommand,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.session import loop as session_loop
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCustomTarget,
)
from protocol.schema.stream_events import TurnCompletedEvent


def _review_tools():
    """返回恢复测试使用的冻结只读工具目录。"""
    return tuple({
        "name": name,
        "description": f"Run the frozen read-only {name} tool.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    } for name in ("exec_command", "write_stdin"))


def _request() -> ModelStreamRequest:
    """创建一项可供 TUI 恢复观察的冻结请求。"""
    return ModelStreamRequest(
        cid="cid_tui_recovery",
        sid="sid_tui_recovery_0001",
        turn_id="turn_tui_recovery_0001",
        pref_config={"primary": {"model": "gpt-test", "apikey": "secret"}},
        message="submitted request",
        tools=(),
        attachments=({"filename": "input.txt", "content": "frozen"},),
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
        },
    )


def _command(request: ModelStreamRequest) -> SubmitTurnCommand:
    """创建与远端 Turn 坐标一致的本地冻结命令。"""
    return SubmitTurnCommand.create(
        command_id="command_tui_recovery_0001",
        session_id="tui_recovery_session_0001",
        run_id="run_tui_recovery_0001",
        idempotency_key="intent_tui_recovery_0001",
        message=request.message,
        pref_config=request.pref_config_value(),
        attachments=request.attachment_values(),
        extras={"source": "recovery-test"},
        trace_context={
            "remote_turn": {
                "cid": request.cid,
                "sid": request.sid,
                "turn_id": request.turn_id,
            },
        },
    )


class _RecoveryRuntime:
    """记录恢复观察产生的 Runtime 调用。"""

    def __init__(self) -> None:
        self.set_turn_start_pending = Mock()
        self.append_submitted_query = Mock(return_value=True)
        self.consume_exit_request = Mock(return_value=None)
        self.begin_recovery_gate = Mock()
        self.finish_recovery_gate = Mock()
        self.queue_background_block = Mock()


@pytest.mark.anyio
async def test_cold_run_recovery_replays_without_resubmitting_user_message(
    monkeypatch,
) -> None:
    """确保进程恢复只 attach 原 Turn，消费终态后才解除本地 Run 门禁。"""
    request = _request()
    snapshot = RunSnapshot(
        command=_command(request),
        status=RunStatus.RECONCILIATION_REQUIRED,
        sequence=3,
        snapshot_version=1,
        effect_id="",
        effect_status="reconciliation_required",
        recovery_action=RecoveryAction.RECONCILE,
        updated_at="2026-09-05T00:00:00Z",
    )
    recovery = RemoteTurnRecovery(
        snapshot=snapshot,
        request=request,
        replay_target_seq=9,
    )
    runtime = _RecoveryRuntime()
    result = RunResult(status="completed", assistant_text="offline answer")

    async def observe_turn(_recovery, *, callbacks):
        callbacks.input_event(TurnCompletedEvent(
            type="turn.completed",
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            event_seq=9,
            status="completed",
            last_event_seq=9,
            completed_at=1.0,
        ))
        return result

    observe_turn_mock = AsyncMock(side_effect=observe_turn)
    resolve_recovery = AsyncMock()
    host = SimpleNamespace(
        frontend=SimpleNamespace(application=SimpleNamespace(emit=Mock())),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        lifecycle=SimpleNamespace(
            stop_event=asyncio.Event(),
            request_stop=Mock(),
        ),
        observe_recovered_turn=observe_turn_mock,
    )
    turn_application = SimpleNamespace(
        resolve_observed_recovery=resolve_recovery,
    )

    async def execute_observer(_application, _runtime, turn, **_kwargs):
        return await turn

    monkeypatch.setattr(session_loop, "execute_tui_model_turn", execute_observer)

    outcome = await session_loop._execute_tui_recovered_turn(
        host,
        runtime,
        SimpleNamespace(),
        turn_application,
        AsyncMock(spec=ProtocolCommandClient),
        recovery,
        dispatcher=None,
    )

    assert outcome.settled is True
    observe_turn_mock.assert_awaited_once()
    resolve_recovery.assert_awaited_once_with(recovery, result)
    runtime.append_submitted_query.assert_not_called()


@pytest.mark.anyio
async def test_cold_review_recovery_uses_review_observer_without_resubmit(
    monkeypatch,
) -> None:
    """确保 Review 冷恢复不进入只接受聊天请求的普通恢复器。"""
    command = create_review_command(
        session_mode="existing",
        local_session_id="tui_review_session_0001",
        cid="cid_demo_12345678",
        sid="sid_demo_x_abcdef",
        turn_id="turn_review_01",
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={},
        environment_snapshot=None,
        tools=_review_tools(),
    )
    snapshot = RunSnapshot(
        command=command,
        status=RunStatus.RECONCILIATION_REQUIRED,
        sequence=3,
        snapshot_version=1,
        effect_id="",
        effect_status="reconciliation_required",
        recovery_action=RecoveryAction.RECONCILE,
        updated_at="2026-09-08T00:00:00Z",
    )
    recovery = RemoteTurnRecovery(
        snapshot=snapshot,
        request=command.request,
        replay_target_seq=3,
    )
    runtime = _RecoveryRuntime()
    result = RunResult(status="completed", assistant_text="No findings.")

    async def observe_review(_pref_config, *, request, callbacks, **_kwargs):
        callbacks.input_event(TurnCompletedEvent(
            type="turn.completed",
            cid=request.cid,
            sid=request.sid,
            turn_id=request.turn_id,
            event_seq=3,
            status="completed",
            last_event_seq=3,
            completed_at=1.0,
        ))
        return result

    review_observer = AsyncMock(side_effect=observe_review)
    chat_observer = AsyncMock()
    host = SimpleNamespace(
        frontend=SimpleNamespace(application=SimpleNamespace(emit=Mock())),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        lifecycle=SimpleNamespace(
            stop_event=asyncio.Event(),
            request_stop=Mock(),
        ),
        observe_recovered_turn=chat_observer,
    )
    turn_application = SimpleNamespace(
        resolve_observed_recovery=AsyncMock(),
    )

    async def execute_observer(_application, _runtime, turn, **_kwargs):
        return await turn

    monkeypatch.setattr(session_loop, "execute_tui_model_turn", execute_observer)

    state = SimpleNamespace(
        refresh_preferences=AsyncMock(return_value={}),
    )
    outcome = await session_loop._execute_tui_recovered_turn(
        host,
        runtime,
        state,
        turn_application,
        MindChatProtocolClient(),
        recovery,
        dispatcher=None,
        review_turn_runner=review_observer,
    )

    assert outcome.settled is True
    review_observer.assert_awaited_once()
    assert review_observer.await_args.kwargs["replay_target_seq"] == 3
    assert review_observer.await_args.kwargs["hint"] == (
        "Focus on lifecycle correctness."
    )
    chat_observer.assert_not_awaited()
    turn_application.resolve_observed_recovery.assert_awaited_once_with(
        recovery,
        result,
    )


@pytest.mark.anyio
async def test_queued_review_recovery_redispatches_exact_frozen_command(
    monkeypatch,
) -> None:
    """确保网络前退出后由原 Review Command 继续执行。"""
    command = create_review_command(
        session_mode="existing",
        local_session_id="tui_review_session_0001",
        cid="cid_demo_12345678",
        sid="sid_demo_x_abcdef",
        turn_id="turn_review_01",
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={},
        environment_snapshot=None,
        tools=_review_tools(),
    )
    pending = RunSnapshot(
        command=command,
        status=RunStatus.QUEUED,
        sequence=1,
        snapshot_version=1,
        effect_id="",
        effect_status="",
        recovery_action=RecoveryAction.REDISPATCH,
        updated_at="2026-09-08T00:00:00Z",
    )
    reconcile = AsyncMock(side_effect=(
        SessionRecoveryResult(
            pending=(pending,),
            restore_commands=(),
            resolved_run_ids=(),
            redispatch_reviews=(command,),
        ),
        SessionRecoveryResult((), (), ()),
    ))
    turn_application = SimpleNamespace(reconcile_remote_session=reconcile)
    execute_review = AsyncMock(return_value=session_loop._TurnExecutionOutcome(
        settled=True,
        exit_requested=False,
    ))
    monkeypatch.setattr(
        session_loop,
        "_execute_tui_review_command",
        execute_review,
    )
    runtime = _RecoveryRuntime()
    host = SimpleNamespace(
        lifecycle=SimpleNamespace(stop_event=asyncio.Event()),
    )
    protocol_client = MindChatProtocolClient()

    ready = await session_loop._await_durable_session_recovery(
        host,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(emit=Mock()),
        turn_application,
        protocol_client,
        session_id=command.session_id,
        review_capability=protocol_client,
        review_turn_runner=AsyncMock(),
    )

    assert ready is True
    execute_review.assert_awaited_once()
    assert execute_review.await_args.args[5] == command
    assert execute_review.await_args.kwargs["hint"] == (
        "Focus on lifecycle correctness."
    )
    assert reconcile.await_count == 2


def test_runtime_appends_submitted_query_and_payload_in_one_boundary() -> None:
    """确保原始输入和冻结结构化载荷绑定到同一个 Turn。"""
    runtime = TuiRuntime()

    assert runtime.append_submitted_query(
        "submitted request",
        "turn_tui_recovery_0001",
        attachments=({"filename": "input.txt", "content": "frozen"},),
        extras={"source": "recovery-test"},
    )

    block = runtime.document.blocks[-1]
    assert block.turn_id == "turn_tui_recovery_0001"
    assert block.prompt == "submitted request"
    assert block.attachments == (
        {"filename": "input.txt", "content": "frozen"},
    )
    assert block.extras == {"source": "recovery-test"}
