# -*- coding: utf-8 -*-

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.adapters.protocol.client import MindChatProtocolClient
from agent.adapters.protocol.items import CanonicalItemReducer
from agent.adapters.protocol.review_events import ReviewEventProjector
from agent.adapters.protocol.turn_source import SubmittingReviewTurnStreamSource
from agent.adapters.protocol.turn_stream import stream_turn
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.application.turns.execution import TurnExecution
from agent.application.turns.reviews import (
    create_review_command,
    review_wire_tools,
)
from agent.composition import open_effect_journal
from agent.domain.policies import PermissionSettings
from agent.harness.hooks.runtime import HookRuntime
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.tools.client_calls import (
    ClientToolCallOutcome,
    ClientToolCallResult,
)
from agent.ports import (
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSession,
    OutputSurfaceContext,
    RetryChanged,
    ToolStarted,
)
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    Viewport,
)
from agent.stores.approvals.ledger import ApprovalCallLedger
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCustomTarget,
    ReviewOutput,
)
from protocol.schema.stream_events import (
    MarkerEvent,
    ReviewCompletedEvent,
    ReviewStartedEvent,
    TextDeltaEvent,
    TextDoneEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
    TurnCompletedEvent,
    TurnRetryingEvent,
)

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_stream_01"
REVIEW_ITEM_ID = "review_item_stream"


class _ApplicationSink(ApplicationSink):
    """记录 Review 专用视图。"""

    def __init__(self) -> None:
        self.views: list[ApplicationView] = []

    @property
    def viewport(self) -> Viewport:
        return Viewport(width=100, height=30)

    def emit(self, view: ApplicationView) -> None:
        self.views.append(view)


class _AsyncSink:
    """记录输出会话中的异步投影。"""

    def __init__(self) -> None:
        self.items = []

    async def emit(self, item) -> None:
        self.items.append(item)


class _ActivitySink(_AsyncSink):
    """记录标准 Turn 活动事实。"""

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def emit_batch(self, events) -> None:
        self.items.extend(events)


class _OutputControl:
    """实现无终端副作用的输出控制。"""

    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def record_hidden_output(self, text: str) -> None:
        _ = text

    def record_tool_arguments(self, *_args, **_kwargs) -> None:
        return None


class _Transcript:
    """记录标准 Turn Transcript 事件。"""

    def __init__(self) -> None:
        self.entries = []
        self.closed = False

    def open(self) -> None:
        return None

    def append(self, event, *, actor=None, payload=None) -> None:
        self.entries.append((event, actor, dict(payload or {})))

    def close(self) -> None:
        self.closed = True


class _SessionContext:
    """提供共享流准备需要的根会话上下文。"""

    animate = True
    workspace_root = "."
    hook_startup_warnings = ()
    command_hook_sessions = None

    def capture_environment(self, **_kwargs):
        return None

    def skills_payload(self):
        return []


class _SessionState:
    """记录 Review 的最终 assistant 结果。"""

    def __init__(self) -> None:
        self.replies = []
        self.contexts = []

    def remember_assistant_reply(self, text: str) -> None:
        self.replies.append(text)

    def queue_turn_context(self, contexts) -> None:
        self.contexts.append(tuple(contexts))


class _Cleanup:
    """同步等待测试资源清理。"""

    @staticmethod
    async def await_cleanup(awaitable):
        return await awaitable


class _McpSession:
    """提供工具展示所需的最小 MCP 会话。"""

    def display_name_for_tool(self, name: str) -> str:
        return name

    async def list_tools(self):
        raise AssertionError("catalog is already frozen")

    def mcp_approval_descriptor(self, name, arguments):
        _ = name, arguments
        return None

    async def call_tool(self, name, arguments=None, **_kwargs):
        raise AssertionError(f"unexpected direct tool call: {name} {arguments}")


