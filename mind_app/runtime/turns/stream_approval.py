# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from engine.observability import (
    observe,
    observe_exception
)
from mind_app.approval.ledger import ApprovalCallLedger
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.policy import (
    approval_decisions,
    approval_execpolicy_amendment,
    approval_from_event,
    approval_from_snapshot,
    approval_id_from_event
)
from mind_app.output import OutputStatusPort
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.models import ApprovalSource
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_nova.stream_events import ToolApprovalRequiredEvent
from mind_nova.tool_approval import (
    TOOL_APPROVAL_ACCEPT_DECISIONS,
    ToolApprovalSnapshot
)

from .stream_policy import (
    LOCAL_EXEC_POLICY_TOOLS,
    apply_local_exec_policy_approval
)
from .stream_tools import tool_invocation_from_event

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


def approval_with_updated_input(
    approval: dict[str, typing.Any],
    tool: str,
    effective_arguments: dict[str, typing.Any] | None,
) -> dict[str, typing.Any]:
    """返回应用 Hook 参数改写后的审批数据。"""
    if effective_arguments is None:
        return approval

    updated = dict(approval)
    updated["tool"] = tool or updated.get("tool") or ""
    updated["arguments"] = dict(effective_arguments)

    if tool in {"shell_command", "exec_command", "apply_patch"}:
        command_field = "patch" if tool == "apply_patch" else "command"
        command = effective_arguments.get(command_field)
        if isinstance(command, str):
            updated["command"] = command
    return updated


def approval_report_kwargs(
    approval: dict[str, typing.Any],
    *,
    decision: ApprovalDecisionValue,
    source: ApprovalSource,
    turn_id: str,
    hook_reason: str = "",
    additional_context: typing.Sequence[str] = ()
) -> dict[str, typing.Any]:
    """构造审批决定回传所需的协议字段。"""
    approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS
    if approved:
        reason = None
    elif source == "hook":
        reason = hook_reason
    elif source == "policy":
        reason = "approval policy is never"
    elif decision == "cancel":
        reason = "user cancelled"
    else:
        reason = "user denied"

    fields: dict[str, typing.Any] = {
        "decision": decision,
        "reason": reason,
        "turn_id": turn_id,
    }
    if decision == "acceptWithExecpolicyAmendment":
        amendment = approval_execpolicy_amendment(approval)
        if amendment is None:
            raise RuntimeError("approval amendment decision is missing proposal")
        fields["execpolicy_amendment_id"] = amendment.id
    if not approved and additional_context:
        fields["additional_context"] = additional_context
    return fields


