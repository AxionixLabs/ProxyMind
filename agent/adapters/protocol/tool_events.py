# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.adapters.protocol.tool_results import ToolReplayAction
from agent.application.approvals.local_policy import (
    apply_local_exec_policy_approval,
    apply_local_patch_approval,
    local_exec_policy_approval,
    local_exec_policy_cancelled_result,
    local_exec_policy_denied_result,
    local_exec_policy_requirement,
    local_patch_approval,
    normalize_local_permission_arguments
)
from agent.application.approvals.models import ApprovalOutcome
from agent.application.tools.authorization import ToolTurnInterrupted
from agent.application.tools.catalog import meta_for_tool
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.tools.planning import PLAN_STEPS_TOOL
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext
)
from agent.application.views import uses_native_tool_view
from agent.application.views.builders.approval import build_approval_view
from agent.application.views.contracts import PresentationSink
from agent.application.views.tool_execution import show_tool_result
from agent.domain.execution_policy import ExecutionPolicyRequirement
from agent.domain.tool_policy import is_approval_only_tool
from agent.harness.hooks.tool_lifecycle import ToolCallCoordinator
from agent.harness.tools.client_calls import ClientToolCallRunner
from agent.harness.tools.plan_calls import PlanToolCallRunner
from agent.ports import (
    ApprovalCoordinatorPort,
    ExecutionPolicy,
    PatchPreviewPort,
)
from agent.ports.transcript import TranscriptSink
from agent.stores.approvals.ledger import ApprovalCallLedger
from observability import observe
from protocol.client.tools import build_tool_result_envelope
from protocol.client.turn_control import TurnControlRequestError
from protocol.schema.stream_events import (
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
    ToolEvent,
    ToolOutputEvent,
)
from protocol.schema.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS


def tool_invocation_from_event(
    turn_context: TurnContext,
    event: ToolEvent | ToolApprovalRequiredEvent,
    tools: list[dict[str, typing.Any]],
    *,
    arguments: dict[str, typing.Any] | None = None,
    name: str | None = None
) -> ToolInvocation:
    """从流式事件构建不暴露内部授权字段的工具调用上下文。"""
    event_name = str(name or getattr(event, "name", "")).strip()
    local_meta = meta_for_tool(tools, event_name)
    event_arguments = getattr(event, "arguments", {})

    return ToolInvocation(
        turn=turn_context,
        call_id=event.call_id,
        name=event_name,
        arguments=dict(event_arguments if arguments is None else arguments),
        meta=local_meta,
        reason=event.reason,
    )


def hook_denied_result(
    reason: str,
    *,
    tool: str,
    args: typing.Mapping[str, typing.Any],
) -> dict[str, typing.Any]:
    """构建前置 Hook 阻止工具时的标准结果。"""
    text = str(reason or "tool use denied by hook")
    return build_tool_result_envelope(
        tool=tool,
        ok=False,
        args=args,
        text=text,
        attachments=[],
        data={
            "hook_denied": True,
            "error": text,
        },
    )


ToolCallHandlingStatus = typing.Literal["handled", "interrupted"]


@dataclass(frozen=True, slots=True)
class ToolCallHandlingResult:
    """描述工具调用事件对外层轮次循环的影响。"""
    status: ToolCallHandlingStatus
    error: str | None = None

    @classmethod
    def handled(cls) -> "ToolCallHandlingResult":
        """返回已处理且可继续消费事件的结果。"""
        return cls(status="handled")

    @classmethod
    def interrupted(
        cls,
        error: str | None = None
    ) -> "ToolCallHandlingResult":
        """返回应中断当前事件循环的结果。"""
        return cls(status="interrupted", error=error)


