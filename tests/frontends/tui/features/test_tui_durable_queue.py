# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from agent.application.turns.durable_queue import (
    DurableQueueSubmissionResult,
)
from agent.application.turns.commands import RemoteTurnRecovery
from agent.application.turns.commands import SessionRecoveryResult
from agent.application.turns.observation import TurnObservationCallbacks
from agent.application.turns.reviews import create_review_command
from agent.application.turns.run_result import RunResult
from agent.adapters.protocol.client import MindChatProtocolClient
from agent.domain import RecoveryAction
from agent.domain import RunStatus
from agent.domain.policies import preset_permissions
from agent.ports import ProtocolCommandClient
from agent.ports import ProtocolCommandError
from agent.ports import RunSnapshot
from agent.protocol import (
    DurableQueueInput,
    DurableQueueItem,
    DurableQueueMutationReceipt,
    LocalDurableQueueSnapshot,
    ModelStreamRequest,
    SubmitTurnCommand,
)
from frontends.tui.features.durable_queue import TuiDurableQueueFeature
from frontends.tui.features.durable_queue import DurableQueueDispatchResult
from frontends.tui.features.durable_queue import parse_durable_queue_command
from frontends.tui.session import loop as session_loop
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.session.dispatch import DispatchAction
from frontends.tui.session.dispatch import TuiCommandDispatcher
from frontends.tui.core.runtime import TuiRuntime
from protocol.schema.stream_events import TurnCompletedEvent
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCustomTarget,
)


