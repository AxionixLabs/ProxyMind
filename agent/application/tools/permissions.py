# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing

from agent.application.tools.authorization import (
    ExecutionAuthorizationError,
    ToolTurnInterrupted,
    reject_model_execution,
)
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import BuiltinTool
from agent.application.tools.results import (
    LocalToolResult,
    LocalToolSource,
)
from agent.domain.permission_profiles import (
    PermissionGrantScope,
    PermissionProfile,
    normalize_permission_profile,
)
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.permissions import PermissionGrantPort

__all__ = (
    "PERMISSION_PROFILE_SCHEMA",
    "REQUEST_PERMISSIONS_INPUT_SCHEMA",
    "permission_response_result",
    "permission_tools",
)


PERMISSION_PROFILE_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "network": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "是否请求网络访问。",
                },
            },
            "additionalProperties": False,
        },
        "file_system": {
            "type": "object",
            "properties": {
                "read": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "请求读取的路径列表。",
                },
                "write": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "请求写入的路径列表。",
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
    "description": "要申请的文件系统或网络权限。",
}

REQUEST_PERMISSIONS_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "environment_id": {
            "type": "string",
            "description": "目标执行环境标识；省略时使用当前环境。",
        },
        "reason": {
            "type": "string",
            "description": "向用户展示的权限申请理由。",
        },
        "permissions": PERMISSION_PROFILE_SCHEMA,
    },
    "required": ["permissions"],
    "additionalProperties": False,
}


def permission_response_result(
    *,
    permissions: PermissionProfile,
    scope: PermissionGrantScope,
    strict_auto_review: bool,
    error: str = "",
) -> LocalToolResult:
    """构造权限内置工具的结构化回执。"""
    response: dict[str, typing.Any] = {
        "permissions": dict(permissions),
        "scope": scope,
        "strict_auto_review": bool(strict_auto_review),
    }
    if error:
        response["error"] = error
    text = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    return LocalToolResult(
        tool="request_permissions",
        source=LocalToolSource.BUILTIN,
        ok=not bool(error),
        text=text,
        data=response,
    )


def permission_tools(
    approval_coordinator: ApprovalCoordinatorPort | None,
    permission_grants: PermissionGrantPort,
) -> list[BuiltinTool]:
    """构造通过显式审批与授权端口执行的权限工具。"""

    async def request_permissions_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """申请当前环境的额外文件或网络权限。"""
        try:
            reject_model_execution(arguments)
        except ExecutionAuthorizationError as error:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error=error.detail,
            )

        try:
            permissions = normalize_permission_profile(
                arguments.get("permissions"),
                cwd=runtime.turn_context.cwd,
            )
        except ValueError as error:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error=str(error),
            )

        raw_environment_id = arguments.get("environment_id")
        if (
            raw_environment_id is not None
            and not isinstance(raw_environment_id, str)
        ):
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error="environment_id must be a string",
            )
        raw_reason = arguments.get("reason")
        if raw_reason is not None and not isinstance(raw_reason, str):
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error="reason must be a string",
            )

        environment_id = str(raw_environment_id or "").strip()
        reason = str(raw_reason or "").strip()
        call_id = str(runtime.call_id or "").strip()
        if not call_id:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error="request_permissions call_id is required",
            )

        if runtime.turn_context.permissions.approval_policy == "never":
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
            )
        if approval_coordinator is None:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error="permission approval coordinator is unavailable",
            )

        approval_id = f"local-permissions-{call_id}"
        approval = {
            "id": approval_id,
            "approval_id": approval_id,
            "request_id": approval_id,
            "call_id": call_id,
            "turn_id": runtime.turn_context.turn_id,
            "kind": "request_permissions",
            "tool": "request_permissions",
            "arguments": {
                "environment_id": environment_id or None,
                "reason": reason,
                "permissions": permissions,
            },
            "permissions": permissions,
            "environment_id": environment_id,
            "cwd": str(runtime.turn_context.cwd),
            "reason": reason,
            "available_decisions": [
                "grantForTurn",
                "grantForTurnWithStrictAutoReview",
                "grantForSession",
                "decline",
            ],
        }

        outcome = await approval_coordinator.request_outcome(approval)
        if outcome.decision == "cancel":
            if runtime.interrupt_turn is not None:
                interrupted = await runtime.interrupt_turn(call_id)
                if not interrupted:
                    raise ToolTurnInterrupted(
                        "request_permissions cancellation could not interrupt the turn"
                    )
            raise ToolTurnInterrupted("request_permissions cancelled")

        if outcome.decision not in {
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
        }:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
            )

        scope: PermissionGrantScope = (
            "session" if outcome.decision == "grantForSession" else "turn"
        )
        strict_auto_review = outcome.decision == "grantForTurnWithStrictAutoReview"
        permission_grants.grant(
            scope=scope,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            turn_id=runtime.turn_context.turn_id,
            environment_id=environment_id,
            cwd=runtime.turn_context.cwd,
            permissions=permissions,
            requested_permissions=permissions,
            strict_auto_review=strict_auto_review,
        )
        return permission_response_result(
            permissions=permissions,
            scope=scope,
            strict_auto_review=strict_auto_review,
        )

    return [
        BuiltinTool(
            name="request_permissions",
            description=(
                "申请额外的文件系统或网络权限，并等待用户授予本轮或当前会话的权限。"
                "权限只会授予申请内容的规范化子集。"
            ),
            input_schema=REQUEST_PERMISSIONS_INPUT_SCHEMA,
            meta={"class": "permissions"},
            handler=request_permissions_handler,
        ),
    ]


if __name__ == '__main__':
    pass
