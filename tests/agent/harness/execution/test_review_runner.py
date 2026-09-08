# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import mind as application_composition
from agent.adapters.protocol.review_events import ReviewEventProjector
from agent.adapters.protocol.turn_source import SubmittingReviewTurnStreamSource
from agent.application.turns.reviews import (
    create_review_command,
    review_wire_tools,
)
from agent.application.turns.run_result import RunResult
from agent.harness.execution import review_runner as review_turns
from agent.harness.sessions.conversation import ConversationTurn
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    Viewport,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCustomTarget,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_harness_01"


class _ApplicationSink(ApplicationSink):
    """记录 harness 交付的应用视图。"""

    def __init__(self) -> None:
        self.views: list[ApplicationView] = []

    @property
    def viewport(self) -> Viewport:
        return Viewport(width=100, height=30)

    def emit(self, view: ApplicationView) -> None:
        self.views.append(view)


class _Lifecycle:
    """记录 Review 前台生命周期的调用顺序。"""

    def __init__(self, application: ApplicationSink) -> None:
        self.application = application
        self.animate = True
        self.actions: list[str] = []

    def begin_terminal_progress(self) -> None:
        self.actions.append("begin")

    def end_terminal_progress(self) -> None:
        self.actions.append("end")

    async def start_animation(self) -> None:
        self.actions.append("start_animation")

    def emit_worked_footer(self, elapsed_seconds: float) -> None:
        _ = elapsed_seconds
        self.actions.append("worked")

    async def stop_animation(self) -> None:
        self.actions.append("stop_animation")

    async def await_cleanup(self, awaitable) -> None:
        await awaitable


class _ReportHandle:
    """提供 execute_turn 所需的报告租约。"""

    def __init__(self) -> None:
        self.report = SimpleNamespace()
        self.released: list[bool] = []

    async def release(self, *, interrupted: bool = False) -> None:
        self.released.append(interrupted)


class _ExecutionRuntime:
    """提供可控 MCP 工具目录和报告生命周期。"""

    def __init__(self, tools: list[dict]) -> None:
        self.tools = tools
        self.handle = _ReportHandle()
        self.event_reporting = SimpleNamespace(
            acquire=AsyncMock(return_value=self.handle),
        )

    async def with_mcp_session(self, pref_config, callback):
        _ = pref_config
        return await callback(SimpleNamespace(), list(self.tools))

    def tool_profile_for_turn(self):
        raise AssertionError("Review mode must be explicit")

    @staticmethod
    async def await_cleanup(awaitable):
        return await awaitable


class _ReviewProtocolClient:
    """满足 Review 提交和观察能力的最小协议客户端。"""

    async def review(self, request, **_kwargs):
        raise AssertionError(f"stream_turn owns Review submission: {request}")

    def observe_review(self, request, **_kwargs):
        raise AssertionError(f"stream_turn owns Review observation: {request}")


def _catalog() -> list[dict]:
    return [
        {
            "name": name,
            "description": name,
            "inputSchema": {"type": "object"},
            "meta": {
                "client_builtin": True,
                "domain": "coding",
                "class": "review_read",
                "review_read_only": True,
            },
        }
        for name in (
            "read_file",
            "read_repository",
            "apply_patch",
        )
    ]


def _command():
    tools = review_wire_tools(_catalog())
    return create_review_command(
        local_session_id="review_harness_session",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={"primary": {"model": "test-model"}},
        environment_snapshot={"PATH": "D:/tools"},
        tools=tools,
    )