def _request() -> ModelStreamRequest:
    """创建一项可供 TUI 观察的冻结 Queue 请求。"""
    return ModelStreamRequest(
        cid="cid_tui_queue",
        sid="sid_tui_queue_0001",
        turn_id="turn_tui_queue_0001",
        pref_config={"primary": {"model": "gpt-test", "apikey": "secret"}},
        message="queued request",
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
    """创建与远端 Queue Turn 坐标一致的本地冻结命令。"""
    return SubmitTurnCommand.create(
        command_id="command_tui_queue_0001",
        session_id="tui_queue_session_0001",
        run_id="run_tui_queue_0001",
        idempotency_key="intent_tui_queue_0001",
        message=request.message,
        pref_config=request.pref_config_value(),
        attachments=request.attachment_values(),
        extras={"source": "queue-test"},
        trace_context={
            "remote_turn": {
                "cid": request.cid,
                "sid": request.sid,
                "turn_id": request.turn_id,
            },
        },
    )


def _local(status: str = "started") -> LocalDurableQueueSnapshot:
    """创建一项已由服务端 start 的本地恢复快照。"""
    request = _request()
    return LocalDurableQueueSnapshot(
        submission_id="submission_tui_queue_0001",
        client_message_id="message_tui_queue_0001",
        add_request_id="request_tui_queue_add_0001",
        command=_command(request),
        request=request,
        status=status,
        revision=3,
        queue_version=2,
        start_request_id=(
            "request_tui_queue_start_0001"
            if status in {"starting", "started", "settled"}
            else None
        ),
        created_at="2026-09-05T00:00:00Z",
        updated_at="2026-09-05T00:00:01Z",
    )


@pytest.mark.parametrize(
    ("value", "name", "arguments"),
    (
        ("/queue", "list", ()),
        ("/queue list", "list", ()),
        ("/queue add Keep Original Case", "add", ("Keep Original Case",)),
        ("/queue retry submission_1", "retry", ("submission_1",)),
        ("/queue delete submission_1", "delete", ("submission_1",)),
        ("/queue move submission_1 2", "move", ("submission_1", "2")),
        ("/queue start", "start", ()),
        ("/queue start submission_1", "start", ("submission_1",)),
    ),
)
def test_queue_command_parser_preserves_explicit_intent(
    value: str,
    name: str,
    arguments: tuple[str, ...],
) -> None:
    command = parse_durable_queue_command(value)

    assert command.name == name
    assert command.arguments == arguments


@pytest.mark.parametrize(
    "value",
    ("/queue add", "/queue retry two ids", "/queue move item zero", "/queue unknown"),
)
def test_queue_command_parser_rejects_incomplete_or_unknown_intent(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="Usage"):
        parse_durable_queue_command(value)


class _FeatureAttachments:
    """保存 Queue feature 测试中的附件所有权。"""

    def __init__(self) -> None:
        self.values = [{"filename": "input.txt", "content": "draft"}]

    def consume_pending_attachments(self):
        values = self.values
        self.values = []
        return values

    def pending_attachments_snapshot(self):
        return list(self.values)

    def replace_pending_attachments(self, values) -> None:
        self.values = list(values)


class _FeatureState:
    """保存 Queue feature 测试中的扩展输入所有权。"""

    def __init__(self) -> None:
        self.pref_config = {
            "primary": {"model": "gpt-test", "apikey": "secret"},
        }
        self.permissions = preset_permissions("auto")
        self.extras = {"source": "draft"}

    def consume_pending_prompt_extras(self):
        extras = self.extras
        self.extras = {}
        return extras

    def replace_pending_prompt_extras(self, extras) -> None:
        self.extras = dict(extras)


def _feature_host(local: LocalDurableQueueSnapshot):
    """创建 Queue feature 所需的最小宿主边界。"""
    request = local.request
    item = DurableQueueItem(
        queue_seq=1,
        cid=request.cid,
        sid=request.sid,
        submission_id=local.submission_id,
        client_message_id=local.client_message_id,
        turn_id=request.turn_id,
        position=1,
        status="queued",
        input=DurableQueueInput(text=request.message),
        created_at=1.0,
        updated_at=1.0,
    )
    receipt = DurableQueueMutationReceipt(
        request_id=local.add_request_id,
        queue_version=1,
        item=item,
    )
    attachments = _FeatureAttachments()
    return SimpleNamespace(
        attach=attachments,
        conversation=SimpleNamespace(
            session_bound=True,
            snapshot=Mock(return_value={"cid": request.cid, "sid": request.sid}),
        ),
        durable_queue=SimpleNamespace(
            local_snapshot=AsyncMock(return_value=local),
        ),
        enqueue_durable_turn=AsyncMock(return_value=DurableQueueSubmissionResult(
            receipt=receipt,
            local=local,
        )),
    )


@pytest.mark.anyio
async def test_queue_add_transfers_structured_draft_once(monkeypatch) -> None:
    local = _local("queued")
    host = _feature_host(local)
    state = _FeatureState()
    runtime = SimpleNamespace(queue_background_block=Mock())
    monkeypatch.setattr(
        "frontends.tui.features.durable_queue.capture_active_turn_environment",
        lambda _host: {"snapshot_id": "queue-test"},
    )
    feature = TuiDurableQueueFeature(host, runtime, state)

    result = await feature.dispatch("/queue add queued request")

    assert result.started is None
    assert host.attach.values == []
    assert state.extras == {}
    host.enqueue_durable_turn.assert_awaited_once()
    submitted = host.enqueue_durable_turn.await_args.args[0]
    assert submitted.message == "queued request"
    assert submitted.attachment_values() == [
        {"filename": "input.txt", "content": "draft"},
    ]
    assert submitted.extras_value() == {"source": "draft"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("local_status", "restored"),
    (("deleted", True), ("adding", False)),
)
async def test_queue_add_restores_only_definitely_uncommitted_draft(
    monkeypatch,
    local_status: str,
    restored: bool,
) -> None:
    local = _local(local_status)
    host = _feature_host(local)
    state = _FeatureState()
    runtime = SimpleNamespace(queue_background_block=Mock())
    host.enqueue_durable_turn.side_effect = ProtocolCommandError(
        "queue_add_failed",
        "queue add failed",
        retryable=local_status == "adding",
        details={"status_code": 400 if local_status == "deleted" else 503},
    )
    monkeypatch.setattr(
        "frontends.tui.features.durable_queue.capture_active_turn_environment",
        lambda _host: {"snapshot_id": "queue-test"},
    )
    feature = TuiDurableQueueFeature(host, runtime, state)

    await feature.dispatch("/queue add queued request")

    assert bool(host.attach.values) is restored
    assert bool(state.extras) is restored


class _QueueTurnRuntime:
    """记录 Queue Turn 前台观察产生的 Runtime 调用。"""

    def __init__(self) -> None:
        self.set_turn_start_pending = Mock()
        self.append_submitted_query = Mock(return_value=True)
        self.consume_exit_request = Mock(return_value=None)
        self.begin_recovery_gate = Mock()
        self.finish_recovery_gate = Mock()
        self.queue_background_block = Mock()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "terminal_status", "should_settle"),
    (
        ("completed", "completed", True),
        ("interrupted", "interrupted", True),
        ("failed", "failed", True),
        ("cancelled", "cancelled", True),
        ("failed", None, False),
        ("reconciliation_required", None, False),
    ),
)
async def test_queue_turn_uses_observer_and_releases_only_on_terminal(
    monkeypatch,
    status: str,
    terminal_status: str | None,
    should_settle: bool,
) -> None:
    local = _local()
    runtime = _QueueTurnRuntime()

    async def observe_turn(_local, *, callbacks):
        if terminal_status is not None:
            callbacks.input_event(TurnCompletedEvent(
                type="turn.completed",
                cid=local.request.cid,
                sid=local.request.sid,
                turn_id=local.request.turn_id,
                event_seq=7,
                status=terminal_status,
                last_event_seq=7,
                completed_at=1.0,
            ))
        return RunResult(status=status)

    observe_turn_mock = AsyncMock(side_effect=observe_turn)
    settle = AsyncMock()
    host = SimpleNamespace(
        frontend=SimpleNamespace(application=SimpleNamespace(emit=Mock())),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(reset_patch_diff=Mock()),
        ),
        lifecycle=SimpleNamespace(
            stop_event=asyncio.Event(),
            request_stop=Mock(),
        ),
        durable_queue=SimpleNamespace(settle=settle),
        observe_durable_turn=observe_turn_mock,
    )
    dispatcher = SimpleNamespace(handle_stream_command=Mock())
    protocol_client = AsyncMock(spec=ProtocolCommandClient)

    async def execute_observer(_application, _runtime, turn, **_kwargs):
        return await turn

    monkeypatch.setattr(session_loop, "execute_tui_model_turn", execute_observer)

    outcome = await session_loop._execute_tui_durable_queue_turn(
        host,
        runtime,
        SimpleNamespace(),
        dispatcher,
        protocol_client,
        local,
    )

    assert outcome.settled is should_settle
    observe_turn_mock.assert_awaited_once()
    callbacks = observe_turn_mock.await_args.kwargs["callbacks"]
    assert isinstance(callbacks, TurnObservationCallbacks)
    if should_settle:
        settle.assert_awaited_once_with(local.submission_id)
    else:
        settle.assert_not_awaited()
    runtime.append_submitted_query.assert_called_once_with(
        local.command.message,
        local.request.turn_id,
        attachments=local.command.attachment_values(),
        extras=local.command.extras_value(),
    )


