# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from agent.application.approvals.models import ApprovalDecisionValue
from agent.application.approvals.amendments import approval_execpolicy_amendment
from agent.domain.permission_profiles import normalize_permission_profile
from agent.domain.execution_policy import (
    ExecutionPolicyRequirement,
    validate_sandbox_permission_arguments,
)
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext,
)
from agent.ports import (
    ExecutionPolicy,
    PatchPreviewPort,
)

LOCAL_EXEC_POLICY_TOOLS = frozenset({
    "shell_command",
    "exec_command",
    "write_stdin",
})


def local_exec_policy_requirement(
    manager: ExecutionPolicy,
    turn_context: TurnContext,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    call_id: str
) -> ExecutionPolicyRequirement | None:
    """返回 shell/exec 工具的本地执行要求。"""
    if tool not in LOCAL_EXEC_POLICY_TOOLS:
        return None

    command = arguments.get("command")

    if tool == "write_stdin":
        command = arguments.get("stdin")
    if not isinstance(command, str) or not command.strip():
        return None

    try:
        sandbox_permissions = validate_sandbox_permission_arguments(arguments)
    except ValueError as error:
        return ExecutionPolicyRequirement.forbidden(str(error))

    command_cwd = arguments.get("cwd") or turn_context.cwd
    additional_permissions = arguments.get("additional_permissions")
    if additional_permissions is not None:
        try:
            additional_permissions = normalize_permission_profile(
                additional_permissions,
                cwd=command_cwd,
            )
        except ValueError as error:
            return ExecutionPolicyRequirement.forbidden(str(error))

    try:
        requirement = manager.create_exec_approval_requirement_for_command(
            command,
            approval_policy=turn_context.permissions.approval_policy,
            sandbox_mode=turn_context.permissions.sandbox_mode,
            cwd=command_cwd,
            tool=tool,
            amendment_id=f"local-rule-{call_id}",
            sandbox_permissions=sandbox_permissions,
            environment_id=arguments.get("environment_id"),
            tty=arguments.get("tty"),
            additional_permissions=additional_permissions,
            policy_fingerprint=arguments.get("policy_fingerprint"),
            patch_scope=arguments.get("patch_scope"),
        )
    except ValueError as error:
        return ExecutionPolicyRequirement.forbidden(str(error))

    if (
        sandbox_permissions == "with_additional_permissions"
        and additional_permissions
        and not _has_permission_grant(
            turn_context,
            arguments,
            permissions=additional_permissions,
            cwd=command_cwd,
        )
    ):
        if requirement.state == "forbidden":
            return requirement
        if turn_context.permissions.approval_policy == "never":
            return ExecutionPolicyRequirement.forbidden(
                "additional permissions require approval, but approval policy is never"
            )
        return ExecutionPolicyRequirement.needs_approval(
            reason="additional permissions require approval",
            proposed_execpolicy_amendment=requirement.proposed_execpolicy_amendment,
        )

    return requirement


