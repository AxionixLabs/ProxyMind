# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.modes.result import RunResult
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.runtime.support.conversation import ConversationTurn
from mind_app.runtime.turns import executor as turn_executor
from mind_app.tui.session.turn import run_tui_model_turn
from mind_core.permissions import preset_permissions


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
        self.stream_looper = object()
        self.sessions = []
        self.lifecycle_calls = []
        self.conversation_calls = []
        self.hook_scopes = []

    def begin_conversation_turn(self, *, title: str, source: str):
        self.events.append("conversation")
        self.conversation_calls.append((title, source))
        return ConversationTurn(
            cid="cid_tui",
            sid="sid_tui",
            turn_index=1,
            session_started=True,
            start_reason="initial",
        )

    def hook_scope(self, context):
        scope = HookExecutionScope(
            context=context,
            dispatcher=HookRuntime.empty(),
        )
        self.hook_scopes.append(scope)
        return scope

    async def with_mcp_session(self, pref_config, function):
        self.events.append("session")
        self.sessions.append(pref_config)
        return await function("session", [{"name": "tool"}])

    async def run_mode_lifecycle(self, runner, **kwargs):
        self.events.append("operation")
        self.lifecycle_calls.append((runner, kwargs))
        if self.failure is not None:
            raise self.failure
        return RunResult(status="completed", assistant_text="done")

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


@pytest.mark.anyio
async def test_tui_turn_uses_shared_execution_for_attachment_only_prompt(
    monkeypatch,
) -> None:
    controller = _TuiController(attachments=[{
        "filename": "screen.png",
        "kind": "image",
    }])
    report = _Report(controller.events)
    monkeypatch.setattr(turn_executor, "EventReport", lambda *_args: report)
    permissions = preset_permissions("auto")
    pref_config = {"primary": {"model": "test-model"}}

    result = await run_tui_model_turn(
        controller,
        message_text="",
        run_mode="xtra",
        pref_config=pref_config,
        permissions=permissions,
    )

    assert result is None
    assert controller.attach.consumed == 1
    assert controller.conversation_calls == [("screen.png", "tui")]
    assert controller.sessions == [pref_config]
    assert controller.events == [
        "conversation",
        "report.open",
        "session",
        "operation",
        "report.close",
    ]
    assert report.opened == 1
    assert report.closed == [True]

    runner, call = controller.lifecycle_calls[0]
    assert runner is controller.stream_looper
    assert call["attachments"] == [{
        "filename": "screen.png",
        "kind": "image",
    }]
    assert call["ev_report"] is report
    execution = call["turn_execution"]
    assert execution.message == ""
    assert execution.metadata == {"cid": "cid_tui", "sid": "sid_tui"}
    assert execution.context.sid == "sid_tui"
    assert execution.context.source == "tui"
    assert execution.context.session_started is True
    assert execution.context.permissions is permissions
    assert execution.hook_scope is controller.hook_scopes[0]
    assert len(controller.hook_scopes) == 1
    assert "message" not in call
    assert "metadata" not in call
    assert "permissions" not in call
    assert "turn_context" not in call
    assert "hook_scope" not in call


@pytest.mark.anyio
async def test_tui_turn_closes_report_after_failure(monkeypatch) -> None:
    controller = _TuiController(failure=RuntimeError("stream failed"))
    report = _Report(controller.events)
    monkeypatch.setattr(turn_executor, "EventReport", lambda *_args: report)

    with pytest.raises(RuntimeError, match="stream failed"):
        await run_tui_model_turn(
            controller,
            message_text="hello",
            run_mode="xtra",
            pref_config={},
            permissions=preset_permissions("auto"),
        )

    assert report.opened == 1
    assert report.closed == [True]


@pytest.mark.anyio
async def test_tui_turn_closes_report_without_drain_after_cancellation(
    monkeypatch,
) -> None:
    controller = _TuiController(failure=asyncio.CancelledError())
    report = _Report(controller.events)
    monkeypatch.setattr(turn_executor, "EventReport", lambda *_args: report)

    with pytest.raises(asyncio.CancelledError):
        await run_tui_model_turn(
            controller,
            message_text="hello",
            run_mode="xtra",
            pref_config={},
            permissions=preset_permissions("auto"),
        )

    assert report.opened == 1
    assert report.closed == [False]