@pytest.mark.anyio
async def test_cold_run_recovery_replays_without_resubmitting_user_message(
    monkeypatch,
) -> None:
    """确保进程恢复只 attach 原 Turn，消费终态后才解除本地 Run 门禁。"""
    local = _local()
    snapshot = RunSnapshot(
        command=local.command,
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
        request=local.request,
        replay_target_seq=9,
    )
    runtime = _QueueTurnRuntime()
    result = RunResult(status="completed", assistant_text="offline answer")

    async def observe_turn(_recovery, *, callbacks):
        callbacks.input_event(TurnCompletedEvent(
            type="turn.completed",
            cid=local.request.cid,
            sid=local.request.sid,
            turn_id=local.request.turn_id,
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
        local_session_id="tui_review_session_0001",
        cid="cid_demo_12345678",
        sid="sid_demo_x_abcdef",
        turn_id="turn_review_01",
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={},
        environment_snapshot=None,
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
    runtime = _QueueTurnRuntime()
    result = RunResult(status="completed", assistant_text="No findings.")

    async def observe_review(request, **kwargs):
        kwargs["on_event"](TurnCompletedEvent(
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
    monkeypatch.setattr(
        session_loop,
        "run_observed_review_turn",
        review_observer,
    )
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

    outcome = await session_loop._execute_tui_recovered_turn(
        host,
        runtime,
        SimpleNamespace(),
        turn_application,
        MindChatProtocolClient(),
        recovery,
        dispatcher=None,
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
        local_session_id="tui_review_session_0001",
        cid="cid_demo_12345678",
        sid="sid_demo_x_abcdef",
        turn_id="turn_review_01",
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={},
        environment_snapshot=None,
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
    runtime = _QueueTurnRuntime()
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
    )

    assert ready is True
    execute_review.assert_awaited_once()
    assert execute_review.await_args.args[6] == command
    assert execute_review.await_args.kwargs["hint"] == (
        "Focus on lifecycle correctness."
    )
    assert reconcile.await_count == 2


@pytest.mark.anyio
async def test_cold_queue_recovery_keeps_gate_closed_until_snapshot_succeeds(
    monkeypatch,
) -> None:
    local = _local()
    runtime = _QueueTurnRuntime()
    runtime.wait_for_recovery_submit = AsyncMock()
    recover_started = AsyncMock(side_effect=[
        ProtocolCommandError(
            "queue_unavailable",
            "temporary failure",
            retryable=True,
        ),
        local,
    ])
    dispatcher = SimpleNamespace(
        durable_queue=SimpleNamespace(recover_started=recover_started),
    )
    host = SimpleNamespace(
        lifecycle=SimpleNamespace(stop_event=asyncio.Event()),
    )
    monkeypatch.setattr(session_loop, "RECOVERY_RETRY_DELAYS_SEC", (0.0,))

    ready, recovered = await session_loop._await_durable_queue_recovery(
        host,
        runtime,
        dispatcher,
    )

    assert ready
    assert recovered is local
    assert recover_started.await_count == 2
    runtime.begin_recovery_gate.assert_called_once_with()
    runtime.finish_recovery_gate.assert_called_once_with(submit_draft=True)
    runtime.queue_background_block.assert_called_once()


@pytest.mark.anyio
async def test_dispatcher_hands_started_queue_turn_to_loop_exactly_once() -> None:
    """确保 `/queue start` 只向 Session loop 交付一个观察快照。"""
    local = _local()
    runtime = TuiRuntime()
    host = SimpleNamespace(
        frontend=SimpleNamespace(application=SimpleNamespace(emit=Mock())),
    )
    dispatcher = TuiCommandDispatcher(
        host,
        runtime,
        SimpleNamespace(),
        TuiForegroundTasks(runtime, host),
        protocol_client=Mock(spec=ProtocolCommandClient),
    )
    dispatcher.durable_queue = SimpleNamespace(
        dispatch=AsyncMock(return_value=DurableQueueDispatchResult(local)),
    )

    action = await dispatcher.dispatch("/queue start")

    assert action is DispatchAction.DURABLE_QUEUE_TURN
    assert dispatcher.take_started_durable_queue_turn() is local
    with pytest.raises(RuntimeError, match="did not produce"):
        dispatcher.take_started_durable_queue_turn()


def test_runtime_appends_queue_query_and_payload_in_one_boundary() -> None:
    """确保 Queue 原始输入和冻结结构化载荷绑定到同一个 Turn。"""
    runtime = TuiRuntime()

    assert runtime.append_submitted_query(
        "queued request",
        "turn_tui_queue_0001",
        attachments=({"filename": "input.txt", "content": "frozen"},),
        extras={"source": "queue-test"},
    )

    block = runtime.document.blocks[-1]
    assert block.turn_id == "turn_tui_queue_0001"
    assert block.prompt == "queued request"
    assert block.attachments == (
        {"filename": "input.txt", "content": "frozen"},
    )
    assert block.extras == {"source": "queue-test"}
