# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import types as mcp_types
from mind_app.native_coding import NativeCoding
from mind_app.native_coding.execution_authorization import (
    ExecutionAuthorizationError,
    canonical_arguments,
    validate_execution_authorization,
    validate_runtime_identity
)
from mind_app.client_tools.types import (
    ClientTool,
    ClientToolRuntime
)
from .schemas import (
    APPLY_PATCH_INPUT_SCHEMA,
    EXEC_COMMAND_INPUT_SCHEMA,
    SHELL_COMMAND_INPUT_SCHEMA,
    WRITE_STDIN_INPUT_SCHEMA
)


def build_coding_result(
    *,
    tool: str,
    args: dict[str, typing.Any],
    raw: dict[str, typing.Any],
    target: str
) -> mcp_types.CallToolResult:
    """构造编码工具调用结果。"""
    output = dict(raw or {})
    ok     = bool(output.get("ok"))

    data = output.get("data")
    if not isinstance(data, dict):
        data = {}

    text        = str(output.get("text") or data or "")
    result_text = f"tool={tool} target={target} ok={ok} {text}"

    structured: dict[str, typing.Any] | None = {
        "ok"          : ok,
        "tool"        : tool,
        "args"        : dict(args or {}),
        "text"        : result_text,
        "attachments" : list(output.get("attachments") or []),
        "data"        : data,
        "target"      : target
    }

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=result_text)],
        structuredContent=structured,
        isError=not ok,
        _meta={"logs": list(output.get("logs") or [])}
    )


def authorization_failure_result(
    coding: NativeCoding,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    error: ExecutionAuthorizationError,
) -> mcp_types.CallToolResult:
    """构造执行授权失败结果。"""
    raw = coding.fail_result(
        error.reason,
        tool=tool,
        error="execution_policy_blocked",
        detail=error.detail,
    )
    return build_coding_result(
        tool=tool,
        args={
            key: value
            for key, value in arguments.items()
            if key != "execution"
        },
        raw=raw,
        target=coding.agent_id,
    )


def sandbox_failure_result(
    coding: NativeCoding,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
) -> mcp_types.CallToolResult:
    """构造只读沙箱拒绝写入或进程执行的结果。"""
    raw = coding.fail_result(
        "sandbox_read_only",
        tool=tool,
        error="sandbox_denied",
        sandbox_mode="read-only",
    )
    return build_coding_result(
        tool=tool,
        args={key: value for key, value in arguments.items() if key != "execution"},
        raw=raw,
        target=coding.agent_id,
    )


def read_only_sandbox(runtime: ClientToolRuntime) -> bool:
    """判断当前客户端工具是否运行在只读沙箱中。"""
    return runtime.turn_context.permissions.sandbox_mode == "read-only"


def validate_unsandboxed_process_authorization(
    runtime: ClientToolRuntime
) -> None:
    """校验无系统进程沙箱时的本地进程执行权限。"""
    permissions = runtime.turn_context.permissions

    sandbox_mode = permissions.sandbox_mode
    if sandbox_mode == "read-only":
        raise ExecutionAuthorizationError(
            "sandbox_read_only",
            "read-only mode does not allow local process execution"
        )

    execution   = runtime.execution if isinstance(runtime.execution, dict) else {}
    state       = str(execution.get("state") or "").strip().lower()
    raw_reasons = execution.get("reasons")

    reasons = {
        str(reason).strip()
        for reason in raw_reasons
        if str(reason).strip()
    } if isinstance(raw_reasons, list) else set()

    approved = state == "approved" or (
        state == "allowed" and "session_approval_matched" in reasons
    )

    if sandbox_mode == "workspace-write" and not approved:
        raise ExecutionAuthorizationError(
            "unsandboxed_process_approval_required",
            "workspace-write local process execution requires explicit approval"
        )
    if (
        sandbox_mode == "danger-full-access"
        and permissions.approval_policy == "untrusted"
        and not approved
    ):
        raise ExecutionAuthorizationError(
            "untrusted_process_approval_required",
            "untrusted local process execution requires explicit approval"
        )


def validate_workspace_write_authorization(runtime: ClientToolRuntime) -> None:
    """校验客户端工作区写入权限。"""
    permissions = runtime.turn_context.permissions
    if permissions.sandbox_mode == "read-only":
        raise ExecutionAuthorizationError(
            "sandbox_read_only",
            "read-only mode does not allow workspace writes"
        )


def trusted_canonical(
    runtime: ClientToolRuntime,
    *,
    tool: str,
    require_grant: bool = True,
) -> dict[str, typing.Any]:
    """从可信运行上下文读取并校验 canonical 参数。"""
    turn = runtime.turn_context
    validate_runtime_identity(cid=turn.cid, sid=turn.sid, call_id=runtime.call_id)
    validate_execution_authorization(runtime.execution, require_grant=require_grant)
    return canonical_arguments(runtime.execution, tool=tool)