class ToolCallBatchBuffer:
    """在执行前收集并校验一个已登记的客户端工具批次。"""

    def __init__(self) -> None:
        """初始化空批次缓冲区。"""
        self._start: ToolCallsStartEvent | None = None
        self._calls: dict[str, ToolCallEvent] = {}
        self._ignored_batch: ToolCallsStartEvent | None = None
        self._completed_batch_ids: set[str] = set()

    @property
    def active(self) -> bool:
        """返回当前是否处于等待批次调用事件的状态。"""
        return self._start is not None or self._ignored_batch is not None

    def begin(self, event: ToolCallsStartEvent) -> None:
        """登记批次声明并拒绝嵌套批次。"""
        if self._start is not None or self._ignored_batch is not None:
            raise ValueError("tool.calls.start arrived before previous batch completed")
        if event.batch_id in self._completed_batch_ids:
            self._ignored_batch = event
            return
        self._start = event
        self._calls = {}

    def accept(self, event: ToolCallEvent) -> tuple[ToolCallEvent, ...]:
        """接收批内调用，并拒绝缺少完整批次边界的调用。"""
        if self._ignored_batch is not None:
            return ()
        if self._start is None:
            raise ValueError("tool.call arrived without tool.calls.start")
        if event.call_id not in self._start.call_ids:
            raise ValueError("tool.call call_id is not declared by tool.calls.start")
        if event.call_id in self._calls:
            raise ValueError("duplicate tool.call in one batch")
        self._calls[event.call_id] = event
        return ()

    def complete(self, event: ToolCallsDoneEvent) -> tuple[ToolCallEvent, ...]:
        """校验批次结束声明并按声明顺序释放调用。"""
        ignored = self._ignored_batch
        if ignored is not None:
            if (
                event.batch_id != ignored.batch_id
                or event.call_ids != ignored.call_ids
                or event.count != ignored.count
            ):
                raise ValueError("tool.calls.done does not match tool.calls.start")
            self._ignored_batch = None
            return ()

        start = self._start
        if start is None:
            raise ValueError("tool.calls.done arrived without tool.calls.start")
        if (
            event.batch_id != start.batch_id
            or event.call_ids != start.call_ids
            or event.count != start.count
        ):
            raise ValueError("tool.calls.done does not match tool.calls.start")
        missing = [call_id for call_id in start.call_ids if call_id not in self._calls]
        if missing:
            raise ValueError("tool.calls.done arrived before every tool.call")
        calls = tuple(self._calls[call_id] for call_id in start.call_ids)
        self._completed_batch_ids.add(start.batch_id)
        self._start = None
        self._calls = {}
        return calls


