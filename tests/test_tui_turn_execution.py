# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from agent.application.turns.run_result import RunResult
from agent.application.turns.context import TurnContext
from agent.harness.hooks.runtime import HookRuntime
from agent.application.hooks.context import HookExecutionContext
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.sessions.conversation import ConversationTurn
from protocol.client.reports import EventReportRuntimeOwner
from frontends.tui.session import turn as tui_turn
from frontends.tui.session.turn import run_tui_model_turn
from agent.domain.policies import preset_permissions


class _Report:
    def __init__(self, events) -> None:
        self.events = events
        self.opened = 0
        self.closed = []

    async def open(self) -> None:
        self.events.append("report.open")
        self.opened += 1

    async def close(self, *, drain: bool = True) -> None:
        self.events.append("report.close")
        self.closed.append(drain)


class _ReportPool:
    def __init__(self, report: _Report) -> None:
        self.report = report

    async def acquire(self, _cid: str, _sid: str) -> _Report:
        await self.report.open()
        return self.report

    async def close_session(
        self,
        _cid: str,
        _sid: str,
        *,
        drain: bool = True,
    ) -> None:
        await self.report.close(drain=drain)

    async def close(self, *, drain: bool = True) -> None:
        await self.report.close(drain=drain)


class _Attachments:
    def __init__(self, items) -> None:
        self.items = list(items)
        self.consumed = 0

    def has_pending_attachments(self) -> bool:
        return bool(self.items)

    def consume_pending_attachments(self):
        self.consumed += 1
        return list(self.items)


class _TuiController:
    def __init__(self, *, attachments=(), failure=None) -> None:
        self.events = []
        self.attach = _Attachments(attachments)
        self.failure = failure
        self.history_workspace = "D:/workspace"
        self.workspace_root = self.history_workspace
        self.permission_grants = None
        self.output_record_path = "D:/logs/output.log"
        self.transcript_path_for_session = (
            lambda _sid: "D:/sessions/session.jsonl"
        )
        self.report = SimpleNamespace(output_record_path="D:/logs/output.log")
        self.transcripts = SimpleNamespace(
            path_for_session=lambda _sid: "D:/sessions/session.jsonl",
        )
        self.sessions = []
        self.lifecycle_calls = []
        self.conversation_calls = []
        self.hook_scopes = []
        self.tool_filter_mode = "app"
        self.event_report = _Report(self.events)
        self.event_reporting = EventReportRuntimeOwner(
            pool=_ReportPool(self.event_report),
        )

    async def begin_conversation_turn(
        self,
        *,
        cid=None,
        sid=None,
        title: str,
        source: str,
    ):
        assert cid is None
        assert sid is None
        self.events.append("conversation")
        self.conversation_calls.append((title, source))
        return ConversationTurn(
            cid="cid_tui",
            sid="sid_tui",
            turn_index=1,
            session_started=True,
            start_reason="initial",
            additional_context=("queued context",),
            system_message="queued system",
        )

    def hook_scope(self, context):
        if isinstance(context, TurnContext):
            context = HookExecutionContext.from_turn(context)
        scope = HookExecutionScope(
            context=context,
            dispatcher=HookRuntime.empty(),
        )
        self.hook_scopes.append(scope)
        return scope

    def tool_profile_for_turn(self):
        return self.tool_filter_mode

    async def with_mcp_session(self, pref_config, function):
        self.events.append("session")
        self.sessions.append(pref_config)
        return await function(
            "session",
            [{"name": "tool", "meta": {"domain": "coding"}}],
        )

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


@pytest.fixture
def tui_turn_operations(monkeypatch):
    """替换 TUI 用例直接依赖的流式和前台生命周期操作。"""
    stream_operation = object()

    async def run_foreground_turn(lifecycle, operation, *args, **kwargs):
        """记录前台生命周期调用并返回测试结果。"""
        controller = args[0]
        assert lifecycle is None
        controller.events.append("operation")
        controller.lifecycle_calls.append((operation, kwargs))
        if controller.failure is not None:
            raise controller.failure
        return RunResult(status="completed", assistant_text="done")

    monkeypatch.setattr(tui_turn, "stream_turn", stream_operation)
    monkeypatch.setattr(tui_turn, "run_foreground_turn", run_foreground_turn)
    return stream_operation


