# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import sqlite3
from observability import observe_exception
from agent.application import (
    AgentMailboxWaitResult,
    AgentSnapshot,
    AgentSettings,
    AgentThreadContext,
    AgentTurnContext,
    RunResult,
    TurnExecution,
    AgentWaitResult,
)
from infrastructure.skills import skills_payload
from protocol.transport.events import EventReport
from agent.ports import (
    McpSessionPort,
    SubagentExecutionPort,
    SubagentOperation,
    TurnInputEventHandler,
)
from agent.adapters.subagent_execution import StreamSubagentExecution
from agent.harness.subagent_runner import SubagentRunner
from agent.application.execution import (
    AgentContext,
    TurnContext
)
from agent.domain.agents import (
    AgentSubmission,
)
from mind_app.runtime.turns.executor import (
    execute_turn,
    resolve_turn_hook_scope,
)
from agent.harness.agent_control import (
    AgentControl,
    AgentNotFoundError,
    AgentStateError,
)
from agent.stores.agent_mailbox import (
    format_mailbox_context
)
from agent.application import ForkTurns, normalize_fork_turns
from mind_app.runtime.subagents.context import build_fork_context
from agent.harness.agent_delivery import (
    AgentActiveTurn,
    AgentDeliveryRegistry,
    AgentMessageDispatch,
)
from agent.adapters.agent_messages import SteeringMessageDelivery
from agent.ports.agent_messages import AgentMessageDeliveryPort
from agent.stores.agent_graph import (
    AgentGraphCheckpoint,
    AgentGraphPersistence,
    AgentGraphStore
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind

SkillsProvider         = typing.Callable[[], list[dict[str, str]]]
TranscriptPathResolver = typing.Callable[[str], str]
SessionCleanup         = typing.Callable[[str], typing.Awaitable[typing.Any]]


class SubagentTurnFailedError(RuntimeError):
    """表示子模型轮次返回了未完成结果。"""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        detail = str(result.error or "").strip()
        reason = detail or f"subagent turn ended as {result.status}"
        super().__init__("\n\n".join((reason, *result.additional_context)))


class SubagentRuntime:
    """管理主控制器内按根会话隔离的子执行线程。"""

    def __init__(
        self,
        controller: "Mind",
        *,
        enabled: bool = True,
        settings: AgentSettings | None = None,
        executor: SubagentExecutionPort | None = None,
        message_delivery: AgentMessageDeliveryPort | None = None,
        graph_store: AgentGraphStore | None = None,
        skills_provider: SkillsProvider | None = None,
        transcript_path_for: TranscriptPathResolver | None = None,
        session_cleanup: SessionCleanup | None = None
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("subagent runtime enabled state must be a boolean")

        self._controller       = controller
        self._enabled          = enabled
        self._settings         = settings or AgentSettings()
        self._executor         = executor or StreamSubagentExecution(self._run_stream)
        self._message_delivery = message_delivery or SteeringMessageDelivery()
        self._graph_store      = graph_store

        self._graph_persistence = (
            AgentGraphPersistence(graph_store)
            if graph_store is not None
            else None
        )

        self._skills_provider     = skills_provider or self._configured_skills
        self._transcript_path_for = transcript_path_for or (lambda _sid: "")
        self._session_cleanup     = session_cleanup
        self._runner              = SubagentRunner(
            turn_runner=self._run_turn,
            cleanup=controller,
        )
        self._lock                = asyncio.Lock()
        self._controls: dict[str, AgentControl] = {}
        self._active_deliveries = AgentDeliveryRegistry()

        self._shutdown: bool = False

    async def _run_turn(
        self,
        pref_config: dict[str, typing.Any],
        execution: TurnExecution,
        operation: SubagentOperation[RunResult],
        *,
        event_report: EventReport | None = None,
    ) -> RunResult:
        """把 Harness Turn 端口绑定到当前 runtime Controller。"""
        return await execute_turn(
            self._controller,
            pref_config,
            execution,
            operation,
            event_report=event_report,
        )

    async def _run_stream(
        self,
        session: McpSessionPort,
        pref_config: dict[str, typing.Any],
        tools: list[dict[str, typing.Any]],
        *,
        turn_execution: TurnExecution,
        event_report: EventReport,
        skills: list[dict[str, str]],
        on_turn_input_event: TurnInputEventHandler | None = None,
    ) -> RunResult:
        """将当前 Controller 绑定到流式 Subagent adapter 端口。"""
        from mind_app.presentation.output.silent import create_silent_output_session
        from mind_app.runtime.turns.stream import stream_turn

        return await stream_turn(
            self._controller,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=turn_execution,
            ev_report=event_report,
            skills=skills,
            session_factory=create_silent_output_session,
            on_turn_input_event=on_turn_input_event,
        )

    @property
    def settings(self) -> AgentSettings:
        """返回运行时使用的固定配置。"""
        return self._settings

    @property
    def enabled(self) -> bool:
        """返回运行时是否允许创建和控制子执行主体。"""
        return self._enabled

    async def spawn(
        self,
        parent: TurnContext,
        message: str,
        pref_config: typing.Mapping[str, typing.Any],
        *,
        agent_type: str,
        task_name: str,
        fork_turns: ForkTurns | None = None,
        agent_id: str | None = None
    ) -> AgentSnapshot:
        """在父轮次所属根会话中创建子执行线程。"""
        task = _normalize_task(message)

        normalized_fork_turns = normalize_fork_turns(
            fork_turns,
            default_turns=self._settings.default_fork_turns,
        )

        control = await self._control(parent.agent.root_session_id)

        thread = AgentThreadContext.child(
            parent,
            agent_type,
            task_name,
            pref_config,
            skills=self._skills_provider(),
            agent_id=agent_id,
            fork_turns=normalized_fork_turns,
            fork_context=build_fork_context(
                parent.transcript_path,
                normalized_fork_turns,
                max_chars=self._settings.max_fork_context_chars,
            ),
            transcript_path_for=self._transcript_path_for,
        )

        return await control.spawn(
            thread,
            AgentSubmission.create(
                task,
                kind="initial",
                parent_turn_id=parent.turn_id,
            ),
        )

    async def followup_task(
        self,
        root_session_id: str,
        target: str,
        message: str,
        *,
        parent_turn_id: str = "",
        caller: AgentContext | None = None,
    ) -> str:
        """向根会话中的已有执行线程提交或排队下一轮任务。"""
        task    = _normalize_task(message)
        control = await self._existing_control(root_session_id)

        return await control.followup(
            target,
            AgentSubmission.create(
                task,
                kind="followup",
                parent_turn_id=parent_turn_id,
            ),
            caller=caller,
        )

    async def send_message(
        self,
        root_session_id: str,
        target: str,
        message: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentMessageDispatch:
        """优先向活动轮次投递消息，不可用时保留在邮箱。"""
        task      = _normalize_task(message)
        control   = await self._existing_control(root_session_id)
        recipient = await control.get(target, caller=caller)

        active = await self._active_deliveries.get(
            recipient.agent_id,
            root_session_id=root_session_id,
        )

        claim_owner = active.context.turn_id if active is not None else ""

        event = await control.send_message(
            target,
            task,
            caller=caller,
            claim_owner=claim_owner,
        )
        if active is not None:
            delivered = False
            try:
                receipt = await active.deliver(event)
                if receipt is not None:
                    await control.acknowledge_messages(
                        event.recipient_agent_id,
                        claim_owner,
                        (event,),
                    )
                    delivered = True
                    return AgentMessageDispatch(
                        event,
                        "active_turn",
                        receipt=receipt,
                    )
            finally:
                if not delivered:
                    await control.release_messages(
                        event.recipient_agent_id,
                        claim_owner,
                        (event,),
                    )
        return AgentMessageDispatch(event, "mailbox")

    async def resume(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """重新开放根会话中已经关闭的执行线程。"""
        control = await self._existing_control(root_session_id)
        return await control.resume(target, caller=caller)

    async def get(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """返回根会话中指定执行线程的快照。"""
        control = await self._existing_control(root_session_id)
        return await control.get(target, caller=caller)

    async def snapshots(
        self,
        root_session_id: str,
    ) -> tuple[AgentSnapshot, ...]:
        """返回根会话中的全部执行线程快照。"""
        control = await self._existing_control(root_session_id)
        return await control.snapshots()

    async def list_snapshots(
        self,
        root_session_id: str,
        *,
        caller: AgentContext | None = None,
        path_prefix: str | None = None,
    ) -> tuple[AgentSnapshot, ...]:
        """返回根会话中指定任务路径下的执行线程快照。"""
        control = await self._control(root_session_id)
        return await control.list_snapshots(
            caller=caller,
            path_prefix=path_prefix,
        )

    async def wait(
        self,
        root_session_id: str,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None,
        caller: AgentContext | None = None,
    ) -> AgentWaitResult:
        """等待根会话中的目标执行线程进入终态。"""
        control = await self._existing_control(root_session_id)
        return await control.wait(
            targets,
            timeout_sec=timeout_sec,
            caller=caller,
        )

    async def wait_updates(
        self,
        root_session_id: str,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None,
        caller: AgentContext | None = None,
    ) -> AgentMailboxWaitResult:
        """等待根会话中目标执行线程的动态更新。"""
        control = await self._existing_control(root_session_id)
        return await control.wait_updates(
            targets,
            timeout_sec=timeout_sec,
            caller=caller,
        )

    async def interrupt(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """中断根会话中指定执行线程的当前轮次。"""
        control = await self._existing_control(root_session_id)
        return await control.interrupt(target, caller=caller)

    async def close(
        self,
        root_session_id: str,
        target: str,
        *,
        caller: AgentContext | None = None
    ) -> AgentSnapshot:
        """关闭根会话中指定执行线程及其后代并返回关闭前快照。"""
        control         = await self._existing_control(root_session_id)
        target_snapshot = await control.get(target, caller=caller)
        previous        = await control.close(target, caller=caller)

        snapshots = await control.list_snapshots(
            caller=caller,
            path_prefix=target_snapshot.thread.agent.task_path,
        )

        if self._session_cleanup is not None:
            await asyncio.gather(
                *(self._session_cleanup(snapshot.thread.sid) for snapshot in snapshots),
                return_exceptions=True,
            )

        return previous

    async def shutdown_root(
        self,
        root_session_id: str
    ) -> tuple[AgentSnapshot, ...]:
        """关闭并移除指定根会话的执行树。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            control = self._controls.pop(normalized, None)
        await self._active_deliveries.close(normalized)
        if control is None:
            return ()

        snapshots = await control.shutdown()
        if self._graph_persistence is not None:
            await self._graph_persistence.flush()
        return snapshots

    async def shutdown(self) -> None:
        """终止运行时并关闭全部根会话执行树。"""
        async with self._lock:
            if self._shutdown:
                return None
            self._shutdown = True
            controls = self._controls
            self._controls = {}

        await self._active_deliveries.close()

        if controls:
            await asyncio.gather(
                *(control.shutdown() for control in controls.values()),
                return_exceptions=False,
            )
        if self._graph_persistence is not None:
            await self._graph_persistence.close()

    async def _execute_submission(
        self,
        turn: AgentTurnContext,
        submission: AgentSubmission,
    ) -> RunResult:
        """执行控制器已经分配的结构化任务。"""
        thread      = turn.thread
        pref_config = thread.config_snapshot()

        control = await self._existing_control(
            thread.agent.root_session_id
        )

        mailbox_events = await control.claim_messages(
            thread.agent.agent_id,
            submission.submission_id,
        )
        acknowledged = False

        try:
            mailbox_context = format_mailbox_context(mailbox_events)

            context = TurnContext.create(
                agent=thread.agent,
                cid=thread.cid,
                sid=thread.sid,
                source=thread.source,
                pref_config=pref_config,
                cwd=thread.cwd,
                permissions=thread.permissions,
                permission_grants=getattr(self._controller, "permission_grants", None),
                transcript_path=thread.transcript_path,
                parent_transcript_path=thread.parent_transcript_path,
                session_started=turn.turn_index == 1,
                session_start_reason="subagent",
            )

            execution = TurnExecution(
                context=context,
                message=submission.message,
                hook_scope=resolve_turn_hook_scope(self._controller, context),
                metadata={
                    "parent_turn_id": (
                        submission.parent_turn_id
                        or thread.spawn_turn_id
                    ),
                    "submission_id": turn.submission_id,
                    "submission_kind": submission.kind,
                    "mailbox_event_ids": [
                        event.event_id
                        for event in mailbox_events
                    ],
                    "turn_index": turn.turn_index,
                    "task_name": thread.agent.task_name,
                    "task_path": thread.agent.task_path,
                    "fork_turns": thread.fork_turns,
                    "fork_context": {
                        "available_turns": thread.fork_context.available_turns,
                        "selected_turns": thread.fork_context.selected_turns,
                        "included_turns": thread.fork_context.included_turns,
                        "chars": thread.fork_context.chars,
                        "truncated": thread.fork_context.truncated,
                    },
                },
                additional_context=(
                    *(
                        thread.fork_context.parts
                        if turn.turn_index == 1
                        else ()
                    ),
                    *((mailbox_context,) if mailbox_context else ()),
                ),
            )

            async def execute_subagent(
                prepared: TurnExecution,
                session: McpSessionPort,
                tools: list[dict[str, typing.Any]],
                event_report: EventReport
            ) -> RunResult:
                """通过运行时装配的执行端口运行固定子轮次。"""
                active = AgentActiveTurn(
                    prepared.context,
                    self._message_delivery,
                )
                await self._active_deliveries.register(active)
                try:
                    return await self._executor.execute(
                        pref_config=pref_config,
                        skills=thread.skills_snapshot(),
                        execution=prepared,
                        session=session,
                        tools=tools,
                        event_report=event_report,
                        on_turn_input_event=active.handle_event,
                    )
                finally:
                    active.close()
                    await self._active_deliveries.unregister(active)

            result = await self._runner.run(
                pref_config,
                execution,
                execute_subagent,
            )
            if result.status != "completed":
                raise SubagentTurnFailedError(result)
            if mailbox_events:
                await control.acknowledge_messages(
                    thread.agent.agent_id,
                    submission.submission_id,
                    mailbox_events,
                )
            acknowledged = True
            return result
        finally:
            if mailbox_events and not acknowledged:
                await control.release_messages(
                    thread.agent.agent_id,
                    submission.submission_id,
                    mailbox_events,
                )

    def _configured_skills(self) -> list[dict[str, str]]:
        """读取并固定创建线程时有效的技能描述。"""
        try:
            config = self._controller.config_session.load()
            return skills_payload(config)
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "subagent.skills.resolve_failed",
                error,
                level="WARNING",
            )
            return []

    async def _control(
        self,
        root_session_id: str,
        *,
        create_empty: bool = True,
    ) -> AgentControl:
        """返回或创建根会话对应的执行控制器。"""
        normalized = _normalize_root_session_id(root_session_id)

        async with self._lock:
            self._require_active()
            if not self._enabled:
                raise AgentStateError("subagent runtime is disabled")

            control = self._controls.get(normalized)
            if control is None:
                checkpoint = await self._load_checkpoint(normalized)
                if checkpoint is None and not create_empty:
                    raise AgentNotFoundError(
                        f"agent root session not found: {normalized}"
                    )
                control = self._create_control(normalized, checkpoint)
                self._controls[normalized] = control

            return control

    async def _existing_control(self, root_session_id: str) -> AgentControl:
        """返回已经建立的根会话执行控制器。"""
        return await self._control(root_session_id, create_empty=False)

    async def _load_checkpoint(
        self,
        root_session_id: str,
    ) -> AgentGraphCheckpoint | None:
        """从本地存储读取根会话执行树快照。"""
        if self._graph_store is None:
            return None
        try:
            return await asyncio.to_thread(
                self._graph_store.load,
                root_session_id,
            )
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            observe_exception(
                "subagent.graph.restore_failed",
                error,
                level="WARNING",
                root_session_id=root_session_id,
            )
            raise AgentStateError(
                f"agent graph restore failed: {root_session_id}"
            ) from error

    def _create_control(
        self,
        root_session_id: str,
        checkpoint: AgentGraphCheckpoint | None,
    ) -> AgentControl:
        """创建空控制树或从已校验快照重建。"""
        publisher = (
            self._graph_persistence.publish
            if self._graph_persistence is not None
            else None
        )
        if checkpoint is not None:
            return AgentControl.restore(
                checkpoint,
                self._execute_submission,
                max_open_agents=(
                    self._settings.max_concurrent_threads_per_session
                ),
                max_depth=self._settings.max_depth,
                checkpoint_publisher=publisher,
            )
        return AgentControl(
            AgentContext.root(root_session_id),
            self._execute_submission,
            max_open_agents=(
                self._settings.max_concurrent_threads_per_session
            ),
            max_depth=self._settings.max_depth,
            checkpoint_publisher=publisher,
        )

    def _require_active(self) -> None:
        """确认运行时仍可接受操作。"""
        if self._shutdown:
            raise AgentStateError("subagent runtime is shut down")


def _normalize_task(message: str) -> str:
    """返回非空的子轮次任务文本。"""
    if not isinstance(message, str):
        raise TypeError("subagent task must be a string")
    if not message.strip():
        raise ValueError("subagent task is required")

    return message


def _normalize_root_session_id(value: str) -> str:
    """返回非空的根会话标识。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("root session id is required")
    return normalized


if __name__ == '__main__':
    pass
