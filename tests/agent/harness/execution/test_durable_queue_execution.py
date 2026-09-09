# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.run_result import RunResult
from agent.domain.policies import preset_permissions
from agent.harness.execution import durable_queue as queue_execution
from agent.harness.execution import observed_turn as observed_execution
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.hooks.runtime import HookRuntime
from agent.protocol import (
    LocalDurableQueueSnapshot,
    ModelStreamRequest,
    SubmitTurnCommand,
)


class _HookScopeProvider:
    """为 Queue 执行测试创建不含活动 Hook 的固定作用域。"""

    def hook_scope(self, context: HookExecutionContext) -> HookExecutionScope:
        return HookExecutionScope.empty(context)


class _SessionContext:
    """提供构建冻结 Queue 请求所需的会话输入。"""

    @property
    def animate(self) -> bool:
        return True

    @property
    def workspace_root(self) -> str:
        return "D:/workspace"

    @property
    def hook_startup_warnings(self) -> tuple[str, ...]:
        return ()

    @property
    def command_hook_sessions(self):
        return None

    def capture_environment(self, **_kwargs):
        return None

    def skills_payload(self):
        return [{"name": "queue-skill"}]


class _RootSession:
    """实现 Queue 根轮次准备与会话隔离所需的最小接口。"""

    workspace_root = "D:/workspace"
    permission_grants = None
    output_record_path = "D:/logs/output.log"
    permissions = preset_permissions("auto")
    approval_ledger = None
    hook_scope_provider = _HookScopeProvider()

    def __init__(self) -> None:
        self.begin_turn = AsyncMock(return_value=SimpleNamespace(
            cid="cid_queue_execution",
            sid="sid_queue_execution",
            session_started=False,
            start_reason="",
            additional_context=("session context",),
            system_message="queue system",
            metadata=lambda: {
                "cid": "cid_queue_execution",
                "sid": "sid_queue_execution",
            },
        ))

    def transcript_path_for_session(self, sid: str) -> str:
        return f"D:/sessions/{sid}.jsonl"

    def snapshot(self) -> dict[str, str]:
        return {
            "cid": "cid_queue_execution",
            "sid": "sid_queue_execution",
        }


class _ExecutionRuntime:
    """在固定工具目录上执行 Queue 请求冻结回调。"""

    def __init__(self) -> None:
        self.event_reporting = SimpleNamespace()

    async def with_mcp_session(self, pref_config, callback):
        assert pref_config == {
            "primary": {"model": "gpt-test", "apikey": "secret"},
        }
        return await callback(SimpleNamespace(), [
            {"name": "read_file", "type": "function"},
            {
                "name": "hidden_tool",
                "type": "function",
                "meta": {"hidden": True},
            },
        ])

    def tool_profile_for_turn(self):
        return None

    async def await_cleanup(self, awaitable):
        return await awaitable


def _command() -> SubmitTurnCommand:
    """创建与既有服务端 Session 绑定的 Queue 命令。"""
    return SubmitTurnCommand.create(
        command_id="command_queue_execution_0001",
        session_id="local_queue_execution",
        run_id="run_queue_execution_0001",
        message="queued input",
        attachments=({"filename": "input.txt", "content": "frozen"},),
        environment_snapshot={"snapshot_id": "env_queue_execution"},
        pref_config={
            "primary": {"model": "gpt-test", "apikey": "secret"},
        },
        extras={"priority": "normal"},
        trace_context={
            "remote_turn": {
                "cid": "cid_queue_execution",
                "sid": "sid_queue_execution",
                "turn_id": "turn_queue_execution_0001",
            },
        },
    )


def _started_local() -> LocalDurableQueueSnapshot:
    """创建 queue.start 已确认的本地冻结快照。"""
    command = _command()
    request = ModelStreamRequest(
        cid="cid_queue_execution",
        sid="sid_queue_execution",
        turn_id="turn_queue_execution_0001",
        pref_config=command.pref_config_value(),
        message=command.message,
        tools=({"name": "read_file", "type": "function"},),
        attachments=command.attachment_values(),
        environment_snapshot=command.environment_snapshot_value(),
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "network_access": "restricted",
            },
            "extras": command.extras_value(),
            "additional_context": ["frozen session context"],
            "system_message": "frozen queue system",
        },
    )
    return LocalDurableQueueSnapshot(
        submission_id="submission_queue_execution_0001",
        client_message_id="message_queue_execution_0001",
        add_request_id="request_queue_add_execution_0001",
        command=command,
        request=request,
        status="started",
        revision=3,
        queue_version=2,
        start_request_id="request_queue_start_execution_0001",
        created_at="2026-09-04T00:00:00Z",
        updated_at="2026-09-04T00:00:01Z",
    )


