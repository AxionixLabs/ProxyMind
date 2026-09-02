# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing
from dataclasses import dataclass

from agent.application.approvals.models import ApprovalOutcome
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalIdentity,
    ExecutionIdentity,
    McpApprovalAction,
    McpToolDescriptor,
    mcp_approval_risk,
    mcp_requires_approval,
)
from agent.ports.approval_core import ApprovalActionCoordinatorPort
from agent.ports.output import ToolInteractionActivityPort

__all__ = (
    "McpApprovalAuthorization",
    "authorize_mcp_tool_call",
    "build_mcp_approval_action",
    "mcp_approval_payload",
)


@dataclass(frozen=True, slots=True)
class McpApprovalAuthorization:
    """保存 MCP 审批门对一次调用的确定结果。"""

    allowed: bool
    reason: str
    action: McpApprovalAction | None = None
    outcome: ApprovalOutcome | None = None
    presentation: dict[str, typing.Any] | None = None


def _json_fingerprint(value: typing.Any, *, field_name: str) -> ActionFingerprint:
    """为已经校验的 JSON 值生成稳定 SHA-256 指纹。"""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be valid JSON") from error
    return ActionFingerprint(hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def build_mcp_approval_action(
    turn: TurnContext,
    *,
    call_id: str,
    descriptor: McpToolDescriptor,
    arguments: dict[str, typing.Any],
) -> McpApprovalAction:
    """从固定 Turn、调用身份和描述符构造类型化 MCP 动作。"""
    normalized_call_id = str(call_id or "").strip()
    if not normalized_call_id:
        raise ValueError("MCP tool_call_id is required")
    if not isinstance(descriptor, McpToolDescriptor):
        raise TypeError("MCP descriptor is required")
    if not isinstance(arguments, dict):
        raise TypeError("MCP arguments must be an object")

    arguments_fingerprint = _json_fingerprint(
        arguments,
        field_name="MCP arguments",
    )
    action_fingerprint = _json_fingerprint({
        "server": descriptor.server,
        "connector_id": descriptor.connector_id,
        "tool_name": descriptor.tool_name,
        "schema_fingerprint": descriptor.schema_fingerprint.value,
        "arguments_fingerprint": arguments_fingerprint.value,
    }, field_name="MCP action")
    approval_id = f"mcp-{action_fingerprint.value[:24]}"
    return McpApprovalAction(
        identity=ApprovalIdentity(
            session_id=turn.agent.root_session_id,
            run_id=turn.turn_id,
            approval_id=approval_id,
            action_id=normalized_call_id,
        ),
        execution=ExecutionIdentity(
            environment_id=turn.permissions.sandbox_mode,
            execution_id=turn.turn_id,
            tool_call_id=normalized_call_id,
        ),
        fingerprint=action_fingerprint,
        descriptor=descriptor,
        arguments_fingerprint=arguments_fingerprint,
    )


def mcp_approval_payload(
    action: McpApprovalAction,
    arguments: dict[str, typing.Any],
    *,
    agent: AgentContext | None = None,
) -> dict[str, typing.Any]:
    """把类型化 MCP 动作投影为当前审批展示队列的结构化载荷。"""
    descriptor = action.descriptor
    annotations = descriptor.annotations
    decisions = ["accept"]
    if descriptor.policy.allow_session_remember:
        decisions.append("acceptForSession")
    if descriptor.policy.allow_persistent_approval:
        decisions.append("acceptAndRemember")
    decisions.append("decline")
    payload: dict[str, typing.Any] = {
        "request_id": action.identity.approval_id,
        "approval_id": action.identity.approval_id,
        "session_id": action.identity.session_id,
        "run_id": action.identity.run_id,
        "action_id": action.identity.action_id,
        "execution_id": action.execution.execution_id,
        "environment_id": action.execution.environment_id,
        "call_id": action.execution.tool_call_id,
        "kind": "mcp_tool_call",
        "_local_mcp_approval": True,
        "tool": descriptor.exposed_name,
        "server": descriptor.server,
        "tool_name": descriptor.tool_name,
        "tool_title": descriptor.title,
        "tool_description": descriptor.description,
        "connector_id": descriptor.connector_id,
        "connector_name": descriptor.connector_name,
        "connector_description": descriptor.connector_description,
        "connected_account_email": descriptor.connected_account,
        "transport": descriptor.transport,
        "annotations": {
            "read_only_hint": annotations.read_only_hint,
            "destructive_hint": annotations.destructive_hint,
            "open_world_hint": annotations.open_world_hint,
        },
        "risk": mcp_approval_risk(annotations).value,
        "arguments": dict(arguments),
        "schema_fingerprint": descriptor.schema_fingerprint.value,
        "available_decisions": decisions,
    }
    if agent is not None and agent.depth > 0:
        payload["agent_id"] = agent.agent_id
        payload["agent_type"] = agent.agent_type
        payload["agent_depth"] = agent.depth
    return payload


async def authorize_mcp_tool_call(
    turn: TurnContext,
    *,
    coordinator: ApprovalActionCoordinatorPort | None,
    activity: ToolInteractionActivityPort,
    call_id: str,
    descriptor: McpToolDescriptor,
    arguments: dict[str, typing.Any],
) -> McpApprovalAuthorization:
    """执行 MCP 纯策略和必要的人机审批，任何缺失依赖都失败关闭。"""
    action = build_mcp_approval_action(
        turn,
        call_id=call_id,
        descriptor=descriptor,
        arguments=arguments,
    )
    if not mcp_requires_approval(descriptor):
        return McpApprovalAuthorization(
            True,
            "mcp policy approved",
            action=action,
        )
    if turn.permissions.approval_policy == "never":
        return McpApprovalAuthorization(
            False,
            "MCP tool requires approval but approval policy is never",
            action=action,
        )
    if coordinator is None:
        return McpApprovalAuthorization(
            False,
            "MCP approval coordinator is unavailable",
            action=action,
        )

    presentation = mcp_approval_payload(action, arguments, agent=turn.agent)
    await activity.approval_started(
        action.identity.approval_id,
        action.execution.tool_call_id,
    )
    try:
        outcome = await coordinator.request_action_outcome(action, presentation)
    finally:
        await activity.approval_completed(
            action.identity.approval_id,
            action.execution.tool_call_id,
        )
    allowed = outcome.decision in {
        "accept",
        "acceptForSession",
        "acceptAndRemember",
    }
    return McpApprovalAuthorization(
        allowed,
        (
            "MCP tool approval accepted"
            if allowed
            else "MCP tool approval declined"
        ),
        action=action,
        outcome=outcome,
        presentation=presentation,
    )


if __name__ == '__main__':
    pass