class ToolEventHandler:
    """处理工具调用、审批策略和服务端工具输出事件。"""

    def __init__(
        self,
        *,
        turn_context: TurnContext,
        execution_policy: ExecutionPolicy,
        approval_coordinator: ApprovalCoordinatorPort,
        patch_preview: PatchPreviewPort | None = None,
        tools: list[dict[str, typing.Any]],
        ledger: ApprovalCallLedger,
        coordinator: ToolCallCoordinator,
        client_runner: ClientToolCallRunner,
        plan_runner: PlanToolCallRunner,
        tool_execution: ToolExecutionAdapter,
        activity: TurnActivityProjector,
        presentation: PresentationSink,
        transcript: TranscriptSink,
        post_result: typing.Callable[..., typing.Awaitable[None]],
        resolve_replayed_call: typing.Callable[
            ...,
            typing.Awaitable[ToolReplayAction],
        ],
        interrupt_turn: typing.Callable[[str], typing.Awaitable[bool]],
    ) -> None:
        """绑定当前轮次拥有的工具执行依赖。"""
        self.turn_context = turn_context
        self.execution_policy = execution_policy
        self.approval_coordinator = approval_coordinator
        self.patch_preview = patch_preview
        self.tools = tools
        self.ledger = ledger
        self.coordinator = coordinator
        self.client_runner = client_runner
        self.plan_runner = plan_runner
        self.tool_execution = tool_execution
        self.activity = activity
        self.presentation = presentation
        self.transcript = transcript
        self.post_result = post_result
        self.resolve_replayed_call = resolve_replayed_call
        self.interrupt_turn = interrupt_turn

    async def classify_replayed_call(
        self,
        event: ToolCallEvent,
    ) -> ToolReplayAction:
        """使用服务端持久状态裁决历史调用，不根据本地展示猜测。"""
        action = await self.resolve_replayed_call(
            cid=self.turn_context.cid,
            sid=self.turn_context.sid,
            call_id=event.call_id,
            tool_name=event.name,
        )
        observe(
            "tool.call.replay_resolved",
            call_id=event.call_id,
            turn_id=self.turn_context.turn_id,
            action=action,
        )
        return action

    async def complete_replayed_call(self, event: ToolCallEvent) -> None:
        """对已有权威结果的历史调用只收束本地活动投影。"""
        await self.activity.tool_completed(
            event.call_id,
            tool_activity_kind(event.name),
            name=event.name,
        )
        await self.activity.request_model_wait("tool_result")

    async def handle_call(
        self,
        event: ToolCallEvent
    ) -> ToolCallHandlingResult:
        """处理客户端工具调用并返回外层循环应采取的动作。"""
        result = await self._handle_call(event)
        tool_kind = tool_activity_kind(event.name)
        await self.activity.tool_completed(
            event.call_id,
            tool_kind,
            name=event.name,
        )
        if result.status == "handled":
            await self.activity.request_model_wait("tool_result")
        return result

    async def _handle_call(
        self,
        event: ToolCallEvent,
    ) -> ToolCallHandlingResult:
        """执行已取得活动 lease 的客户端工具调用。"""
        name = event.name
        arguments = dict(event.arguments)
        turn_context = self.turn_context

        approval_state = self.ledger.consume(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            call_id=event.call_id,
        )
        if approval_state == "consumed":
            observe(
                "tool.call.duplicate",
                call_id=event.call_id,
                turn_id=turn_context.turn_id,
            )
            return ToolCallHandlingResult.handled()

        if approval_state == "terminal":
            observe(
                "tool.call.terminal_replay",
                call_id=event.call_id,
                turn_id=turn_context.turn_id,
            )
            return ToolCallHandlingResult.handled()

        approval_consumed = approval_state == "approved"

        if not name:
            raise ValueError("tool.call name must be a non-empty string")

        invocation = tool_invocation_from_event(
            turn_context,
            event,
            self.tools,
            arguments=arguments,
        )
        hook_decision = await self.coordinator.prepare(invocation)

        if not hook_decision.allowed:
            self.coordinator.record_rejected(
                invocation,
                hook_decision.reason,
            )
            await self.post_result(
                invocation.turn.cid,
                invocation.turn.sid,
                invocation.call_id,
                invocation.name,
                False,
                hook_denied_result(
                    hook_decision.reason,
                    tool=invocation.name,
                    args=invocation.arguments,
                ),
                additional_context=hook_decision.additional_context,
            )
            return ToolCallHandlingResult.handled()

        invocation = self.coordinator.effective_invocation(
            invocation,
            hook_decision,
        )
        try:
            arguments = normalize_local_permission_arguments(
                turn_context,
                invocation.arguments,
            )
        except ValueError as error:
            await self._reject_and_wait(
                invocation,
                reason="additional permission profile is invalid",
                result={
                    "execution_denied": True,
                    "error": "additional permission profile is invalid",
                    "detail": str(error),
                },
            )
            return ToolCallHandlingResult.handled()
        invocation = invocation.with_arguments(arguments)

        if name == PLAN_STEPS_TOOL:
            tool_outcome = await self.client_runner.execute(
                invocation,
                use_coding_trace=False,
                operation_handler=self.plan_runner.execute_operation,
            )
            await self._post_tool_outcome(invocation, tool_outcome)
            return ToolCallHandlingResult.handled()

        local_requirement = local_exec_policy_requirement(
            self.execution_policy,
            turn_context,
            tool=name,
            arguments=arguments,
            call_id=event.call_id,
        )
        if local_requirement is not None and local_requirement.state == "forbidden":
            await self._reject_and_wait(
                invocation,
                reason="local execution policy forbids command",
                result=local_exec_policy_denied_result(local_requirement),
            )
            return ToolCallHandlingResult.handled()

        if (
            name == "apply_patch"
            and str(arguments.get("patch") or "").strip()
            and turn_context.permissions.approval_policy == "untrusted"
            and not approval_consumed
        ):
            patch_result = await self._handle_patch_approval(invocation)
            if patch_result is not None:
                return patch_result

        if (
            local_requirement is not None
            and local_requirement.state == "needs_approval"
            and not approval_consumed
        ):
            local_result = await self._handle_local_exec_approval(
                invocation,
                requirement=local_requirement,
            )
            if local_result is not None:
                return local_result

        try:
            tool_outcome = await self.client_runner.execute(
                invocation,
                use_coding_trace=uses_native_tool_view(name),
            )
        except (ToolTurnInterrupted, TurnControlRequestError) as error:
            return ToolCallHandlingResult.interrupted(str(error))

        await self._post_tool_outcome(invocation, tool_outcome)
        return ToolCallHandlingResult.handled()

    async def handle_output(self, event: ToolOutputEvent) -> None:
        """记录并展示服务端已执行工具的结果。"""
        name = event.name
        if not name:
            return

        arguments = dict(event.arguments)
        use_coding_trace = uses_native_tool_view(name)
        tool_run = self.tool_execution.project_server_output(event.payload)

        self.transcript.append(
            "tool.failed" if tool_run.status == "failed" else "tool.completed",
            actor="tool",
            payload={
                "call_id": event.call_id,
                "name": name,
                "arguments": arguments,
                "ok": tool_run.ok,
                "status": tool_run.status,
                "duration_ms": tool_run.cost_ms,
                "result": tool_run.fields,
            },
        )

        if (
            tool_run.status not in {"declined", "cancelled"}
            and not is_approval_only_tool(name)
        ):
            await show_tool_result(
                self.presentation,
                name,
                arguments,
                tool_run,
                use_coding_trace=use_coding_trace,
                call_id=event.call_id,
            )


    async def _handle_patch_approval(
        self,
        invocation: ToolInvocation
    ) -> ToolCallHandlingResult | None:
        """处理本地补丁专用审批，批准时允许继续执行。"""
        approval_coordinator = self.approval_coordinator

        patch_approval = local_patch_approval(
            self.patch_preview,
            invocation,
        )
        execution_policy = self.execution_policy

        patch_session_approved = execution_policy.patch_scope_approved_for_session(
            patch_approval.get("patch_scope"),
            cwd=patch_approval.get("cwd"),
            environment_id=patch_approval.get("environment_id"),
        )
        if patch_session_approved:
            patch_outcome = ApprovalOutcome.create(
                "acceptForSession",
                source="policy",
                reason="policy",
            )
        elif approval_coordinator is None:
            await self._reject_and_wait(
                invocation,
                reason="patch approval coordinator is unavailable",
                result={
                    "approval_denied": True,
                    "error": "patch approval coordinator is unavailable",
                },
            )
            return ToolCallHandlingResult.handled()
        else:
            patch_outcome = await self._request_local_approval(patch_approval)
            await self.presentation.emit(build_approval_view(
                patch_approval,
                decision=patch_outcome.decision,
                source=patch_outcome.source,
            ))

        if patch_outcome.decision not in TOOL_APPROVAL_ACCEPT_DECISIONS:
            patch_result = (
                local_exec_policy_cancelled_result()
                if patch_outcome.decision == "cancel"
                else {
                    "approval_denied": True,
                    "error": "patch approval declined",
                }
            )
            self.coordinator.record_rejected(
                invocation,
                "patch approval declined",
                result=patch_result,
            )
            await self._post_invocation_result(
                invocation,
                ok=False,
                result=patch_result,
                text=(
                    "user cancelled"
                    if patch_outcome.decision == "cancel"
                    else None
                ),
            )
            if patch_outcome.decision == "cancel":
                return await self._interrupt_after_approval(
                    invocation.call_id,
                    failure_message=(
                        "failed to interrupt turn after patch approval cancellation"
                    ),
                )
            return ToolCallHandlingResult.handled()

        update_error = apply_local_patch_approval(
            self.execution_policy,
            approval=patch_approval,
            decision=patch_outcome.decision,
        )
        if update_error:
            await self._reject_and_wait(
                invocation,
                reason="patch approval cache update failed",
                result={
                    "approval_denied": True,
                    "error": "patch approval cache update failed",
                    "detail": update_error,
                },
            )
            return ToolCallHandlingResult.handled()
        return None

    async def _handle_local_exec_approval(
        self,
        invocation: ToolInvocation,
        *,
        requirement: ExecutionPolicyRequirement
    ) -> ToolCallHandlingResult | None:
        """处理本地执行策略要求的审批，批准时允许继续执行。"""
        if self.turn_context.permissions.approval_policy == "never":
            await self._reject_and_wait(
                invocation,
                reason="local execution policy requires approval",
                result=local_exec_policy_denied_result(requirement),
            )
            return ToolCallHandlingResult.handled()

        local_approval = local_exec_policy_approval(
            invocation=invocation,
            requirement=requirement,
        )
        local_outcome = await self._request_local_approval(local_approval)
        await self.presentation.emit(build_approval_view(
            local_approval,
            decision=local_outcome.decision,
            source=local_outcome.source,
        ))

        if local_outcome.decision not in TOOL_APPROVAL_ACCEPT_DECISIONS:
            local_result = (
                local_exec_policy_cancelled_result()
                if local_outcome.decision == "cancel"
                else {
                    "approval_denied": True,
                    "error": "local execution policy approval declined",
                }
            )
            self.coordinator.record_rejected(
                invocation,
                "local execution policy approval declined",
                result=local_result,
            )
            await self._post_invocation_result(
                invocation,
                ok=False,
                result=local_result,
                text=(
                    "user cancelled"
                    if local_outcome.decision == "cancel"
                    else None
                ),
            )
            if local_outcome.decision == "cancel":
                return await self._interrupt_after_approval(
                    invocation.call_id,
                    failure_message=(
                        "failed to interrupt turn after approval cancellation"
                    ),
                )
            return ToolCallHandlingResult.handled()

        update_error = apply_local_exec_policy_approval(
            self.execution_policy,
            invocation=invocation,
            approval=local_approval,
            decision=local_outcome.decision,
        )
        if update_error:
            await self._reject_and_wait(
                invocation,
                reason="local execution policy update failed",
                result={
                    "approval_denied": True,
                    "error": "local execution policy update failed",
                    "detail": update_error,
                },
            )
            return ToolCallHandlingResult.handled()
        return None

    async def _request_local_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalOutcome:
        """在 typed 审批表面生命周期内请求本地决定。"""
        approval_id = str(
            approval.get("approval_id")
            or approval.get("id")
            or approval.get("request_id")
            or ""
        ).strip()
        call_id = str(approval.get("call_id") or "").strip()
        if not approval_id or not call_id:
            raise ValueError("local approval identity is required")
        await self.activity.approval_started(approval_id, call_id)
        try:
            return await self.approval_coordinator.request_outcome(approval)
        finally:
            await self.activity.approval_completed(approval_id, call_id)

    async def _post_tool_outcome(
        self,
        invocation: ToolInvocation,
        outcome: typing.Any
    ) -> None:
        """提交客户端工具执行和 Hook 处理后的最终结果。"""
        tool_result = outcome.result
        await self.post_result(
            invocation.turn.cid,
            invocation.turn.sid,
            invocation.call_id,
            tool_result.name,
            tool_result.ok,
            tool_result.fields,
            additional_context=outcome.additional_context,
        )

    async def _post_invocation_result(
        self,
        invocation: ToolInvocation,
        *,
        ok: bool,
        result: typing.Mapping[str, typing.Any],
        text: str | None = None,
    ) -> None:
        """提交未进入客户端执行器的确定工具结果。"""
        result_text = str(
            text
            if text is not None
            else result.get("error") or result.get("reason") or ""
        )
        envelope = build_tool_result_envelope(
            tool=invocation.name,
            ok=ok,
            args=invocation.arguments,
            text=result_text,
            attachments=[],
            data=result,
        )
        await self.post_result(
            invocation.turn.cid,
            invocation.turn.sid,
            invocation.call_id,
            invocation.name,
            ok,
            envelope,
        )

    async def _reject_and_wait(
        self,
        invocation: ToolInvocation,
        *,
        reason: str,
        result: typing.Any
    ) -> None:
        """记录拒绝、提交结果并恢复等待状态。"""
        self.coordinator.record_rejected(
            invocation,
            reason,
            result=result,
        )
        await self._post_invocation_result(
            invocation,
            ok=False,
            result=result,
        )

    async def _interrupt_after_approval(
        self,
        call_id: str,
        *,
        failure_message: str
    ) -> ToolCallHandlingResult:
        """中断取消本地审批所对应的逻辑轮次。"""
        if not await self.interrupt_turn(call_id):
            raise TurnControlRequestError(failure_message)
        return ToolCallHandlingResult.interrupted()


def tool_activity_kind(name: str) -> typing.Literal["client", "plan"]:
    """返回客户端工具使用的展示活动分类。"""
    return "plan" if name == PLAN_STEPS_TOOL else "client"


if __name__ == '__main__':
    pass
