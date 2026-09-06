# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.tools.authorization import (
    ExecutionAuthorizationError,
    reject_model_execution,
)
from agent.application.tools.coding_schemas import (
    WRITE_STDIN_INPUT_SCHEMA,
    exec_command_input_schema,
    shell_command_input_schema,
)
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.execution_results import (
    client_execution_failure,
    client_execution_result,
)
from agent.application.tools.results import LocalToolResult
from agent.domain.execution_policy import validate_sandbox_permission_arguments
from agent.ports.process_tools import WorkspaceProcessPort

__all__ = (
    "EXEC_COMMAND_TOOL",
    "SHELL_COMMAND_TOOL",
    "WRITE_STDIN_TOOL",
    "process_tools",
)


SHELL_COMMAND_TOOL = "shell_command"
EXEC_COMMAND_TOOL = "exec_command"
WRITE_STDIN_TOOL = "write_stdin"

_EXECUTION_ONLY_FIELDS = frozenset({
    "environment_id",
    "justification",
    "patch_scope",
    "policy_fingerprint",
    "tty",
})


def process_tools(
    executor: WorkspaceProcessPort,
    *,
    exec_permission_approvals_enabled: bool = False,
) -> list[ClientTool]:
    """构造通过工作区进程端口执行的本地命令工具。"""

    async def shell_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """执行单条本地命令。"""
        try:
            execution_arguments = _process_arguments(
                arguments,
                exec_permission_approvals_enabled=(
                    exec_permission_approvals_enabled
                ),
                preserve_tty=False,
            )
        except ExecutionAuthorizationError as error:
            return _authorization_failure(
                executor,
                tool=SHELL_COMMAND_TOOL,
                arguments=arguments,
                error=error,
            )

        result = await executor.shell_command(
            **execution_arguments,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            run_id=runtime.turn_context.turn_id,
            environment_id="local",
            sandbox_mode=runtime.turn_context.permissions.sandbox_mode,
        )
        return client_execution_result(
            tool=SHELL_COMMAND_TOOL,
            arguments=execution_arguments,
            result=result,
            target=executor.agent_id,
        )

    async def exec_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """启动可持续读写的本地命令会话。"""
        try:
            execution_arguments = _process_arguments(
                arguments,
                exec_permission_approvals_enabled=(
                    exec_permission_approvals_enabled
                ),
                preserve_tty=True,
            )
        except ExecutionAuthorizationError as error:
            return _authorization_failure(
                executor,
                tool=EXEC_COMMAND_TOOL,
                arguments=arguments,
                error=error,
            )

        result = await executor.exec_command(
            **execution_arguments,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            run_id=runtime.turn_context.turn_id,
            environment_id="local",
            sandbox_mode=runtime.turn_context.permissions.sandbox_mode,
        )
        return client_execution_result(
            tool=EXEC_COMMAND_TOOL,
            arguments=execution_arguments,
            result=result,
            target=executor.agent_id,
        )

    async def write_stdin_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """写入、轮询或控制持续命令会话。"""
        try:
            reject_model_execution(arguments)
            execution_arguments = dict(arguments)
        except ExecutionAuthorizationError as error:
            return _authorization_failure(
                executor,
                tool=WRITE_STDIN_TOOL,
                arguments=arguments,
                error=error,
            )

        result = await executor.write_stdin(
            **execution_arguments,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            call_id=str(runtime.call_id or ""),
        )
        return client_execution_result(
            tool=WRITE_STDIN_TOOL,
            arguments=execution_arguments,
            result=result,
            target=executor.agent_id,
        )

    return [
        ClientTool(
            name=SHELL_COMMAND_TOOL,
            description=(
                "在工作区内执行单条本地 shell 命令。命令由系统默认 shell 解释执行，"
                "适合运行单个诊断命令、测试、构建或脚本。代码修改请使用 apply_patch。"
            ),
            input_schema=shell_command_input_schema(
                exec_permission_approvals_enabled=exec_permission_approvals_enabled,
            ),
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=shell_command_handler,
        ),
        ClientTool(
            name=EXEC_COMMAND_TOOL,
            description=(
                "启动可持续读写的本地 shell 命令会话。适合长耗时任务和持续输出；"
                "需要 REPL、终端控制或交互式程序时设置 tty=true 创建原生 PTY。"
            ),
            input_schema=exec_command_input_schema(
                exec_permission_approvals_enabled=exec_permission_approvals_enabled,
            ),
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=exec_command_handler,
        ),
        ClientTool(
            name=WRITE_STDIN_TOOL,
            description=(
                "向 exec_command 创建的命令会话写入标准输入、调整 PTY 尺寸，"
                "或在 stdin 为空时轮询增量输出。"
            ),
            input_schema=WRITE_STDIN_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=write_stdin_handler,
        ),
    ]


def _process_arguments(
    arguments: dict[str, typing.Any],
    *,
    exec_permission_approvals_enabled: bool,
    preserve_tty: bool,
) -> dict[str, typing.Any]:
    """校验权限覆盖并返回本地执行器可见参数。"""
    reject_model_execution(arguments)
    if (
        not exec_permission_approvals_enabled
        and str(arguments.get("sandbox_permissions") or "").strip().casefold()
        == "with_additional_permissions"
    ):
        raise ExecutionAuthorizationError(
            "additional_permissions_disabled",
            "inline additional permissions are disabled",
        )
    try:
        validate_sandbox_permission_arguments(arguments)
    except ValueError as error:
        raise ExecutionAuthorizationError(
            "sandbox_permissions_invalid",
            str(error),
        ) from error
    result = {
        key: value
        for key, value in arguments.items()
        if key not in _EXECUTION_ONLY_FIELDS
    }
    if preserve_tty and "tty" in arguments:
        result["tty"] = arguments["tty"]
    return result


def _authorization_failure(
    executor: WorkspaceProcessPort,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    error: ExecutionAuthorizationError,
) -> LocalToolResult:
    """把进程参数门禁失败投影为稳定工具结果。"""
    public_arguments = {
        key: value
        for key, value in arguments.items()
        if key != "execution"
    }
    return client_execution_failure(
        tool=tool,
        arguments=public_arguments,
        target=executor.agent_id,
        reason=error.reason,
        details={
            "tool": tool,
            "error": "execution_policy_blocked",
            "detail": error.detail,
        },
    )


if __name__ == '__main__':
    pass