class _ReviewStream:
    """按 Canonical reducer 顺序交付 Review 和标准 Turn 事件。"""

    def __init__(self, events) -> None:
        self._events = tuple(events)
        self._reducer = CanonicalItemReducer(
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
        )
        self.current_item = None
        self.end_reason = "settled"
        self.last_event_seq = 0
        self.closed = False

    @property
    def canonical_items(self):
        return self._reducer.canonical_items

    @property
    def canonical_item_history(self):
        return self._reducer.item_history

    @property
    def pending_approval_items(self):
        return self._reducer.pending_approval_items

    @property
    def assistant_text(self) -> str:
        return self._reducer.assistant_text

    @property
    def sources(self):
        return self._reducer.sources

    def __aiter__(self) -> AsyncIterator:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator:
        for event in self._events:
            self.current_item = self._reducer.apply(event)
            self.last_event_seq = event.event_seq or self.last_event_seq
            yield event

    async def aclose(self) -> None:
        self.closed = True


class _ReviewCapability:
    """返回预置 Review 流并记录冻结请求。"""

    def __init__(self, stream: _ReviewStream) -> None:
        self.stream = stream
        self.request = None

    async def review(
        self,
        request,
        *,
        on_recovery_status=None,
        on_approval_snapshot=None,
    ):
        _ = on_recovery_status, on_approval_snapshot
        self.request = request
        return self.stream


def _catalog():
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
        for name in ("read_file", "read_repository")
    ]


def _events():
    target = ReviewCustomTarget("Focus on lifecycle correctness.")
    workspace = ClientReviewWorkspace.create()
    common = {
        "proto": "mind.chat",
        "cid": CID,
        "sid": SID,
        "turn_id": TURN_ID,
        "presentation_epoch": 1,
    }
    return (
        MarkerEvent(type="turn.started", event_seq=1, **common),
        ReviewStartedEvent(
            type="review.started",
            event_seq=2,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="in_progress",
            review_item_id=REVIEW_ITEM_ID,
            status="in_progress",
            target=target,
            workspace_revision=workspace.revision,
            prompt_version="mind-review/1",
            **common,
        ),
        MarkerEvent(type="turn.thinking", event_seq=3, **common),
        TurnRetryingEvent(
            type="turn.retrying",
            event_seq=4,
            round=1,
            attempt=2,
            max_attempts=3,
            retry_in_ms=20,
            reason="stream_reset",
            **common,
        ),
        ToolCallsStartEvent(
            type="tool.calls.start",
            event_seq=5,
            batch_id="batch_review",
            call_ids=("call_review",),
            count=1,
            ready=True,
            **common,
        ),
        ToolCallEvent(
            type="tool.call",
            event_seq=6,
            call_id="call_review",
            name="read_repository",
            arguments={"operation": "status"},
            **common,
        ),
        ToolCallsDoneEvent(
            type="tool.calls.done",
            event_seq=7,
            batch_id="batch_review",
            call_ids=("call_review",),
            count=1,
            **common,
        ),
        TextDeltaEvent(
            type="text.delta",
            event_seq=8,
            item_id="review_json",
            item_kind="text",
            item_status="in_progress",
            segment_id="review_json",
            text='{"findings":',
            **common,
        ),
        TextDoneEvent(
            type="text.done",
            event_seq=9,
            item_id="review_json",
            item_kind="text",
            item_status="completed",
            segment_id="review_json",
            final_text='{"findings": []}',
            **common,
        ),
        ReviewCompletedEvent(
            type="review.completed",
            event_seq=10,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="completed",
            review_item_id=REVIEW_ITEM_ID,
            status="completed",
            output=ReviewOutput(
                findings=(),
                overall_correctness="correct",
                overall_explanation="No findings.",
                overall_confidence_score=0.98,
            ),
            **common,
        ),
        TurnCompletedEvent(
            type="turn.completed",
            event_seq=11,
            status="completed",
            last_event_seq=11,
            completed_at=1.0,
            **common,
        ),
    )


