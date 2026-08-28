# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.policy import (
    approval_execpolicy_amendment,
    approval_reason
)
from mind_app.native_coding.exec.exec_policy import (
    ExecApprovalRequirement,
    ExecPolicyManager,
    validate_sandbox_permission_arguments
)
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind

LOCAL_EXEC_POLICY_TOOLS = frozenset({
    "shell_command",
    "exec_command",
    "write_stdin",
})


def local_exec_policy_requirement(
    manager: ExecPolicyManager,
    turn_context: TurnContext,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    call_id: str
) -> ExecApprovalRequirement | None:
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
        return ExecApprovalRequirement.forbidden(str(error))

    try:
        return manager.create_exec_approval_requirement_for_command(
            command,
            approval_policy=turn_context.permissions.approval_policy,
            sandbox_mode=turn_context.permissions.sandbox_mode,
            cwd=arguments.get("cwd") or turn_context.cwd,
            tool=tool,
            amendment_id=f"local-rule-{call_id}",
            sandbox_permissions=sandbox_permissions,
            environment_id=arguments.get("environment_id"),
            tty=arguments.get("tty"),
            additional_permissions=arguments.get("additional_permissions"),
            policy_fingerprint=arguments.get("policy_fingerprint"),
            patch_scope=arguments.get("patch_scope"),
        )
    except ValueError as error:
        return ExecApprovalRequirement.forbidden(str(error))


def local_exec_policy_approval(
    *,
    invocation: ToolInvocation,
    requirement: ExecApprovalRequirement
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

    justification = str(
        invocation.arguments.get("justification") or invocation.reason or ""
    ).strip()

    if (
        not justification
        and str(invocation.arguments.get("sandbox_permissions") or "")
        .strip()
        .casefold()
        == "require_escalated"
    ):
        justification = "Command requested host shell execution."
    if justification:
        approval["justification"] = justification
    if requirement.reason:
        approval["reason"] = requirement.reason
    if invocation.reason:
        approval["approval_reason"] = invocation.reason
    selected_reason = approval_reason(approval)
    if selected_reason:
        approval["reason"] = selected_reason
        approval["justification"] = selected_reason
    amendment = requirement.proposed_execpolicy_amendment
    if amendment is not None:
        approval["proposed_execpolicy_amendment"] = {
            "id": amendment.id,
            "command_prefix": list(amendment.command_prefix),
            "display": amendment.display,
        }
    return approval


def local_permission_approval(
    invocation: ToolInvocation,
) -> dict[str, typing.Any]:
    """构造工具附加权限缺失时的本地权限审批请求。"""
    permissions = invocation.arguments.get("additional_permissions")
    if not isinstance(permissions, dict) or not permissions:
        raise ValueError("additional permissions must be a non-empty object")
    reason = str(
        invocation.arguments.get("justification")
        or invocation.reason
        or "additional permissions are required"
    ).strip()
    approval_id = f"local-permissions-{invocation.call_id}"
    return {
        "id": approval_id,
        "approval_id": approval_id,
        "request_id": approval_id,
        "call_id": invocation.call_id,
        "turn_id": invocation.turn.turn_id,
        "kind": "request_permissions",
        "tool": "request_permissions",
        "arguments": {"permissions": permissions},
        "permissions": typing.cast(dict[str, typing.Any], permissions),
        "environment_id": str(
            invocation.arguments.get("environment_id") or ""
        ).strip(),
        "cwd": str(invocation.arguments.get("cwd") or invocation.turn.cwd),
        "reason": reason,
        "justification": reason,
        "available_decisions": [
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
            "decline",
        ],
    }


def local_patch_approval(
    controller: "Mind",
    invocation: ToolInvocation
) -> dict[str, typing.Any]:
    """构造补丁专用的本地审批请求。"""
    arguments = dict(invocation.arguments)
    patch     = str(arguments.get("patch") or "")

    preview: dict[str, typing.Any] | None = None

    preview_patch = getattr(
        getattr(controller, "native_coding", None),
        "preview_patch",
        None,
    )

    if callable(preview_patch):
        build_preview = typing.cast(
            typing.Callable[..., typing.Any],
            preview_patch,
        )
        expected_sha256 = arguments.get("expected_sha256")
        if not isinstance(expected_sha256, dict):
            expected_sha256 = None
        try:
            candidate = build_preview(
                patch=patch,
                expected_sha256=expected_sha256,
                force=bool(arguments.get("force", False)),
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
        "justification": reason,
    }
    environment_id = str(arguments.get("environment_id") or "").strip()
    if environment_id:
        approval["environment_id"] = environment_id
    if preview is not None:
        approval["preview"] = preview
    return approval


def apply_local_patch_approval(
    manager: ExecPolicyManager,
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
    manager: ExecPolicyManager,
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


def local_exec_policy_denied_result(requirement: ExecApprovalRequirement) -> dict[str, typing.Any]:
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
        "ok": False,
        "text": "user cancelled",
        "data": {
            "executed": False,
            "status": "cancelled",
        },
    }


if __name__ == '__main__':
    pass