@pytest.mark.anyio
async def test_tui_turn_uses_shared_execution_for_attachment_only_prompt(
    tui_turn_operations,
) -> None:
    controller = _TuiController(attachments=[{
        "filename": "screen.png",
        "kind": "image",
    }])
    report = controller.event_report
    permissions = preset_permissions("auto")
    pref_config = {"primary": {"model": "test-model"}}
    prepared_attachments = []

    result = await run_tui_model_turn(
        controller,
        controller,
        controller,
        message_text="",
        pref_config=pref_config,
        permissions=permissions,
        turn_id="turn_tui",
        prompt_extras={"selection": {"x": 10, "y": 20}},
        on_prompt_prepared=prepared_attachments.extend,
    )

    assert result == RunResult(status="completed", assistant_text="done")
    assert controller.attach.consumed == 1
    assert prepared_attachments == [{
        "filename": "screen.png",
        "kind": "image",
    }]
    assert controller.lifecycle_calls[0][1][
        "turn_execution"
    ].context.transcript_path == "D:/sessions/session.jsonl"
    assert controller.conversation_calls == [("screen.png", "tui")]
    assert controller.sessions == [pref_config]
    assert controller.events == [
        "conversation",
        "report.open",
        "session",
        "operation",
    ]
    assert report.opened == 1
    assert report.closed == []

    runner, call = controller.lifecycle_calls[0]
    assert runner is tui_turn_operations
    assert call["turn_execution"].additional_context == ("queued context",)
    assert call["turn_execution"].system_message == "queued system"
    assert call["attachments"] == [{
        "filename": "screen.png",
        "kind": "image",
    }]
    assert call["extras"] == {"selection": {"x": 10, "y": 20}}
    assert call["ev_report"] is report
    execution = call["turn_execution"]
    assert execution.message == ""
    assert execution.metadata == {"cid": "cid_tui", "sid": "sid_tui"}
    assert execution.context.sid == "sid_tui"
    assert execution.context.turn_id == "turn_tui"
    assert execution.context.source == "tui"
    assert execution.context.session_started is True
    assert execution.context.permissions is permissions
    assert execution.hook_scope is controller.hook_scopes[0]
    assert dict(execution.input_payload) == {
        "content": "",
        "attachments": [{
            "filename": "screen.png",
            "kind": "image",
        }],
        "extras": {"selection": {"x": 10, "y": 20}},
    }
    assert len(controller.hook_scopes) == 1
    assert "message" not in call
    assert "metadata" not in call
    assert "permissions" not in call
    assert "turn_context" not in call
    assert "hook_scope" not in call


@pytest.mark.anyio
async def test_tui_turn_snapshots_helix_tool_mode_before_session_setup(
    tui_turn_operations,
) -> None:
    controller = _TuiController()

    async def with_mcp_session(_pref_config, function):
        controller.tool_filter_mode = "api"
        return await function("session", [
            {"name": "device_info", "meta": {"domain": "device"}},
            {
                "name": "nexus_http_request",
                "meta": {"domain": "bench", "class": "nexus"},
            },
        ])

    controller.with_mcp_session = with_mcp_session

    await run_tui_model_turn(
        controller,
        controller,
        controller,
        message_text="hello",
        pref_config={"primary": {"model": "test-model"}},
        permissions=preset_permissions("auto"),
    )

    tools = controller.lifecycle_calls[0][1]["tools"]
    assert [tool["name"] for tool in tools] == ["device_info"]


@pytest.mark.anyio
async def test_tui_turn_snapshots_unlinked_helix_state_before_session_setup(
    tui_turn_operations,
) -> None:
    controller = _TuiController()
    controller.tool_filter_mode = None

    async def with_mcp_session(_pref_config, function):
        controller.tool_filter_mode = "app"
        return await function("session", [
            {"name": "device_info", "meta": {"domain": "device"}},
            {
                "name": "nexus_http_request",
                "meta": {"domain": "bench", "class": "nexus"},
            },
        ])

    controller.with_mcp_session = with_mcp_session

    await run_tui_model_turn(
        controller,
        controller,
        controller,
        message_text="hello",
        pref_config={"primary": {"model": "test-model"}},
        permissions=preset_permissions("auto"),
    )

    tools = controller.lifecycle_calls[0][1]["tools"]
    assert [tool["name"] for tool in tools] == [
        "device_info",
        "nexus_http_request",
    ]


@pytest.mark.anyio
async def test_tui_turn_keeps_session_report_after_failure(
    tui_turn_operations,
) -> None:
    controller = _TuiController(failure=RuntimeError("stream failed"))
    report = controller.event_report

    with pytest.raises(RuntimeError, match="stream failed"):
        await run_tui_model_turn(
            controller,
            controller,
            controller,
            message_text="hello",
            pref_config={},
            permissions=preset_permissions("auto"),
        )

    assert report.opened == 1
    assert report.closed == []


@pytest.mark.anyio
async def test_tui_turn_closes_report_without_drain_after_cancellation(
    tui_turn_operations,
) -> None:
    controller = _TuiController(failure=asyncio.CancelledError())
    report = controller.event_report

    with pytest.raises(asyncio.CancelledError):
        await run_tui_model_turn(
            controller,
            controller,
            controller,
            message_text="hello",
            pref_config={},
            permissions=preset_permissions("auto"),
        )

    assert report.opened == 1
    assert report.closed == [False]
