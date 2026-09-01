# -*- coding: utf-8 -*-

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.application.agents.views import (
    AgentMailboxWaitResult,
    AgentSnapshot,
    AgentWaitResult,
)
from agent.application.turns.run_result import RunResult
from agent.domain.agents import AgentSubmission
from agent.application.hooks.context import HookExecutionContext
from agent.stores.agents.graph import (
    AgentGraphCheckpoint,
    AgentGraphRecord,
)
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from frontends.output.silent import create_silent_output_session
from agent.application.turns.context import AgentContext, TurnContext
from agent.harness.hooks.scope import (
    HookExecutionScope,
)
from agent.harness.agents.control import (
    AgentControl,
    AgentStateError,
)
from agent.ports.agent_messages import AgentMessageReceipt
from agent.harness.agents.runtime import SubagentRuntime
from agent.harness.execution.turn_runner import execute_turn
from protocol.client.reports import EventReportRuntimeOwner
from agent.stores.agents.graph import AgentGraphStore
from agent.stores.agents.mailbox import (
    AgentMailboxStore,
    format_mailbox_context,
)
from agent.application.agents.thread import AgentThreadContext
from agent.application.config.settings import AgentSettings
from agent.domain.policies import preset_permissions
from protocol.schema.identifiers import new_cid, new_sid
from protocol.schema.stream_events import MarkerEvent
from protocol.schema.turn_inputs import TurnInput