@pytest.mark.anyio
async def test_enqueue_freezes_session_hooks_tools_and_request_before_add() -> None:
    """确保 Queue add 接收的是唯一完整的冻结模型请求。"""
    session = _RootSession()
    expected = SimpleNamespace(status="queued")
    queue_application = SimpleNamespace(enqueue=AsyncMock(return_value=expected))

    result = await queue_execution.enqueue_durable_root_turn(
        session,
        queue_application,
        _command(),
        permissions=preset_permissions("auto"),
        execution_runtime=_ExecutionRuntime(),
        session_context=_SessionContext(),
        submission_id="submission_queue_execution_0001",
        client_message_id="message_queue_execution_0001",
        request_id="request_queue_add_execution_0001",
    )

    assert result is expected
    session.begin_turn.assert_not_awaited()
    call = queue_application.enqueue.await_args
    frozen = call.args[1]
    assert isinstance(frozen, ModelStreamRequest)
    assert frozen.cid == "cid_queue_execution"
    assert frozen.sid == "sid_queue_execution"
    assert frozen.turn_id == "turn_queue_execution_0001"
    assert frozen.message == "queued input"
    assert frozen.tool_values() == [
        {"name": "read_file", "type": "function"},
    ]
    assert frozen.option_values() == {
        "session_mode": "existing",
        "extras": {"priority": "normal"},
        "skills": [{"name": "queue-skill"}],
        "permissions": {
            "sandbox_mode": "workspace-write",
            "approval_policy": "on-request",
            "approvals_reviewer": "user",
            "network_access": "restricted",
        },
    }
    assert call.kwargs == {
        "submission_id": "submission_queue_execution_0001",
        "client_message_id": "message_queue_execution_0001",
        "request_id": "request_queue_add_execution_0001",
    }


@pytest.mark.anyio
async def test_observe_started_queue_turn_uses_frozen_tool_names(
    monkeypatch,
) -> None:
    """确保 queue.start 后只 attach，且新出现的本地工具不会泄漏到旧请求。"""
    captured = {}

    async def execute_turn(
        _runtime,
        pref_config,
        execution,
        operation,
        *,
        event_report=None,
    ):
        assert event_report is None
        return await operation(
            execution,
            SimpleNamespace(),
            [
                {"name": "read_file", "type": "function"},
                {"name": "late_tool", "type": "function"},
            ],
            SimpleNamespace(),
        )

    async def run_foreground_turn(_lifecycle, operation, **kwargs):
        captured.update(kwargs)
        captured["operation"] = operation
        return RunResult(status="completed", assistant_text="observed")

    monkeypatch.setattr(observed_execution, "execute_turn", execute_turn)
    monkeypatch.setattr(
        observed_execution,
        "run_foreground_turn",
        run_foreground_turn,
    )
    model_capability = SimpleNamespace(stream=Mock())
    turn_observer = SimpleNamespace(observe=Mock())

    session = _RootSession()
    result = await queue_execution.observe_durable_root_turn(
        session,
        _started_local(),
        model_capability=model_capability,
        turn_observer=turn_observer,
        protocol_client=SimpleNamespace(),
        effect_journal_factory=Mock(),
        tool_execution=SimpleNamespace(),
        execution_runtime=_ExecutionRuntime(),
    )

    assert result == RunResult(status="completed", assistant_text="observed")
    session.begin_turn.assert_not_awaited()
    assert captured["operation"] is observed_execution.observe_stream_turn
    assert captured["tools"] == [
        {"name": "read_file", "type": "function"},
    ]
    assert captured["model_capability"] is model_capability
    assert captured["turn_observer"] is turn_observer
    execution = captured["turn_execution"]
    assert execution.context.turn_id == "turn_queue_execution_0001"
    assert execution.context.session_started is False
    assert execution.context.permissions == preset_permissions("auto")
    assert execution.additional_context == ("frozen session context",)
    assert execution.system_message == "frozen queue system"


@pytest.mark.anyio
async def test_observe_queue_turn_rejects_unstarted_or_foreign_session() -> None:
    """确保没有 start receipt 或当前会话已切换时不能建立 observer。"""
    local = _started_local()
    unstarted = LocalDurableQueueSnapshot(
        submission_id=local.submission_id,
        client_message_id=local.client_message_id,
        add_request_id=local.add_request_id,
        command=local.command,
        request=local.request,
        status="queued",
        revision=2,
        queue_version=1,
        start_request_id=None,
        created_at=local.created_at,
        updated_at=local.updated_at,
    )
    arguments = {
        "model_capability": SimpleNamespace(stream=Mock()),
        "turn_observer": SimpleNamespace(observe=Mock()),
        "protocol_client": SimpleNamespace(),
        "effect_journal_factory": Mock(),
        "tool_execution": SimpleNamespace(),
        "execution_runtime": _ExecutionRuntime(),
    }

    with pytest.raises(ValueError, match="has not started"):
        await queue_execution.observe_durable_root_turn(
            _RootSession(),
            unstarted,
            **arguments,
        )

    foreign_session = _RootSession()
    foreign_session.snapshot = lambda: {
        "cid": "cid_other",
        "sid": "sid_other",
    }
    with pytest.raises(RuntimeError, match="another session"):
        await queue_execution.observe_durable_root_turn(
            foreign_session,
            local,
            **arguments,
        )


@pytest.mark.parametrize(
    "options",
    (
        {"additional_context": [1]},
        {"additional_context": "not-a-list"},
        {"system_message": ["not-a-string"]},
    ),
)
def test_observe_queue_turn_rejects_invalid_frozen_context(options) -> None:
    """确保 observer 不以当前 Session 状态修补损坏的冻结上下文。"""
    with pytest.raises(ValueError, match="observed turn"):
        observed_execution._request_turn_context(options)