@pytest.mark.anyio
async def test_review_reuses_standard_activity_and_tool_event_pump(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tools = review_wire_tools(_catalog())
    command = create_review_command(
        local_session_id="review_stream_session",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace=ClientReviewWorkspace.create(),
        pref_config={"primary": {"model": "test"}},
        environment_snapshot=None,
        tools=tools,
    )
    application = _ApplicationSink()
    session_state = _SessionState()
    transcript = _Transcript()
    activity = _ActivitySink()
    content = _AsyncSink()
    presentation = _AsyncSink()
    output_session = None

    def session_factory(
        _path: str,
        *,
        context: OutputSurfaceContext,
        animate: bool,
    ) -> OutputSession:
        nonlocal output_session
        _ = animate
        output_session = OutputSession(
            context=context,
            control=_OutputControl(),
            activity=activity,
            content=content,
            presentation=presentation,
        )
        return output_session

    async def execute_tool(_runner, invocation, **_kwargs):
        return ClientToolCallOutcome(result=ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=True,
            text=" M app.py",
            call_id=invocation.call_id,
            fields={
                "ok": True,
                "tool": invocation.name,
                "source": "client",
                "args": dict(invocation.arguments),
                "text": " M app.py",
                "attachments": [],
                "data": {},
            },
        ))

    monkeypatch.setattr(
        "agent.adapters.protocol.turn_stream.ClientToolCallRunner.execute",
        execute_tool,
    )
    stream = _ReviewStream(_events())
    capability = _ReviewCapability(stream)
    source = SubmittingReviewTurnStreamSource(capability, command.request)
    projector = ReviewEventProjector(
        application,
        hint="current changes",
        assistant_reply_sink=session_state.remember_assistant_reply,
    )
    interaction = type("Interaction", (), {
        "approval_source": "user",
        "begin_approval_session": AsyncMock(),
        "approval_snapshot_changed": lambda *_args: None,
        "present_approval": AsyncMock(return_value="decline"),
        "end_approval_session": AsyncMock(),
    })()
    cleanup = _Cleanup()
    context = TurnContext.create(
        agent=AgentContext.root(SID),
        cid=CID,
        sid=SID,
        source="review",
        pref_config={"primary": {"model": "test"}},
        cwd=str(tmp_path),
        permissions=PermissionSettings("read-only", "never"),
        approval_coordinator=ApprovalCoordinator(interaction),
        execution_policy=ExecPolicyManager(
            workspace_root=tmp_path,
            rules_paths=(),
            writable_rules_path=tmp_path / "rules",
        ),
        approval_ledger=ApprovalCallLedger(),
        transcript_factory=lambda *_args, **_kwargs: transcript,
        cleanup=cleanup,
        session_context=_SessionContext(),
        session_state=session_state,
        turn_id=TURN_ID,
    )
    execution = TurnExecution(
        context=context,
        message="current changes",
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=HookRuntime.empty(),
        ),
    )
    protocol_client = MindChatProtocolClient()
    protocol_client.post_tool_result = AsyncMock(return_value={})
    protocol_client.get_tool_result_status = AsyncMock(return_value={})
    protocol_client.post_effect_reconciliation = AsyncMock(return_value={})

    result = await stream_turn(
        _McpSession(),
        {"primary": {"model": "test"}},
        _catalog(),
        turn_execution=execution,
        turn_source=source,
        event_projection=projector,
        protocol_client=protocol_client,
        effect_journal_factory=lambda: open_effect_journal(
            tmp_path / "review-effects.db"
        ),
        tool_execution=McpToolExecutionAdapter(),
        session_factory=session_factory,
    )

    assert result.status == "completed"
    assert result.assistant_text == "No findings."
    assert [view.type for view in application.views] == [
        "review.started",
        "review.finished",
        "review.completed",
    ]
    assert any(
        isinstance(item, ModelWaitRequested)
        and item.reason in {"initial", "server_thinking"}
        for item in activity.items
    )
    assert any(isinstance(item, ToolStarted) for item in activity.items)
    assert [
        (item.source, item.state)
        for item in activity.items
        if isinstance(item, RetryChanged)
    ] == [
        ("provider", "started"),
        ("provider", "completed"),
    ]
    assert not any(isinstance(item, AssistantTextDelta) for item in content.items)
    assert not any(
        actor == "assistant" and "findings" in str(payload.get("content"))
        for _event, actor, payload in transcript.entries
    )
    protocol_client.post_tool_result.assert_awaited_once()
    assert session_state.replies == ["No findings."]
    assert stream.closed