class _Controller:
    def __init__(self) -> None:
        self.configs = []
        self.stream_calls = []
        self.stream_handler = None
        self.subagent_execution = _SubagentExecution(self)
        self.subagent_turn_runner = self._run_subagent_turn
        self.subagent_cleanup = self
        self.config_session = SimpleNamespace(load=lambda: {
            "skills": {"enabled": ["__test_none__"], "disabled": []},
        })
        self.event_reporting = EventReportRuntimeOwner(
            report_factory=lambda _cid, _sid: _EventReport(),
        )

    def hook_scope(self, context):
        return HookExecutionScope.empty(context)

    @property
    def hook_scope_provider(self):
        return self

    async def _run_subagent_turn(
        self,
        pref_config,
        execution,
        operation,
        *,
        event_report=None,
    ):
        return await execute_turn(
            self,
            pref_config,
            execution,
            operation,
            event_report=event_report,
        )

    async def with_mcp_session(self, pref_config, function):
        self.configs.append(pref_config)
        return await function(
            "session",
            [{"name": "tool", "meta": {"domain": "coding"}}],
        )

    async def run_stream_turn(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.stream_handler is not None:
            return await self.stream_handler(**kwargs)
        return RunResult(status="completed")

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable

    @staticmethod
    def tool_profile_for_turn():
        return None


class _SubagentExecution:
    def __init__(self, controller) -> None:
        self._controller = controller

    async def execute(
        self,
        pref_config,
        skills,
        execution,
        session,
        tools,
        event_report,
        on_turn_input_event=None,
    ):
        return await self._controller.run_stream_turn(
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=execution,
            event_report=event_report,
            skills=skills,
            session_factory=create_silent_output_session,
            on_turn_input_event=on_turn_input_event,
        )


class _EventReport:
    async def open(self) -> None:
        return None

    async def close(self, *, drain: bool = True) -> None:
        return None


class _Delivery:
    def __init__(self) -> None:
        self.calls = []

    async def deliver(
        self,
        context: TurnContext,
        turn_input: TurnInput,
    ) -> AgentMessageReceipt:
        self.calls.append((context, turn_input))
        return AgentMessageReceipt(
            status="accepted",
            turn_id=context.turn_id,
            client_message_id=turn_input.client_message_id,
        )


def _parent_turn(*, transcript_path: str = "") -> TurnContext:
    cid = new_cid()
    sid = new_sid(cid)
    return TurnContext.create(
        agent=AgentContext.root(sid),
        cid=cid,
        sid=sid,
        source="test",
        pref_config={"primary": {"model": "parent-model"}},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        transcript_path=transcript_path,
        turn_id="parent_turn",
    )


@pytest.mark.anyio
async def test_runtime_keeps_thread_context_across_submissions() -> None:
    controller = _Controller()
    parent = _parent_turn()
    pref_config = {
        "primary": {"model": "child-model"},
        "routing": {"tags": ["review"]},
    }
    skill_payload = [{"name": "review", "description": "Review changes"}]
    runtime = SubagentRuntime(
        controller,
        skills_provider=lambda: skill_payload,
    )
    spawned = await runtime.spawn(
        parent,
        "first task",
        pref_config,
        agent_type="review",
        task_name="review",
        agent_id="agent_review",
    )
    pref_config["primary"]["model"] = "mutated"
    pref_config["routing"]["tags"].append("unexpected")
    skill_payload[0]["description"] = "mutated"
    with pytest.raises(TypeError):
        spawned.thread.pref_config["primary"]["model"] = "forbidden"
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    await runtime.followup_task(
        parent.sid,
        spawned.agent_id,
        "second task",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    assert len(controller.stream_calls) == 2
    first_call, second_call = controller.stream_calls
    first = first_call["turn_execution"]
    second = second_call["turn_execution"]
    assert first_call["session"] == "session"
    assert first_call["tools"] == [
        {"name": "tool", "meta": {"domain": "coding"}},
    ]
    assert first_call["session_factory"] is create_silent_output_session
    assert first_call["skills"] == [{
        "name": "review",
        "description": "Review changes",
    }]
    assert second_call["skills"] == first_call["skills"]
    assert first.context.agent is second.context.agent
    assert first.context.cid == second.context.cid
    assert first.context.sid == second.context.sid
    assert first.context.turn_id != second.context.turn_id
    assert first.context.cwd == parent.cwd
    assert first.context.permissions == parent.permissions
    assert first.context.session_started
    assert not second.context.session_started
    assert first.metadata["parent_turn_id"] == parent.turn_id
    assert first.metadata["turn_index"] == 1
    assert second.metadata["turn_index"] == 2
    assert controller.configs == [
        {
            "primary": {"model": "child-model"},
            "routing": {"tags": ["review"]},
        },
        {
            "primary": {"model": "child-model"},
            "routing": {"tags": ["review"]},
        },
    ]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_assigns_stable_child_transcript_path(tmp_path) -> None:
    controller = _Controller()
    parent = _parent_turn(
        transcript_path=str(tmp_path / "root.log"),
    )
    store = ConversationTranscriptStore(tmp_path / "sessions")
    runtime = SubagentRuntime(
        controller,
        transcript_path_for=store.path_for_session,
        transcript_entries_for=(
            lambda path: ConversationTranscriptStore.reader(path).read()
        ),
    )

    spawned = await runtime.spawn(
        parent,
        "inspect",
        {},
        agent_type="review",
        task_name="inspect",
        agent_id="agent_review",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    first = controller.stream_calls[0]["turn_execution"]

    await runtime.followup_task(parent.sid, spawned.agent_id, "inspect again")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    second = controller.stream_calls[1]["turn_execution"]

    assert first.context.transcript_path == second.context.transcript_path
    assert first.context.transcript_path != parent.transcript_path
    assert first.context.parent_transcript_path == parent.transcript_path
    assert second.context.parent_transcript_path == parent.transcript_path
    child_path = Path(first.context.transcript_path)
    assert child_path.is_relative_to(tmp_path / "sessions")
    assert child_path.name == f"session-{first.context.sid}.jsonl"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_close_cleans_target_and_descendant_sessions() -> None:
    controller = _Controller()
    cleaned = []

    async def cleanup(session_id: str) -> None:
        cleaned.append(session_id)

    runtime = SubagentRuntime(
        controller,
        settings=AgentSettings(max_depth=2),
        session_cleanup=cleanup,
    )
    parent = _parent_turn()
    child = await runtime.spawn(
        parent,
        "inspect",
        {},
        agent_type="review",
        task_name="inspect",
        agent_id="agent_child",
    )
    await runtime.wait(parent.sid, [child.agent_id], timeout_sec=1)
    child_turn = controller.stream_calls[0]["turn_execution"].context
    descendant = await runtime.spawn(
        child_turn,
        "verify",
        {},
        agent_type="test",
        task_name="verify",
        agent_id="agent_descendant",
    )
    await runtime.wait(parent.sid, [descendant.agent_id], timeout_sec=1)

    await runtime.close(parent.sid, child.agent_id)

    assert set(cleaned) == {child.thread.sid, descendant.thread.sid}
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_flushes_graph_checkpoint_on_shutdown(tmp_path) -> None:
    controller = _Controller()
    store = AgentGraphStore(tmp_path / "agents.db")
    runtime = SubagentRuntime(controller, graph_store=store)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "inspect",
        {},
        agent_type="review",
        task_name="inspect",
        agent_id="agent_review",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    await runtime.shutdown()

    checkpoint = store.load(parent.sid)
    assert checkpoint is not None
    assert checkpoint.records[0].thread.agent.agent_id == spawned.agent_id
    assert checkpoint.records[0].status == "closed"
    assert checkpoint.records[0].status_before_close == "completed"


@pytest.mark.anyio
async def test_runtime_releases_root_and_restores_closed_graph(tmp_path) -> None:
    controller = _Controller()
    store = AgentGraphStore(tmp_path / "agents.db")
    runtime = SubagentRuntime(controller, graph_store=store)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "inspect",
        {},
        agent_type="review",
        task_name="inspect",
        agent_id="agent_review",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    released = await runtime.shutdown_root(parent.sid)

    assert released[0].status == "closed"
    persisted = store.load(parent.sid)
    assert persisted is not None
    assert persisted.records[0].status == "closed"
    restored = await runtime.get(parent.sid, spawned.agent_id)
    assert restored.status == "closed"
    assert restored.thread.agent.task_path == "/root/inspect"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_lazily_restores_graph_and_continues_queue(tmp_path) -> None:
    controller = _Controller()
    store = AgentGraphStore(tmp_path / "agents.db")
    parent = _parent_turn()
    thread = AgentThreadContext.child(
        parent,
        "worker",
        "worker",
        {},
        agent_id="agent_worker",
    )
    active = AgentSubmission.create(
        "interrupted task",
        kind="initial",
        parent_turn_id=parent.turn_id,
    )
    queued = AgentSubmission.create(
        "persisted followup",
        kind="followup",
        parent_turn_id=parent.turn_id,
    )
    store.save(AgentGraphCheckpoint(
        root_session_id=parent.sid,
        revision=7,
        updated_at_ms=time.time_ns() // 1_000_000,
        records=(AgentGraphRecord(
            thread=thread,
            status="running",
            submission=active,
            queue=(queued,),
            turn_count=1,
        ),),
    ))
    runtime = SubagentRuntime(controller, graph_store=store)

    restored = await runtime.get(parent.sid, "/root/worker")

    assert restored.status == "interrupted_by_restart"
    assert restored.queued_count == 1
    assert controller.stream_calls == []

    await runtime.followup_task(
        parent.sid,
        "worker",
        "new followup",
        caller=parent.agent,
    )
    waited = await runtime.wait(
        parent.sid,
        ["/root/worker"],
        timeout_sec=1,
    )

    assert waited.snapshots[0].status == "completed"
    assert [
        call["turn_execution"].message
        for call in controller.stream_calls
    ] == ["persisted followup", "new followup"]
    assert [
        call["turn_execution"].metadata["turn_index"]
        for call in controller.stream_calls
    ] == [2, 3]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_restores_unread_mailbox_message_once(tmp_path) -> None:
    controller = _Controller()
    store = AgentGraphStore(tmp_path / "agents.db")
    parent = _parent_turn()
    thread = AgentThreadContext.child(
        parent,
        "worker",
        "worker",
        {},
        agent_id="agent_worker",
    )
    submission = AgentSubmission.create(
        "initial task",
        kind="initial",
        parent_turn_id=parent.turn_id,
    )
    mailbox = AgentMailboxStore()
    message = mailbox.publish(
        "message",
        parent.agent,
        recipient=thread.agent,
        message="do not change the database layer",
    )
    store.save(AgentGraphCheckpoint(
        root_session_id=parent.sid,
        revision=3,
        updated_at_ms=time.time_ns() // 1_000_000,
        records=(AgentGraphRecord(
            thread=thread,
            status="completed",
            submission=submission,
            turn_count=1,
        ),),
        mailbox=mailbox.snapshot(),
    ))
    runtime = SubagentRuntime(controller, graph_store=store)

    await runtime.followup_task(
        parent.sid,
        "/root/worker",
        "continue",
        caller=parent.agent,
    )
    await runtime.wait(parent.sid, ["worker"], timeout_sec=1)

    execution = controller.stream_calls[0]["turn_execution"]
    assert execution.metadata["mailbox_event_ids"] == [message.event_id]
    assert "do not change the database layer" in execution.additional_context[0]

    await runtime.shutdown()

    persisted = store.load(parent.sid)
    assert persisted is not None
    restored_mailbox = AgentMailboxStore.from_snapshot(persisted.mailbox)
    assert restored_mailbox.take_messages(thread.agent.agent_id) == ()


@pytest.mark.anyio
async def test_runtime_injects_unread_mailbox_messages_into_followup() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_worker",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    dispatch = await runtime.send_message(
        parent.sid,
        "/root/worker",
        "do not change the database layer",
        caller=parent.agent,
    )
    await runtime.followup_task(
        parent.sid,
        spawned.agent_id,
        "continue",
        caller=parent.agent,
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    execution = controller.stream_calls[-1]["turn_execution"]
    assert dispatch.delivery == "mailbox"
    assert execution.metadata["mailbox_event_ids"] == [dispatch.event.event_id]
    assert len(execution.additional_context) == 1
    assert "do not change the database layer" in execution.additional_context[0]
    assert "/root" in execution.additional_context[0]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_releases_mailbox_claim_after_failed_followup() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_worker",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    dispatch = await runtime.send_message(
        parent.sid,
        spawned.agent_id,
        "preserve this constraint",
        caller=parent.agent,
    )

    async def fail(**_kwargs):
        return RunResult(status="failed", error="temporary failure")

    controller.stream_handler = fail
    await runtime.followup_task(parent.sid, spawned.agent_id, "try once")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    failed = controller.stream_calls[-1]["turn_execution"]

    controller.stream_handler = None
    await runtime.followup_task(parent.sid, spawned.agent_id, "try again")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    retried = controller.stream_calls[-1]["turn_execution"]

    await runtime.followup_task(parent.sid, spawned.agent_id, "after success")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    following = controller.stream_calls[-1]["turn_execution"]

    assert failed.metadata["mailbox_event_ids"] == [dispatch.event.event_id]
    assert retried.metadata["mailbox_event_ids"] == [dispatch.event.event_id]
    assert following.metadata["mailbox_event_ids"] == []
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_releases_mailbox_claim_after_context_failure(
    monkeypatch,
) -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_worker",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    dispatch = await runtime.send_message(
        parent.sid,
        spawned.agent_id,
        "preserve this constraint",
        caller=parent.agent,
    )

    format_calls = []

    def fail_once(events):
        format_calls.append(events)
        if len(format_calls) == 1:
            raise RuntimeError("context preparation failed")
        return format_mailbox_context(events)

    monkeypatch.setattr(
        "agent.harness.execution.subagent_submission.format_mailbox_context",
        fail_once,
    )
    await runtime.followup_task(parent.sid, spawned.agent_id, "try once")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    await runtime.followup_task(parent.sid, spawned.agent_id, "try again")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    retried = controller.stream_calls[-1]["turn_execution"]

    assert retried.metadata["mailbox_event_ids"] == [dispatch.event.event_id]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_steers_active_turn_without_reinjecting_message() -> None:
    controller = _Controller()
    delivery = _Delivery()
    runtime = SubagentRuntime(controller, message_delivery=delivery)
    parent = _parent_turn()
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream(**kwargs):
        execution = kwargs["turn_execution"]
        if execution.metadata["turn_index"] == 1:
            kwargs["on_turn_input_event"](MarkerEvent(
                type="turn.start",
                turn_id=execution.context.turn_id,
            ))
            started.set()
            await release.wait()
        return RunResult(status="completed")

    controller.stream_handler = stream
    spawned = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_worker",
    )
    await started.wait()

    dispatch = await runtime.send_message(
        parent.sid,
        spawned.agent_id,
        "new active constraint",
        caller=parent.agent,
    )
    release.set()
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    await runtime.followup_task(
        parent.sid,
        spawned.agent_id,
        "continue",
        caller=parent.agent,
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    followup = controller.stream_calls[-1]["turn_execution"]
    assert dispatch.delivery == "active_turn"
    assert dispatch.receipt == AgentMessageReceipt(
        status="accepted",
        turn_id=delivery.calls[0][0].turn_id,
        client_message_id=dispatch.event.event_id,
    )
    assert delivery.calls[0][0].agent.agent_id == spawned.agent_id
    assert delivery.calls[0][1].client_message_id == dispatch.event.event_id
    assert followup.metadata["mailbox_event_ids"] == []
    assert followup.additional_context == ()
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_forks_recent_parent_turns_into_first_child_turn(tmp_path) -> None:
    controller = _Controller()
    parent_path = tmp_path / "parent.jsonl"
    writer = ConversationTranscriptStore.writer(
        parent_path,
        session_id="sid_parent",
        turn_id="turn_parent",
    )
    writer.open()
    writer.append(
        "message.created",
        actor="user",
        payload={"content": "parent task"},
    )
    writer.append(
        "message.created",
        actor="assistant",
        payload={"content": "parent result"},
    )
    writer.close()

    runtime = SubagentRuntime(
        controller,
        settings=AgentSettings(default_fork_turns=1),
        transcript_path_for=ConversationTranscriptStore(
            tmp_path / "sessions"
        ).path_for_session,
        transcript_entries_for=(
            lambda path: ConversationTranscriptStore.reader(path).read()
        ),
    )
    parent = _parent_turn(transcript_path=str(parent_path))

    spawned = await runtime.spawn(
        parent,
        "child task",
        {},
        agent_type="review",
        task_name="review",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    execution = controller.stream_calls[-1]["turn_execution"]

    assert "parent task" in execution.additional_context[0]
    assert execution.metadata["task_path"] == "/root/review"
    assert execution.metadata["fork_turns"] == "1"
    assert execution.metadata["submission_kind"] == "initial"
    assert execution.metadata["fork_context"] == {
        "available_turns": 1,
        "selected_turns": 1,
        "included_turns": 1,
        "chars": len(execution.additional_context[0]),
        "truncated": False,
    }
    await runtime.shutdown()


@pytest.mark.anyio
async def test_silent_output_session_records_transcript(tmp_path) -> None:
    transcript = tmp_path / "agent.log"
    session = create_silent_output_session(str(transcript))

    await session.control.open()
    await session.control.record_hidden_output("child result")
    await session.control.stop()

    assert transcript.read_text(encoding="utf-8") == "child result\n"


@pytest.mark.anyio
async def test_runtime_isolates_controls_by_root_session() -> None:
    runtime = SubagentRuntime(_Controller())
    parents = (_parent_turn(), _parent_turn())

    snapshots = []
    for parent in parents:
        snapshots.append(await runtime.spawn(
            parent,
            "task",
            {},
            agent_type="worker",
            task_name="worker",
            agent_id="same_agent_id",
        ))

    for parent, snapshot in zip(parents, snapshots):
        result = await runtime.wait(
            parent.sid,
            [snapshot.agent_id],
            timeout_sec=1,
        )
        assert result.snapshots[0].status == "completed"
        assert len(await runtime.snapshots(parent.sid)) == 1

    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_shutdown_cancels_tasks_and_is_terminal() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    controller.stream_handler = operation

    await runtime.spawn(
        parent,
        "blocking task",
        {},
        agent_type="worker",
        task_name="blocking",
    )
    await started.wait()

    await runtime.shutdown()

    assert stopped.is_set()
    with pytest.raises(AgentStateError, match="shut down"):
        await runtime.spawn(
            parent,
            "late task",
            {},
            agent_type="worker",
            task_name="late",
        )


@pytest.mark.anyio
async def test_disabled_runtime_rejects_spawn() -> None:
    runtime = SubagentRuntime(
        _Controller(),
        enabled=False,
    )

    with pytest.raises(AgentStateError, match="disabled"):
        await runtime.spawn(
            _parent_turn(),
            "task",
            {},
            agent_type="worker",
            task_name="disabled",
        )


@pytest.mark.anyio
async def test_runtime_marks_failed_model_result_as_failed() -> None:
    controller = _Controller()

    async def fail(**_kwargs):
        return RunResult(
            status="failed",
            error="model request failed",
            additional_context=("project selection is required",),
        )

    controller.stream_handler = fail
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()

    spawned = await runtime.spawn(
        parent,
        "failing task",
        {},
        agent_type="worker",
        task_name="failing",
    )
    waited = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert waited.snapshots[0].status == "failed"
    assert "model request failed" in waited.snapshots[0].error
    assert "project selection is required" in waited.snapshots[0].error
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_reuses_thread_after_executor_exception() -> None:
    controller = _Controller()

    async def fail(**_kwargs):
        raise RuntimeError("stream crashed")

    controller.stream_handler = fail
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "failing task",
        {},
        agent_type="worker",
        task_name="failing",
    )
    failed = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert failed.snapshots[0].status == "failed"
    assert failed.snapshots[0].error == "RuntimeError: stream crashed"

    controller.stream_handler = None
    await runtime.followup_task(parent.sid, spawned.agent_id, "retry task")
    retried = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert retried.snapshots[0].status == "completed"
    assert retried.snapshots[0].result.status == "completed"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_coordinates_two_agents_through_interrupt_resume_and_exit() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = {
        "agent_first": asyncio.Event(),
        "agent_second": asyncio.Event(),
    }
    release_second = asyncio.Event()
    exit_turn_started = asyncio.Event()
    exit_turn_cancelled = asyncio.Event()

    async def execute(**kwargs):
        execution = kwargs["turn_execution"]
        agent_id = execution.context.agent.agent_id
        message = execution.message

        if message == "retry task":
            return RunResult(status="completed", assistant_text="retried")
        if message == "wait for exit":
            exit_turn_started.set()
            try:
                await asyncio.Future()
            finally:
                exit_turn_cancelled.set()

        started[agent_id].set()
        if agent_id == "agent_first":
            await asyncio.Future()
        await release_second.wait()
        return RunResult(status="completed", assistant_text="second done")

    controller.stream_handler = execute
    first = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="review",
        task_name="first",
        agent_id="agent_first",
    )
    second = await runtime.spawn(
        parent,
        "second task",
        {},
        agent_type="test",
        task_name="second",
        agent_id="agent_second",
    )
    await asyncio.gather(*(event.wait() for event in started.values()))

    interrupted = await runtime.interrupt(parent.sid, first.agent_id)
    assert interrupted.status == "interrupted"

    release_second.set()
    second_result = await runtime.wait(
        parent.sid,
        [second.agent_id],
        timeout_sec=1,
    )
    assert second_result.snapshots[0].status == "completed"
    assert second_result.snapshots[0].result.assistant_text == "second done"

    await runtime.close(parent.sid, first.agent_id)
    resumed = await runtime.resume(parent.sid, first.agent_id)
    assert resumed.status == "interrupted"
    await runtime.followup_task(parent.sid, first.agent_id, "retry task")
    retried = await runtime.wait(
        parent.sid,
        [first.agent_id],
        timeout_sec=1,
    )
    assert retried.snapshots[0].result.assistant_text == "retried"

    await runtime.followup_task(parent.sid, second.agent_id, "wait for exit")
    await exit_turn_started.wait()
    await runtime.shutdown()

    assert exit_turn_cancelled.is_set()
    with pytest.raises(AgentStateError, match="shut down"):
        await runtime.spawn(
            parent,
            "late task",
            {},
            agent_type="worker",
            task_name="late",
        )