class ApprovalEventHandler:
    """处理单轮审批事件、重连恢复和批准结果持久化。"""

    def __init__(
        self,
        *,
        controller: "Mind",
        turn_context: TurnContext,
        tools: list[dict[str, typing.Any]],
        ledger: ApprovalCallLedger,
        coordinator: ToolCallCoordinator,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        post_approval: typing.Callable[..., typing.Awaitable[typing.Any]],
    ) -> None:
        """绑定当前轮次拥有的审批依赖。"""
        self.controller     = controller
        self.turn_context   = turn_context
        self.tools          = tools
        self.ledger         = ledger
        self.coordinator    = coordinator
        self.status_control = status_control
        self.presentation   = presentation
        self.post_approval  = post_approval

    async def restore_snapshot(self, snapshot: ToolApprovalSnapshot) -> None:
        """恢复重连前的未决审批，并提交用户随后作出的决定。"""
        turn_context = self.turn_context
        if (
            snapshot.cid != turn_context.cid
            or snapshot.sid != turn_context.sid
            or snapshot.turn_id != turn_context.turn_id
        ):
            raise ValueError("approval snapshot does not belong to current turn")

        for item in snapshot.approvals:
            if (
                item.status == "resolved"
                and item.decision in TOOL_APPROVAL_ACCEPT_DECISIONS
            ):
                self.ledger.record_approved(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )
                continue
            if item.status != "pending":
                continue

            restored_approval = approval_from_snapshot({
                "approval_id": item.approval_id,
                "turn_id": item.turn_id,
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
                "approval": item.approval,
            })
            self._add_agent_identity(restored_approval)

            restored_outcome = (
                await self.controller.approval_coordinator.request_outcome(
                    restored_approval
                )
            )
            restored_decision = restored_outcome.decision
            if (
                restored_decision != "cancel"
                and restored_decision not in approval_decisions(restored_approval)
            ):
                raise RuntimeError(
                    "restored approval decision is not available: "
                    f"{restored_decision}"
                )

            await self.post_approval(
                snapshot.cid,
                snapshot.sid,
                item.call_id,
                item.approval_id,
                **approval_report_kwargs(
                    restored_approval,
                    decision=restored_decision,
                    source=typing.cast(
                        ApprovalSource,
                        restored_outcome.source,
                    ),
                    turn_id=snapshot.turn_id,
                ),
            )
            if restored_decision in TOOL_APPROVAL_ACCEPT_DECISIONS:
                self.ledger.record_approved(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )

    async def handle(self, event: ToolApprovalRequiredEvent) -> None:
        """处理审批请求并把决定同步到服务端和本地账本。"""
        turn_context = self.turn_context
        approval     = approval_from_event(event)

        if self.ledger.is_approved(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            call_id=event.call_id,
        ):
            observe(
                "approval.replayed",
                tool=str(approval.get("tool") or ""),
                call_id=event.call_id,
                approval_id=approval_id_from_event(event),
            )
            return

        self._add_agent_identity(approval)
        await self.status_control.end_status(immediate=True)

        approval_started_at = time.perf_counter()

        approval_id      = approval_id_from_event(event)
        approval_tool    = str(approval.get("tool") or "").strip()
        approval_call_id = event.call_id

        permission_decision = None
        approval_invocation: ToolInvocation | None = None
        if approval_tool:
            approval_arguments = self._approval_arguments(
                approval,
                tool=approval_tool,
            )
            prepared_invocation = tool_invocation_from_event(
                turn_context,
                event,
                self.tools,
                arguments=approval_arguments,
                name=approval_tool,
            )
            approval_invocation = prepared_invocation
            permission_decision = await self.coordinator.prepare_permission(
                prepared_invocation
            )
            effective_arguments = None
            if permission_decision.updated_input is not None:
                effective_arguments = dict(
                    self.coordinator.effective_invocation(
                        prepared_invocation,
                        permission_decision,
                    ).arguments
                )
            approval = approval_with_updated_input(
                approval,
                approval_tool,
                effective_arguments,
            )

        observe(
            "approval.requested",
            tool=approval_tool,
            call_id=approval_call_id,
            approval_id=approval_id,
        )

        decision: ApprovalDecisionValue
        decision_source: ApprovalSource
        if permission_decision is not None and permission_decision.action == "deny":
            decision, decision_source = "decline", "hook"
        elif permission_decision is not None and permission_decision.action == "allow":
            decision, decision_source = "accept", "hook"
        elif turn_context.permissions.approval_policy == "never":
            decision, decision_source = "decline", "policy"
        else:
            outcome = await self.controller.approval_coordinator.request_outcome(
                approval
            )
            decision, decision_source = outcome.decision, outcome.source

        allowed_decisions = approval_decisions(approval)
        if decision != "cancel" and decision not in allowed_decisions:
            raise RuntimeError(
                f"approval decision is not available: {decision}"
            )

        observe(
            "approval.decided",
            tool=approval_tool,
            call_id=approval_call_id,
            approval_id=approval_id,
            decision=decision,
            decision_source=decision_source,
            hook_keys=(
                list(permission_decision.hook_keys)
                if permission_decision is not None
                else []
            ),
            elapsed_ms=int((time.perf_counter() - approval_started_at) * 1000),
        )

        await self.presentation.emit(build_approval_view(
            approval,
            decision=decision,
            source=decision_source,
        ))
        approval_turn_id = str(
            approval.get("turn_id") or turn_context.turn_id
        ).strip()

        try:
            await self.post_approval(
                turn_context.cid,
                turn_context.sid,
                approval_call_id,
                approval_id,
                **approval_report_kwargs(
                    approval,
                    decision=decision,
                    source=decision_source,
                    turn_id=approval_turn_id,
                    hook_reason=(
                        permission_decision.reason
                        if permission_decision is not None
                        else ""
                    ),
                    additional_context=(
                        permission_decision.additional_context
                        if permission_decision is not None
                        else ()
                    ),
                ),
            )
        except Exception as error:
            self.ledger.discard(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=approval_call_id,
            )
            observe_exception(
                "approval.report_failed",
                error,
                tool=approval_tool,
                call_id=approval_call_id,
                approval_id=approval_id,
                decision=decision,
            )
            raise

        approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS
        if approved:
            self._apply_local_policy_choice(
                approval_invocation,
                approval=approval,
                tool=approval_tool,
                call_id=approval_call_id,
                decision=decision,
            )
            self.ledger.record_approved(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=approval_call_id,
            )
        else:
            self.ledger.discard(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=approval_call_id,
            )
            await self.status_control.begin_reply_wait_status(
                delay_sec=0.15,
                animate_after_sec=0.85,
            )

    def _add_agent_identity(self, approval: dict[str, typing.Any]) -> None:
        """为子执行主体的审批附加当前身份。"""
        agent = self.turn_context.agent
        if agent.depth <= 0:
            return

        approval["agent_id"]    = agent.agent_id
        approval["agent_type"]  = agent.agent_type
        approval["agent_depth"] = agent.depth

    @staticmethod
    def _approval_arguments(
        approval: dict[str, typing.Any],
        *,
        tool: str,
    ) -> dict[str, typing.Any]:
        """恢复审批事件中工具 Hook 使用的参数。"""
        raw_arguments = approval.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        if arguments or tool not in LOCAL_EXEC_POLICY_TOOLS:
            return arguments

        command = approval.get("command")
        argument_name = "stdin" if tool == "write_stdin" else "command"
        if isinstance(command, str) and command.strip():
            arguments[argument_name] = command
        return arguments

    def _apply_local_policy_choice(
        self,
        invocation: ToolInvocation | None,
        *,
        approval: dict[str, typing.Any],
        tool: str,
        call_id: str,
        decision: ApprovalDecisionValue,
    ) -> None:
        """把审批事件中的会话或持久化选择写入本地策略。"""
        if (
            invocation is None
            or tool not in LOCAL_EXEC_POLICY_TOOLS
            or decision not in {
                "acceptForSession",
                "acceptWithExecpolicyAmendment",
            }
        ):
            return
        update_error = apply_local_exec_policy_approval(
            self.controller.exec_policy_manager,
            invocation=invocation,
            approval=approval,
            decision=decision,
        )
        if update_error:
            observe(
                "approval.local_policy_update_failed",
                level="WARNING",
                call_id=call_id,
                decision=decision,
                error=update_error,
            )


if __name__ == '__main__':
    pass