def reject_model_execution(arguments: dict[str, typing.Any]) -> None:
    """拒绝从模型工具参数传入执行授权。"""
    if "execution" in arguments:
        raise ExecutionAuthorizationError(
            "model_execution_forbidden", "execution must come from the trusted tool event"
        )


def coding_tools(native_coding: NativeCoding | None = None) -> list[ClientTool]:
    """返回编码工具列表。"""
    coding = native_coding or NativeCoding()

    async def shell_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """执行单条命令。"""
        if read_only_sandbox(runtime):
            return sandbox_failure_result(
                coding,
                tool="shell_command",
                arguments=arguments,
            )
        try:
            reject_model_execution(arguments)
            args = trusted_canonical(runtime, tool="shell_command")
            validate_unsandboxed_process_authorization(runtime)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="shell_command", arguments=arguments, error=exc
            )

        raw = await coding.shell_command(**args, execution=runtime.execution)

        return build_coding_result(
            tool="shell_command",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def apply_patch_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """应用补丁。"""
        if read_only_sandbox(runtime):
            return sandbox_failure_result(
                coding,
                tool="apply_patch",
                arguments=arguments,
            )

        try:
            reject_model_execution(arguments)
            validate_workspace_write_authorization(runtime)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="apply_patch", arguments=arguments, error=exc
            )

        args = {
            "patch"           : str(arguments.get("patch") or ""),
            "expected_sha256" : arguments.get("expected_sha256"),
            "force"           : bool(arguments.get("force", False))
        }

        raw  = coding.apply_patch(**args)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

        if "delta" in data:
            coding.track_patch_delta(data.get("delta"))

        return build_coding_result(
            tool="apply_patch",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def exec_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """启动可持续命令会话。"""
        if read_only_sandbox(runtime):
            return sandbox_failure_result(
                coding,
                tool="exec_command",
                arguments=arguments,
            )
        try:
            reject_model_execution(arguments)
            args = trusted_canonical(runtime, tool="exec_command")
            validate_unsandboxed_process_authorization(runtime)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="exec_command", arguments=arguments, error=exc
            )

        raw = await coding.exec_command(
            **args,
            execution=runtime.execution,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
        )

        return build_coding_result(
            tool="exec_command",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    async def write_stdin_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """写入或轮询命令会话。"""
        try:
            reject_model_execution(arguments)

            turn = runtime.turn_context

            validate_runtime_identity(
                cid=turn.cid, sid=turn.sid, call_id=runtime.call_id
            )

            args = canonical_arguments(runtime.execution, tool="write_stdin")

            mutates_process = bool(args["stdin"]) or args.get("control") != "none"

            if read_only_sandbox(runtime) and mutates_process:
                return sandbox_failure_result(
                    coding,
                    tool="write_stdin",
                    arguments=arguments,
                )
            validate_execution_authorization(runtime.execution, require_grant=True)
            if mutates_process:
                validate_unsandboxed_process_authorization(runtime)

        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="write_stdin", arguments=arguments, error=exc
            )

        raw = await coding.write_stdin(
            **args,
            execution=runtime.execution,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            call_id=str(runtime.call_id or ""),
        )

        return build_coding_result(
            tool="write_stdin",
            args=args,
            raw=raw,
            target=coding.agent_id
        )

    return [
        ClientTool(
            name="shell_command",
            description=(
                "在工作区内执行单条本地 shell 命令。命令由系统默认 shell 解释执行，"
                "适合运行单个诊断命令、测试、构建或脚本。代码修改请使用 apply_patch。"
            ),
            input_schema=SHELL_COMMAND_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=shell_command_handler,
        ),
        ClientTool(
            name="exec_command",
            description=(
                "启动可持续读写的本地 shell 命令会话。适合长耗时任务、交互式任务和持续输出。"
                "该实现使用标准输入输出管道，不提供真实 PTY。"
            ),
            input_schema=EXEC_COMMAND_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=exec_command_handler,
        ),
        ClientTool(
            name="write_stdin",
            description=(
                "向 exec_command 创建的命令会话写入标准输入，或在 stdin 为空时轮询增量输出。"
            ),
            input_schema=WRITE_STDIN_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=write_stdin_handler,
        ),
        ClientTool(
            name="apply_patch",
            description=(
                "应用严格 apply_patch 补丁修改工作区文件。支持多文件、新建、更新、删除和上下文校验。"
            ),
            input_schema=APPLY_PATCH_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "workspace"},
            handler=apply_patch_handler,
        ),
    ]


if __name__ == '__main__':
    pass