@pytest.mark.anyio
async def test_cancelled_root_wait_preserves_running_agent() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(**_kwargs):
        started.set()
        await release.wait()
        return RunResult(status="completed", assistant_text="child done")

    controller.stream_handler = execute
    spawned = await runtime.spawn(
        parent,
        "long child task",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_worker",
    )
    await started.wait()

    await runtime.wait_updates(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=0,
        caller=parent.agent,
    )
    waiter = asyncio.create_task(runtime.wait_updates(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=30,
        caller=parent.agent,
    ))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    active = await runtime.get(parent.sid, spawned.agent_id)
    assert active.status == "running"

    release.set()
    completed = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )
    assert completed.snapshots[0].status == "completed"
    assert completed.snapshots[0].result.assistant_text == "child done"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_shutdown_bounds_slow_cancellation_cleanup(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        AgentControl,
        "SHUTDOWN_WAIT_TIMEOUT_SEC",
        0.01,
    )
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    cleanup_forced = asyncio.Event()

    async def execute(**_kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cleanup_started.set()
            try:
                await release_cleanup.wait()
                cleanup_finished.set()
            except asyncio.CancelledError:
                cleanup_forced.set()
                raise
            raise

    controller.stream_handler = execute
    await runtime.spawn(
        parent,
        "slow cleanup",
        {},
        agent_type="worker",
        task_name="worker",
        agent_id="agent_slow",
    )
    await started.wait()

    shutdown = asyncio.create_task(runtime.shutdown())
    await cleanup_started.wait()
    await asyncio.wait_for(shutdown, timeout=0.2)

    assert not cleanup_finished.is_set()
    assert cleanup_forced.is_set()