def normalize_local_permission_arguments(
    turn_context: TurnContext,
    arguments: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """规范化命令调用中的附加权限路径。"""
    normalized = dict(arguments)
    if normalized.get("additional_permissions") is not None:
        normalized["additional_permissions"] = normalize_permission_profile(
            normalized["additional_permissions"],
            cwd=normalized.get("cwd") or turn_context.cwd,
        )
    return normalized


def _has_permission_grant(
    turn_context: TurnContext,
    arguments: dict[str, typing.Any],
    *,
    permissions: typing.Any,
    cwd: str | None,
) -> bool:
    """判断当前 Turn 或会话授权是否覆盖附加权限。"""
    store = turn_context.permission_grants
    if store is None:
        return False
    return bool(store.has_grant(
        cid=turn_context.cid,
        sid=turn_context.sid,
        turn_id=turn_context.turn_id,
        environment_id=arguments.get("environment_id"),
        cwd=cwd,
        permissions=permissions,
    ))


def local_exec_policy_approval(
    *,
    invocation: ToolInvocation,
    requirement: ExecutionPolicyRequirement
) -> dict[str, typing.Any]:
    """构造客户端本地执行策略审批请求。"""
    command = str(
        invocation.arguments.get("command")
        or invocation.arguments.get("stdin")
        or ""
    )

    approval: dict[str, typing.Any] = {
        "id": f"local-exec-{invocation.call_id}",
        "request_id": f"local-exec-{invocation.call_id}",
        "call_id": invocation.call_id,
        "tool": invocation.name,
        "arguments": dict(invocation.arguments),
        "command": command,
        "cwd": str(invocation.arguments.get("cwd") or invocation.turn.cwd),
        "risk": (
            "dangerous_command"
            if requirement.state == "needs_approval"
            else "local_policy"
        ),
        "category": "exec",
        "reasons": ["local_exec_policy"],
        "environment": (
            "host"
            if str(invocation.arguments.get("sandbox_permissions") or "")
            .strip()
            .casefold() == "require_escalated"
            else "local"
        ),
    }
    for field_name in (
        "environment_id",
        "tty",
        "additional_permissions",
        "policy_fingerprint",
        "patch_scope",
    ):
        value = invocation.arguments.get(field_name)
        if value not in (None, "", (), [], {}):
            approval[field_name] = value

    reason = str(
        invocation.reason
        or invocation.arguments.get("justification")
        or ""
    ).strip()

    if (
        not reason
        and str(invocation.arguments.get("sandbox_permissions") or "")
        .strip()
        .casefold()
        == "require_escalated"
    ):
        reason = "Command requested host shell execution."
    if not reason:
        reason = str(requirement.reason or "").strip()
    if reason:
        approval["reason"] = reason
    amendment = requirement.proposed_execpolicy_amendment
    if amendment is not None:
        approval["proposed_execpolicy_amendment"] = {
            "id": amendment.id,
            "command_prefix": list(amendment.command_prefix),
            "display": amendment.display,
        }
    return approval


def local_patch_approval(
    patch_preview: PatchPreviewPort | None,
    invocation: ToolInvocation
) -> dict[str, typing.Any]:
    """构造补丁专用的本地审批请求。"""
    arguments = dict(invocation.arguments)
    patch     = str(arguments.get("patch") or "")

    preview: dict[str, typing.Any] | None = None

    expected_sha256 = arguments.get("expected_sha256")
    if not isinstance(expected_sha256, dict):
        expected_sha256 = None
    try:
        candidate = (
            patch_preview(
                patch=patch,
                expected_sha256=expected_sha256,
                force=bool(arguments.get("force", False)),
            )
            if patch_preview is not None
            else None
        )
    except (OSError, TypeError, ValueError, UnicodeError, KeyError):
        candidate = None
    if isinstance(candidate, dict) and candidate.get("ok"):
        preview = candidate.get("data")
        if not isinstance(preview, dict):
            preview = None

    scope: list[str] = []
    if preview is not None:
        files = preview.get("files")
        if isinstance(files, list):
            scope = [
                str(item.get("path") or "").strip()
                for item in files
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ]

    reason = str(
        invocation.reason or "apply_patch requests workspace changes"
    ).strip()

    approval_id = f"local-patch-{invocation.call_id}"

    approval: dict[str, typing.Any] = {
        "id": approval_id,
        "approval_id": approval_id,
        "request_id": approval_id,
        "call_id": invocation.call_id,
        "turn_id": invocation.turn.turn_id,
        "started_at_ms": int(time.time() * 1000),
        "tool": "apply_patch",
        "kind": "apply_patch",
        "arguments": arguments,
        "patch": patch,
        "cwd": str(arguments.get("cwd") or invocation.turn.cwd),
        "patch_scope": scope,
        "category": "apply_patch",
        "risk": "workspace_write",
        "available_decisions": [
            "accept",
            "acceptForSession",
            "decline",
        ],
        "reason": reason,
    }
    environment_id = str(arguments.get("environment_id") or "").strip()
    if environment_id:
        approval["environment_id"] = environment_id
    if preview is not None:
        approval["preview"] = preview
    return approval


def apply_local_patch_approval(
    manager: ExecutionPolicy,
    *,
    approval: dict[str, typing.Any],
    decision: ApprovalDecisionValue
) -> str | None:
    """保存补丁文件的本会话批准结果。"""
    if decision != "acceptForSession":
        return None

    try:
        manager.add_patch_approval_for_session(
            approval.get("patch_scope"),
            cwd=approval.get("cwd"),
            environment_id=approval.get("environment_id"),
        )
    except (OSError, UnicodeError, ValueError) as error:
        return str(error).strip() or type(error).__name__
    return None


def apply_local_exec_policy_approval(
    manager: ExecutionPolicy,
    *,
    invocation: ToolInvocation,
    approval: dict[str, typing.Any],
    decision: ApprovalDecisionValue
) -> str | None:
    """把本地审批的会话或持久化选择写入策略管理器。"""
    command = str(
        invocation.arguments.get("command")
        or invocation.arguments.get("stdin")
        or ""
    )

    cwd = invocation.arguments.get("cwd") or invocation.turn.cwd

    try:
        if decision == "acceptForSession":
            manager.add_approval_for_session(
                command,
                tool=invocation.name,
                cwd=cwd,
                sandbox_permissions=invocation.arguments.get(
                    "sandbox_permissions"
                ),
                environment_id=invocation.arguments.get("environment_id"),
                tty=invocation.arguments.get("tty"),
                additional_permissions=invocation.arguments.get(
                    "additional_permissions"
                ),
                policy_fingerprint=invocation.arguments.get(
                    "policy_fingerprint"
                ),
                patch_scope=invocation.arguments.get("patch_scope"),
            )
        elif decision == "acceptWithExecpolicyAmendment":
            amendment = approval_execpolicy_amendment(approval)
            if amendment is None:
                return "local execution policy amendment is invalid"
            manager.persist_execpolicy_amendment({
                "command_prefix": list(amendment.command_prefix),
            })
    except (OSError, UnicodeError, ValueError) as error:
        return str(error).strip() or type(error).__name__
    return None


def local_exec_policy_denied_result(
    requirement: ExecutionPolicyRequirement,
) -> dict[str, typing.Any]:
    """构造本地执行策略拒绝结果。"""
    return {
        "execution_denied": True,
        "error": "local execution policy forbids command",
        "decision": requirement.state,
        "reason": requirement.reason,
    }


def local_exec_policy_cancelled_result() -> dict[str, typing.Any]:
    """构造本地审批取消后的未执行结果。"""
    return {
        "executed": False,
        "status": "cancelled",
    }
