# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.adapters.protocol.tool_events import tool_invocation_from_event
from agent.application.approvals.amendments import approval_execpolicy_amendment
from agent.application.approvals.local_policy import (
    LOCAL_EXEC_POLICY_TOOLS,
    apply_local_exec_policy_approval
)
from agent.application.approvals.models import (
    ApprovalDecisionValue,
    ApprovalOutcome,
    normalize_approval_decision,
)
from agent.application.approvals.policy import (
    approval_decisions,
    approval_from_event,
    approval_from_snapshot,
    approval_id_from_event
)
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext
)
from agent.application.views import ApprovalSource
from agent.application.views.builders.approval import build_approval_view
from agent.application.views.contracts import PresentationSink
from agent.harness.hooks.tool_lifecycle import ToolCallCoordinator
from agent.ports import (
    ApprovalCoordinatorPort,
    ExecutionPolicy,
    PermissionGrantPort,
)
from agent.ports import OutputStatusPort
from agent.stores.approvals.ledger import ApprovalCallLedger
from observability import (
    observe,
    observe_exception
)
from protocol.schema.stream_events import ToolApprovalRequiredEvent
from protocol.schema.tool_approval import (
    TOOL_APPROVAL_ACCEPT_DECISIONS,
    ToolApprovalSnapshot
)


def _approval_source(value: object) -> ApprovalSource:
    """校验审批协调器返回的展示来源。"""
    if value == "user":
        return "user"
    if value == "hook":
        return "hook"
    if value == "policy":
        return "policy"
    if value == "auto_review":
        return "auto_review"
    raise ValueError(f"unsupported approval source: {value}")


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
        "kind": str(approval.get("kind") or "command"),
        "approval": dict(approval),
    }
    if decision == "acceptWithExecpolicyAmendment":
        amendment = approval_execpolicy_amendment(approval)
        if amendment is None:
            raise RuntimeError("approval amendment decision is missing proposal")
        fields["execpolicy_amendment_id"] = amendment.id
    if (
        str(approval.get("kind") or "").strip() == "request_permissions"
        and approved
    ):
        permissions = approval.get("permissions")
        if not isinstance(permissions, dict):
            raise RuntimeError("permission approval is missing permissions")
        fields["permissions"] = dict(permissions)
        fields["scope"] = (
            "session" if decision == "grantForSession" else "turn"
        )
        fields["strict_auto_review"] = (
            decision == "grantForTurnWithStrictAutoReview"
        )
    if not approved and additional_context:
        fields["additional_context"] = additional_context
    return fields


