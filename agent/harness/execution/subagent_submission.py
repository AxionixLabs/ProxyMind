# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable
)

from agent.application.agents.thread import AgentTurnContext
from agent.application.turns.context import TurnContext
from agent.application.turns.execution import TurnExecution
from agent.application.turns.run_result import RunResult
from agent.domain.agents import AgentSubmission
from agent.harness.agents.control import AgentControl
from agent.harness.agents.delivery import (
    AgentActiveTurn,
    AgentDeliveryRegistry,
)
from agent.ports import (
    EventReportPort,
    AgentMessageDeliveryPort,
    ApprovalCoordinatorPort,
    ApprovalLedger,
    ExecutionPolicy,
    HookExecutionScopePort,
    McpSessionPort,
    PermissionGrantReader,
    PatchPreviewPort,
    SubagentExecutionPort,
    TurnCleanupPort,
    TranscriptFactory,
)
from agent.stores.agents.mailbox import format_mailbox_context
from .subagent_runner import SubagentRunner

__all__ = (
    "SubagentSubmissionExecutor",
    "SubagentTurnFailedError",
)

ControlResolver = Callable[[str], Awaitable[AgentControl]]

HookScopeResolver = Callable[[TurnContext], HookExecutionScopePort]


class SubagentTurnFailedError(RuntimeError):
    """表示子模型轮次返回了未完成结果。"""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        detail = str(result.error or "").strip()
        reason = detail or f"subagent turn ended as {result.status}"
        super().__init__("\n\n".join((reason, *result.additional_context)))


class SubagentSubmissionExecutor:
    """协调已分配子 Agent 提交的 mailbox、执行和确认生命周期。"""

    def __init__(
        self,
        *,
        control_for: ControlResolver,
        runner: SubagentRunner,
        executor: SubagentExecutionPort,
        message_delivery: AgentMessageDeliveryPort,
        active_deliveries: AgentDeliveryRegistry,
        hook_scope_for: HookScopeResolver,
        permission_grants: PermissionGrantReader | None = None,
        execution_policy: ExecutionPolicy | None = None,
        approval_coordinator: ApprovalCoordinatorPort | None = None,
        approval_ledger: ApprovalLedger | None = None,
        transcript_factory: TranscriptFactory | None = None,
        cleanup: TurnCleanupPort | None = None,
        patch_preview: PatchPreviewPort | None = None,
    ) -> None:
        """绑定 Harness 所需端口，不依赖具体 Controller。"""
        self._control_for = control_for
        self._runner = runner
        self._executor = executor
        self._message_delivery = message_delivery
        self._active_deliveries = active_deliveries
        self._hook_scope_for = hook_scope_for
        self._permission_grants = permission_grants
        self._execution_policy = execution_policy
        self._approval_coordinator = approval_coordinator
        self._approval_ledger = approval_ledger
        self._transcript_factory = transcript_factory
        self._cleanup = cleanup
        self._patch_preview = patch_preview

    async def execute(
        self,
        turn: AgentTurnContext,
        submission: AgentSubmission,
    ) -> RunResult:
        """执行控制器已经分配的结构化任务。"""
        thread = turn.thread
        pref_config = thread.config_snapshot()
        control = await self._control_for(thread.agent.root_session_id)
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
                permission_grants=self._permission_grants,
                execution_policy=self._execution_policy,
                approval_coordinator=self._approval_coordinator,
                approval_ledger=self._approval_ledger,
                transcript_factory=self._transcript_factory,
                cleanup=self._cleanup,
                patch_preview=self._patch_preview,
                transcript_path=thread.transcript_path,
                parent_transcript_path=thread.parent_transcript_path,
                session_started=turn.turn_index == 1,
                session_mode="create" if turn.turn_index == 1 else "existing",
                session_start_reason="subagent",
            )

            execution = TurnExecution(
                context=context,
                message=submission.message,
                hook_scope=self._hook_scope_for(context),
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
                event_report: EventReportPort,
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


if __name__ == '__main__':
    pass
