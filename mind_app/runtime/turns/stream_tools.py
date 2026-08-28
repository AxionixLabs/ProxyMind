# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from engine.observability import observe
from mind_app.approval.ledger import ApprovalCallLedger
from mind_app.approval.models import ApprovalOutcome
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.history.contracts import TranscriptSink
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.native_coding.exec.exec_policy import ExecApprovalRequirement
from mind_app.output import OutputStatusPort
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.contracts import PresentationSink
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.runtime.tools.client_call import ClientToolCallRunner
from mind_app.runtime.tools.display import show_tool_result
from mind_app.runtime.tools.plan_call import PlanToolCallRunner
from mind_app.runtime.tools.run import server_tool_output_result
from mind_app.stream_events.tool_trace import coding_trace_tool
from mind_nova.requests.turn_control import TurnControlRequestError
from mind_nova.stream_events import (
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    ToolEvent,
    ToolOutputEvent
)
from mind_nova.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS
from .stream_policy import (
    apply_local_exec_policy_approval,
    local_permission_approval,
    apply_local_patch_approval,
    local_exec_policy_approval,
    local_exec_policy_cancelled_result,
    local_exec_policy_denied_result,
    local_exec_policy_requirement,
    local_patch_approval
)

if typing.TYPE_CHECKING:
    from mind_app.approval.coordinator import ApprovalCoordinator
    from mind_app.controller import Mind


def tool_invocation_from_event(
    turn_context: TurnContext,
    event: ToolEvent | ToolApprovalRequiredEvent,
    tools: list[dict[str, typing.Any]],
    *,
    arguments: dict[str, typing.Any] | None = None,
    name: str | None = None
) -> ToolInvocation:
    """从流式事件构建不暴露内部授权字段的工具调用上下文。"""
    event_name      = str(name or getattr(event, "name", "")).strip()
    local_meta      = meta_for_tool(tools, event_name)
    event_arguments = getattr(event, "arguments", {})

    return ToolInvocation(
        turn=turn_context,
        call_id=event.call_id,
        name=event_name,
        arguments=dict(event_arguments if arguments is None else arguments),
        meta=local_meta,
        reason=event.reason,
    )


def hook_denied_result(reason: str) -> dict[str, typing.Any]:
    """构建前置 Hook 阻止工具时的标准结果。"""
    text = str(reason or "tool use denied by hook")
    return {
        "ok": False,
        "text": text,
        "data": {
            "hook_denied": True,
            "error": text,
        },
    }


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