@pytest.mark.anyio
async def test_review_runner_uses_standard_harness_and_foreground_lifecycle(
    monkeypatch,
) -> None:
    """证明 Review 生产入口复用标准 MCP、前台和 stream_turn 生命周期。"""
    command = _command()
    lifecycle = _Lifecycle(_ApplicationSink())
    runtime = _ExecutionRuntime(_catalog())
    session = SimpleNamespace(
        workspace_root="D:/workspace",
        permission_grants=None,
        approval_ledger=None,
        output_record_path="D:/logs/output.log",
        snapshot=Mock(return_value={"cid": CID, "sid": SID}),
        begin_turn=AsyncMock(return_value=ConversationTurn(
            cid=CID,
            sid=SID,
            turn_index=1,
            session_started=False,
            start_reason="",
        )),
        transcript_path_for_session=(
            lambda _sid: "D:/sessions/session.jsonl"
        ),
    )
    captured = {}

    async def stream_turn(*_args, **kwargs):
        lifecycle.actions.append("stream")
        captured.update(kwargs)
        return RunResult(status="completed", assistant_text="No findings.")

    monkeypatch.setattr(review_turns, "stream_turn", stream_turn)
    protocol_client = _ReviewProtocolClient()

    result = await review_turns.run_review_turn(
        session,
        {"primary": {"model": "test-model"}},
        command.request,
        command.environment_snapshot_value(),
        hint="current changes",
        review_capability=protocol_client,
        review_observer=protocol_client,
        protocol_client=protocol_client,
        effect_journal_factory=Mock(),
        tool_execution=SimpleNamespace(),
        execution_runtime=runtime,
        lifecycle=lifecycle,
    )

    assert result.status == "completed"
    session.begin_turn.assert_awaited_once_with(
        cid=CID,
        sid=SID,
        title="Review current changes",
        source="review",
    )
    execution = captured["turn_execution"]
    assert execution.context.permissions.sandbox_mode == "read-only"
    assert execution.context.permissions.approval_policy == "never"
    assert execution.hook_scope.has_matching("UserPromptSubmit") is False
    assert isinstance(captured["turn_source"], SubmittingReviewTurnStreamSource)
    assert isinstance(captured["event_projection"], ReviewEventProjector)
    assert {tool["name"] for tool in captured["tools"]} == {
        "read_file",
        "read_repository",
    }
    assert captured["exec_env"] == {"PATH": "D:/tools"}
    assert lifecycle.actions == [
        "begin",
        "start_animation",
        "stream",
        "worked",
        "stop_animation",
        "end",
    ]
    assert runtime.handle.released == [False]


@pytest.mark.anyio
async def test_composition_binds_review_runner_dependencies(monkeypatch) -> None:
    """证明 TUI 可从普通根轮次绑定对象调用完整 Review 用例。"""
    run_review_turn = AsyncMock(return_value=RunResult(status="completed"))
    monkeypatch.setattr(
        application_composition,
        "run_review_turn",
        run_review_turn,
    )
    protocol_client = _ReviewProtocolClient()
    runtime_services = SimpleNamespace(
        model_capability=object(),
        protocol_client=protocol_client,
        create_effect_journal=Mock(),
        tool_execution=object(),
    )
    conversation = SimpleNamespace(transcript_factory=Mock())
    lifecycle = _Lifecycle(_ApplicationSink())
    controller = SimpleNamespace(
        conversation=conversation,
        approval_coordinator=object(),
        workspace_runtime=SimpleNamespace(execution_policy=object()),
        execution=object(),
        turn_foreground_lifecycle=lifecycle,
        frontend=SimpleNamespace(session_factory=Mock()),
    )
    command = _command()

    runner = application_composition.bind_root_turn_runner(runtime_services)
    await runner.review(
        controller,
        {"primary": {"model": "test-model"}},
        request=command.request,
        environment_snapshot=command.environment_snapshot_value(),
        hint="current changes",
    )

    assert run_review_turn.await_args.args[:4] == (
        conversation,
        {"primary": {"model": "test-model"}},
        command.request,
        {"PATH": "D:/tools"},
    )
    assert run_review_turn.await_args.kwargs["review_capability"] is protocol_client
    assert run_review_turn.await_args.kwargs["review_observer"] is protocol_client
    assert run_review_turn.await_args.kwargs["protocol_client"] is protocol_client
    assert run_review_turn.await_args.kwargs["execution_runtime"] is (
        controller.execution
    )
    assert run_review_turn.await_args.kwargs["lifecycle"] is lifecycle
    assert "approval_ledger" not in run_review_turn.await_args.kwargs
