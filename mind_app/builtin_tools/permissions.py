# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mcp import types as mcp_types
from protocol.client.turn_control import TurnControlRequestError
from mind_app.approval.permission_grants import normalize_permission_profile
from mind_app.client_tools.coding.schemas import REQUEST_PERMISSIONS_INPUT_SCHEMA
from mind_app.client_tools.types import ClientToolRuntime
from mind_app.native_coding.execution_authorization import (
    ExecutionAuthorizationError,
    reject_model_execution
)
from .types import BuiltinTool

if typing.TYPE_CHECKING:
    from mind_app.approval.coordinator import ApprovalCoordinator


def permission_response_result(
    *,
    permissions: dict[str, typing.Any],
    scope: str,
    strict_auto_review: bool,
    error: str = "",
) -> mcp_types.CallToolResult:
    """构造权限内置工具的结构化回执。"""
    response: dict[str, typing.Any] = {
        "permissions": dict(permissions),
        "scope": scope,
        "strict_auto_review": bool(strict_auto_review),
    }
    if error:
        response["error"] = error
    text = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent={
            "ok": not bool(error),
            "tool": "request_permissions",
            "source": "builtin",
            "args": {},
            "text": text,
            "attachments": [],
            "data": response,
        },
        isError=bool(error),
        _meta={"logs": []},
    )


def permission_tools(
    approval_coordinator: "ApprovalCoordinator | None",
) -> list[BuiltinTool]:
    """构造可选的权限内置工具。"""

    async def request_permissions_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime,
    ) -> mcp_types.CallToolResult:
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
        if raw_environment_id is not None and not isinstance(raw_environment_id, str):
            return permission_response_result(
                permissions={}, scope="turn", strict_auto_review=False,
                error="environment_id must be a string",
            )
        raw_reason = arguments.get("reason")
        if raw_reason is not None and not isinstance(raw_reason, str):
            return permission_response_result(
                permissions={}, scope="turn", strict_auto_review=False,
                error="reason must be a string",
            )

        environment_id = str(raw_environment_id or "").strip()
        reason = str(raw_reason or "").strip()
        call_id = str(runtime.call_id or "").strip()
        if not call_id:
            return permission_response_result(
                permissions={}, scope="turn", strict_auto_review=False,
                error="request_permissions call_id is required",
            )

        if runtime.turn_context.permissions.approval_policy == "never":
            return permission_response_result(
                permissions={}, scope="turn", strict_auto_review=False,
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
                    raise TurnControlRequestError(
                        "request_permissions cancellation could not interrupt the turn"
                    )
            raise TurnControlRequestError("request_permissions cancelled")

        if outcome.decision not in {
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
        }:
            return permission_response_result(
                permissions={}, scope="turn", strict_auto_review=False,
            )

        scope = "session" if outcome.decision == "grantForSession" else "turn"
        strict_auto_review = outcome.decision == "grantForTurnWithStrictAutoReview"
        store = runtime.turn_context.permission_grants
        if store is None:
            return permission_response_result(
                permissions={},
                scope="turn",
                strict_auto_review=False,
                error="permission grant store is unavailable",
            )
        store.grant(
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
