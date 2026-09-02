# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.application.tools.authorization import (
    ExecutionAuthorizationError,
    reject_model_execution,
)
from agent.application.tools.coding_schemas import APPLY_PATCH_INPUT_SCHEMA
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.execution_results import (
    client_execution_failure,
    client_execution_result,
)
from agent.application.tools.results import LocalToolResult
from agent.ports.patching import WorkspacePatchPort

__all__ = (
    "APPLY_PATCH_TOOL",
    "patch_tools",
)

APPLY_PATCH_TOOL = "apply_patch"


def patch_tools(patcher: WorkspacePatchPort) -> list[ClientTool]:
    """构造通过工作区补丁端口执行的文本修改工具。"""

    async def apply_patch_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """校验 Turn 权限后应用补丁并记录精确差异。"""
        public_arguments = {
            key: value
            for key, value in arguments.items()
            if key != "execution"
        }
        if runtime.turn_context.permissions.sandbox_mode == "read-only":
            return client_execution_failure(
                tool=APPLY_PATCH_TOOL,
                arguments=public_arguments,
                target=patcher.agent_id,
                reason="sandbox_read_only",
                details={
                    "tool": APPLY_PATCH_TOOL,
                    "error": "sandbox_denied",
                    "sandbox_mode": "read-only",
                },
            )

        try:
            reject_model_execution(arguments)
            _require_workspace_write(runtime)
            patch_arguments = _patch_arguments(arguments)
        except ExecutionAuthorizationError as error:
            return client_execution_failure(
                tool=APPLY_PATCH_TOOL,
                arguments=public_arguments,
                target=patcher.agent_id,
                reason=error.reason,
                details={
                    "tool": APPLY_PATCH_TOOL,
                    "error": "execution_policy_blocked",
                    "detail": error.detail,
                },
            )

        result = patcher.apply_patch(**patch_arguments)
        data = result.get("data")
        if isinstance(data, Mapping) and "delta" in data:
            delta = data.get("delta")
            patcher.track_patch_delta(
                dict(delta) if isinstance(delta, Mapping) else {}
            )

        return client_execution_result(
            tool=APPLY_PATCH_TOOL,
            arguments=patch_arguments,
            result=result,
            target=patcher.agent_id,
        )

    return [ClientTool(
        name=APPLY_PATCH_TOOL,
        description=(
            "应用文本补丁修改工作区文件。调用参数是对象，必填字段为 patch，形状为 "
            "{\"patch\": \"补丁文本\"}。支持 *** Begin Patch、标准 unified diff 和 "
            "git diff，以及多文件、新建、更新、删除和上下文校验。"
        ),
        input_schema=APPLY_PATCH_INPUT_SCHEMA,
        meta={"hidden": False, "domain": "coding", "class": "workspace"},
        handler=apply_patch_handler,
    )]


def _require_workspace_write(runtime: ToolHandlerContext) -> None:
    """拒绝只读 Turn 的工作区写入。"""
    if runtime.turn_context.permissions.sandbox_mode == "read-only":
        raise ExecutionAuthorizationError(
            "sandbox_read_only",
            "read-only mode does not allow workspace writes",
        )


def _patch_arguments(
    arguments: Mapping[str, typing.Any],
) -> dict[str, typing.Any]:
    """建立补丁执行器接收的规范参数。"""
    expected_sha256 = arguments.get("expected_sha256")
    if expected_sha256 is not None and not isinstance(expected_sha256, Mapping):
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            "apply_patch expected_sha256 must be an object or null",
        )
    hashes = (
        {str(path): str(digest) for path, digest in expected_sha256.items()}
        if isinstance(expected_sha256, Mapping)
        else None
    )
    return {
        "patch": str(arguments.get("patch") or ""),
        "expected_sha256": hashes,
        "force": bool(arguments.get("force", False)),
    }


if __name__ == "__main__":
    pass