class ToolEventHandler:
    """处理工具调用、审批策略和服务端工具输出事件。"""

    def __init__(
        self,
        *,
        controller: "Mind",
        turn_context: TurnContext,
        tools: list[dict[str, typing.Any]],
        ledger: ApprovalCallLedger,
        coordinator: ToolCallCoordinator,
        client_runner: ClientToolCallRunner,
        plan_runner: PlanToolCallRunner,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        transcript: TranscriptSink,
        post_result: typing.Callable[..., typing.Awaitable[None]],
        interrupt_turn: typing.Callable[[str], typing.Awaitable[bool]],
    ) -> None:
        """绑定当前轮次拥有的工具执行依赖。"""
        self.controller     = controller
        self.turn_context   = turn_context
        self.tools          = tools
        self.ledger         = ledger
        self.coordinator    = coordinator
        self.client_runner  = client_runner
        self.plan_runner    = plan_runner
        self.status_control = status_control
        self.presentation   = presentation
        self.transcript     = transcript
        self.post_result    = post_result
        self.interrupt_turn = interrupt_turn

    async def handle_call(
        self,
        event: ToolCallEvent
    ) -> ToolCallHandlingResult:
        """处理客户端工具调用并返回外层循环应采取的动作。"""
        name         = event.name
        arguments    = dict(event.arguments)
        turn_context = self.turn_context

        approval_state = self.ledger.consume(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            call_id=event.call_id,
        )
        if approval_state == "consumed":
            result_record = self.ledger.result_for(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=event.call_id,
            )
            if result_record is not None and result_record.state == "pending":
                await self.post_result(
                    turn_context.cid,
                    turn_context.sid,
                    event.call_id,
                    result_record.name,
                    result_record.ok,
                    result_record.result,
                    additional_context=result_record.additional_context,
                    tool_arguments=result_record.arguments,
                )
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
            await self.post_result(
                turn_context.cid,
                turn_context.sid,
                event.call_id,
                "",
                False,
                {"error": "tool.call missing name/tool"},
            )
            await self.status_control.begin_reply_wait_status()
            return ToolCallHandlingResult.handled()

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
                hook_denied_result(hook_decision.reason),
                additional_context=hook_decision.additional_context,
                tool_arguments=invocation.arguments,
            )
            await self.status_control.begin_reply_wait_status(delay_sec=0.15)
            return ToolCallHandlingResult.handled()

        invocation = self.coordinator.effective_invocation(
            invocation,
            hook_decision,
        )
        arguments = dict(invocation.arguments)

        if (
            isinstance(arguments.get("additional_permissions"), dict)
            and arguments.get("additional_permissions")
            and str(arguments.get("sandbox_permissions") or "")
                .strip()
                .casefold() == "with_additional_permissions"
            and not approval_consumed
            and not self._has_permission_grant(arguments)
        ):
            permission_result = await self._handle_permission_approval(invocation)
            if permission_result is not None:
                return permission_result

        if name == PLAN_STEPS_TOOL:
            tool_outcome = await self.client_runner.execute(
                invocation,
                use_coding_trace=False,
                operation_handler=self.plan_runner.execute_operation,
            )
            await self._post_tool_outcome(invocation, tool_outcome)
            await self.status_control.begin_reply_wait_status(delay_sec=0.75)
            return ToolCallHandlingResult.handled()

        local_requirement = local_exec_policy_requirement(
            self.controller.exec_policy_manager,
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
                use_coding_trace=coding_trace_tool(name),
            )
        except TurnControlRequestError as error:
            return ToolCallHandlingResult.interrupted(str(error))

        await self._post_tool_outcome(invocation, tool_outcome)
        await self.status_control.begin_reply_wait_status(delay_sec=0.75)
        return ToolCallHandlingResult.handled()

    async def handle_output(self, event: ToolOutputEvent) -> None:
        """记录并展示服务端已执行工具的结果。"""
        name = event.name
        if not name:
            return

        arguments        = dict(event.arguments)
        use_coding_trace = coding_trace_tool(name)
        tool_run         = server_tool_output_result(event.payload)

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

        if use_coding_trace:
            await self.status_control.end_status()

        if tool_run.status not in {"declined", "cancelled"}:
            await show_tool_result(
                self.presentation,
                name,
                arguments,
                tool_run,
                use_coding_trace=use_coding_trace,
                call_id=event.call_id,
            )

        await self.status_control.begin_reply_wait_status(
            delay_sec=0.15,
            animate_after_sec=0.85,
        )

    async def _handle_patch_approval(
        self,
        invocation: ToolInvocation
    ) -> ToolCallHandlingResult | None:
        """处理本地补丁专用审批，批准时允许继续执行。"""
        approval_coordinator = getattr(
            self.controller,
            "approval_coordinator",
            None,
        )

        patch_approval = local_patch_approval(self.controller, invocation)

        patch_session_approved = (
            self.controller.exec_policy_manager.patch_scope_approved_for_session(
                patch_approval.get("patch_scope"),
                cwd=patch_approval.get("cwd"),
                environment_id=patch_approval.get("environment_id"),
            )
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
            active_coordinator = typing.cast(
                "ApprovalCoordinator",
                approval_coordinator,
            )
            patch_outcome = await active_coordinator.request_outcome(
                patch_approval
            )
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
            )
            if patch_outcome.decision == "cancel":
                return await self._interrupt_after_approval(
                    invocation.call_id,
                    failure_message=(
                        "failed to interrupt turn after patch approval cancellation"
                    ),
                )
            await self.status_control.begin_reply_wait_status()
            return ToolCallHandlingResult.handled()

        update_error = apply_local_patch_approval(
            self.controller.exec_policy_manager,
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
        requirement: ExecApprovalRequirement
    ) -> ToolCallHandlingResult | None:
        """处理本地执行策略要求的审批，批准时允许继续执行。"""
        reason = str(
            invocation.arguments.get("justification")
            or invocation.reason
            or ""
        ).strip()

        requested_permissions = str(
            invocation.arguments.get("sandbox_permissions") or ""
        ).strip().casefold()

        if (
            invocation.name in {"shell_command", "exec_command"}
            and not reason
            and requested_permissions != "require_escalated"
        ):
            await self._reject_and_wait(
                invocation,
                reason="shell approval reason is missing",
                result={
                    "execution_denied": True,
                    "error": "shell approval reason is missing",
                },
            )
            return ToolCallHandlingResult.handled()

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
        local_outcome = (
            await self.controller.approval_coordinator.request_outcome(
                local_approval
            )
        )
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
            )
            if local_outcome.decision == "cancel":
                return await self._interrupt_after_approval(
                    invocation.call_id,
                    failure_message=(
                        "failed to interrupt turn after approval cancellation"
                    ),
                )
            await self.status_control.begin_reply_wait_status()
            return ToolCallHandlingResult.handled()

        update_error = apply_local_exec_policy_approval(
            self.controller.exec_policy_manager,
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

    def _has_permission_grant(
        self,
        arguments: dict[str, typing.Any],
    ) -> bool:
        """判断工具调用是否已被当前 Turn 或 session 授权。"""
        store = getattr(self.turn_context, "permission_grants", None)
        if store is None:
            store = getattr(self.controller, "permission_grants", None)
        if store is None:
            return False
        return bool(store.has_grant(
            cid=self.turn_context.cid,
            sid=self.turn_context.sid,
            turn_id=self.turn_context.turn_id,
            environment_id=arguments.get("environment_id"),
            cwd=arguments.get("cwd") or self.turn_context.cwd,
            permissions=arguments.get("additional_permissions"),
        ))

    async def _handle_permission_approval(
        self,
        invocation: ToolInvocation,
    ) -> ToolCallHandlingResult | None:
        """处理工具附加权限审批，批准后继续当前调用。"""
        approval_coordinator = getattr(
            self.controller,
            "approval_coordinator",
            None,
        )
        if approval_coordinator is None:
            await self._reject_and_wait(
                invocation,
                reason="permission approval coordinator is unavailable",
                result={
                    "approval_denied": True,
                    "error": "permission approval coordinator is unavailable",
                },
            )
            return ToolCallHandlingResult.handled()

        try:
            approval = local_permission_approval(invocation)
        except ValueError as error:
            await self._reject_and_wait(
                invocation,
                reason="permission approval request is invalid",
                result={
                    "approval_denied": True,
                    "error": str(error),
                },
            )
            return ToolCallHandlingResult.handled()

        outcome = await approval_coordinator.request_outcome(approval)
        await self.presentation.emit(build_approval_view(
            approval,
            decision=outcome.decision,
            source=outcome.source,
        ))

        if outcome.decision not in {
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
        }:
            result = (
                local_exec_policy_cancelled_result()
                if outcome.decision == "cancel"
                else {
                    "approval_denied": True,
                    "error": "permission approval declined",
                }
            )
            self.coordinator.record_rejected(
                invocation,
                "permission approval declined",
                result=result,
            )
            await self._post_invocation_result(invocation, ok=False, result=result)
            if outcome.decision == "cancel":
                return await self._interrupt_after_approval(
                    invocation.call_id,
                    failure_message=(
                        "failed to interrupt turn after permission approval cancellation"
                    ),
                )
            await self.status_control.begin_reply_wait_status()
            return ToolCallHandlingResult.handled()

        store = getattr(self.turn_context, "permission_grants", None)
        if store is None:
            store = getattr(self.controller, "permission_grants", None)
        if store is None:
            await self._reject_and_wait(
                invocation,
                reason="permission grant store is unavailable",
                result={
                    "approval_denied": True,
                    "error": "permission grant store is unavailable",
                },
            )
            return ToolCallHandlingResult.handled()
        store.grant(
            scope=("session" if outcome.decision == "grantForSession" else "turn"),
            cid=invocation.turn.cid,
            sid=invocation.turn.sid,
            turn_id=invocation.turn.turn_id,
            environment_id=approval.get("environment_id"),
            cwd=approval.get("cwd") or invocation.turn.cwd,
            permissions=approval["permissions"],
            requested_permissions=invocation.arguments.get("additional_permissions"),
            strict_auto_review=(
                outcome.decision == "grantForTurnWithStrictAutoReview"
            ),
        )
        return None

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
            tool_arguments=invocation.arguments,
            additional_context=outcome.additional_context,
        )

    async def _post_invocation_result(
        self,
        invocation: ToolInvocation,
        *,
        ok: bool,
        result: typing.Any
    ) -> None:
        """提交未进入客户端执行器的确定工具结果。"""
        await self.post_result(
            invocation.turn.cid,
            invocation.turn.sid,
            invocation.call_id,
            invocation.name,
            ok,
            result,
            tool_arguments=invocation.arguments,
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
        await self.status_control.begin_reply_wait_status()

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


if __name__ == '__main__':
    pass