class ApprovalEventHandler:
    """处理单轮审批事件、重连恢复和批准结果持久化。"""

    def __init__(
        self,
        *,
        turn_context: TurnContext,
        execution_policy: ExecutionPolicy,
        approval_coordinator: ApprovalCoordinatorPort,
        tools: list[dict[str, typing.Any]],
        ledger: ApprovalCallLedger,
        coordinator: ToolCallCoordinator,
        activity: TurnActivityProjector,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        post_approval: typing.Callable[..., typing.Awaitable[typing.Any]],
    ) -> None:
        """绑定当前轮次拥有的审批依赖。"""
        self.turn_context = turn_context
        self.execution_policy = execution_policy
        self.approval_coordinator = approval_coordinator
        self.tools = tools
        self.ledger = ledger
        self.coordinator = coordinator
        self.activity = activity
        self.status_control = status_control
        self.presentation = presentation
        self.post_approval = post_approval

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
                self._apply_permission_grant(
                    approval_from_snapshot(item.approval),
                    decision=normalize_approval_decision(item.decision),
                )
                self.ledger.record_approved(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )
                continue
            if item.status in {"resolved", "expired", "cancelled"}:
                self.ledger.record_terminal(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )
                continue
            if item.status != "pending":
                continue

            restored_approval = approval_from_snapshot(item.approval)
            self._add_agent_identity(restored_approval)

            restored_outcome = await self._request_approval(
                restored_approval,
                approval_id=item.approval_id,
                call_id=item.call_id,
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
                    source=_approval_source(restored_outcome.source),
                    turn_id=snapshot.turn_id,
                ),
            )
            if restored_decision in TOOL_APPROVAL_ACCEPT_DECISIONS:
                if restored_approval.get("kind") == "request_permissions":
                    self._apply_permission_grant(
                        restored_approval,
                        decision=restored_decision,
                    )
                self.ledger.record_approved(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )
            else:
                self.ledger.record_terminal(
                    cid=snapshot.cid,
                    sid=snapshot.sid,
                    turn_id=snapshot.turn_id,
                    call_id=item.call_id,
                )

    async def handle(self, event: ToolApprovalRequiredEvent) -> None:
        """处理审批请求并把决定同步到服务端和本地账本。"""
        turn_context = self.turn_context
        approval = approval_from_event(event)

        if self.ledger.is_terminal(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            call_id=event.call_id,
        ):
            observe(
                "approval.replayed_terminal",
                status=event.status,
                call_id=event.call_id,
                approval_id=approval_id_from_event(event),
            )
            return

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

        if event.status != "pending":
            if (
                event.status == "resolved"
                and isinstance(event.ack, dict)
                and str(event.ack.get("tool_status") or "") == "approved"
            ):
                if event.kind == "request_permissions":
                    ack_decision = str(event.ack.get("decision") or "")
                    if ack_decision in TOOL_APPROVAL_ACCEPT_DECISIONS:
                        self._apply_permission_grant(
                            approval,
                            decision=normalize_approval_decision(ack_decision),
                        )
                self.ledger.record_approved(
                    cid=turn_context.cid,
                    sid=turn_context.sid,
                    turn_id=turn_context.turn_id,
                    call_id=event.call_id,
                )
            else:
                self.ledger.record_terminal(
                    cid=turn_context.cid,
                    sid=turn_context.sid,
                    turn_id=turn_context.turn_id,
                    call_id=event.call_id,
                )
            observe(
                "approval.replayed_terminal",
                status=event.status,
                call_id=event.call_id,
                approval_id=approval_id_from_event(event),
            )
            return

        self._add_agent_identity(approval)
        await self.status_control.end_status(immediate=True)

        approval_started_at = time.perf_counter()

        approval_id = approval_id_from_event(event)
        approval_tool = str(approval.get("tool") or "").strip()
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
        elif (
            turn_context.permissions.approval_policy == "never"
            and approval.get("kind") != "request_permissions"
        ):
            decision, decision_source = "decline", "policy"
        else:
            outcome = await self._request_approval(
                approval,
                approval_id=approval_id,
                call_id=approval_call_id,
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
            if approval.get("kind") == "request_permissions":
                self._apply_permission_grant(approval, decision=decision)
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

    async def _request_approval(
        self,
        approval: dict[str, typing.Any],
        *,
        approval_id: str,
        call_id: str,
    ) -> ApprovalOutcome:
        """在 typed 审批表面生命周期内请求决定。"""
        await self.activity.approval_started(approval_id, call_id)
        try:
            return await self.approval_coordinator.request_outcome(approval)
        finally:
            await self.activity.approval_completed(approval_id, call_id)

    def _add_agent_identity(self, approval: dict[str, typing.Any]) -> None:
        """为子执行主体的审批附加当前身份。"""
        agent = self.turn_context.agent
        if agent.depth <= 0:
            return

        approval["agent_id"] = agent.agent_id
        approval["agent_type"] = agent.agent_type
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
        if tool in LOCAL_EXEC_POLICY_TOOLS:
            command = arguments.get("command")
            if isinstance(command, list):
                arguments["command"] = (
                    command[0]
                    if len(command) == 1
                    else " ".join(str(item) for item in command)
                )
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
            self.execution_policy,
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

    def _apply_permission_grant(
        self,
        approval: dict[str, typing.Any],
        *,
        decision: ApprovalDecisionValue,
    ) -> None:
        """把权限审批结果写入当前 Turn 或会话授权存储。"""
        if decision not in {
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
        }:
            return None
        store = self.turn_context.permission_grants
        if not isinstance(store, PermissionGrantPort):
            observe(
                "approval.permission_grant_unavailable",
                level="WARNING",
                call_id=str(approval.get("call_id") or ""),
            )
            return None
        permissions = approval.get("permissions")
        if not isinstance(permissions, dict):
            observe(
                "approval.permission_grant_invalid",
                level="WARNING",
                call_id=str(approval.get("call_id") or ""),
            )
            return None
        try:
            store.grant(
                scope=("session" if decision == "grantForSession" else "turn"),
                cid=self.turn_context.cid,
                sid=self.turn_context.sid,
                turn_id=str(approval.get("turn_id") or self.turn_context.turn_id),
                environment_id=approval.get("environment_id"),
                cwd=approval.get("cwd") or self.turn_context.cwd,
                permissions=permissions,
                requested_permissions=permissions,
                strict_auto_review=(
                    decision == "grantForTurnWithStrictAutoReview"
                ),
            )
        except (TypeError, ValueError, OSError) as error:
            observe_exception(
                "approval.permission_grant_failed",
                error,
                level="WARNING",
                call_id=str(approval.get("call_id") or ""),
                decision=decision,
            )


if __name__ == '__main__':
    pass
