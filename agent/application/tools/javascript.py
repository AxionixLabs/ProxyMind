# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.approvals.amendments import approval_execpolicy_amendment
from agent.application.tools.authorization import (
    ExecutionAuthorizationError,
    ToolTurnInterrupted,
    reject_model_execution,
)
from agent.application.tools.coding_schemas import (
    JS_REPL_INPUT_SCHEMA,
    JS_REPL_RESET_INPUT_SCHEMA,
)
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.execution_results import (
    client_execution_failure,
    client_execution_result,
)
from agent.application.tools.results import LocalToolResult
from agent.domain.execution_policy import validate_sandbox_permission_arguments
from agent.domain.permission_profiles import normalize_permission_profile
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.javascript import (
    JavaScriptExecution,
    JavaScriptExecutionError,
    JavaScriptExecutionPort,
    JavaScriptExecutionRequest,
    JavaScriptResetDisposition,
    NestedToolOutput,
)
from agent.ports.workspace import ExecutionPolicy
from protocol.schema.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS

__all__ = (
    "JS_REPL_RESET_TOOL",
    "JS_REPL_TOOL",
    "JS_REPL_TOOL_NAMES",
    "javascript_tools",
)


JS_REPL_TOOL = "js_repl"
JS_REPL_RESET_TOOL = "js_repl_reset"
JS_REPL_TOOL_NAMES = frozenset({JS_REPL_TOOL, JS_REPL_RESET_TOOL})
NESTED_PROCESS_TOOLS = frozenset({
    "shell_command",
    "exec_command",
    "write_stdin",
})

JS_REPL_DESCRIPTION = (
    "在当前 sid 持有的持久 Node.js Kernel 中执行 JavaScript；顶层 await、fetch 和动态 "
    "await import(...) 可用，变量及对象跨 Cell、跨模型轮次保留。host.cwd、host.homeDir "
    "和 host.tmpDir 分别提供工作目录、主目录和会话临时目录。使用 console.log 输出结果。"
    "顶层静态 import 不受支持；包和本地 .js/.mjs 文件应使用动态 import，本地模块会在每次"
    "执行时重新加载，顶层 binding 则保留到 reset。若名称已声明，应复用、重新赋值或换名，"
    "不要为持久代码包裹整段块作用域。Kernel 禁止 process、node:process、child_process、"
    "node:child_process、worker_threads 和 node:worker_threads。需要执行系统命令、启动程序"
    "或打开浏览器时，不要退出 js_repl 改调外层 shell，而应在 JavaScript 内调用 await "
    "host.tool(\"shell_command\", {command: \"...\", sandbox_permissions: "
    "\"require_escalated\", justification: \"...\"})；Windows 打开网页示例为 await "
    "host.tool(\"shell_command\", {command: 'Start-Process \"https://example.com\"', "
    "sandbox_permissions: \"require_escalated\", justification: \"打开默认浏览器\"})。"
    "host.tool(name, args) 可调用当前会话的其他工具，并向 JavaScript 返回包含"
    "结构化真实结果的 function_call_output；嵌套调用沿用对应工具的审批和展示流程。"
    "Cell 自身只展示 console.log 等显式输出。"
    "host.emitImage(value) 才会把图片附加到外层结果；它接受图片 data URL、单个 input_image、"
    "{bytes, mimeType, detail} 或仅含一张图片且不含文本的工具结果，并可在一个 Cell 中调用多次。"
    "host.tool 和 host.emitImage 的引用可跨 Cell 保存，但 Cell 结束后触发的异步回调没有有效"
    "执行上下文。文件写入遵守当前 read-only、workspace-write 或 full-access 权限；只读模式"
    "仍允许写入 host.tmpDir。不要直接写 stdin/stdout/stderr。执行超时、活动执行被中断或 "
    "Kernel 异常退出会清空上下文；普通 Cell 或模型轮次结束不会。"
)


def javascript_tools(
    executor: JavaScriptExecutionPort,
    *,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
) -> list[ClientTool]:
    """构造持久 JavaScript 内核工具及其嵌套调用编排。"""

    async def js_repl_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """执行一个持久 JavaScript 单元。"""
        try:
            reject_model_execution(arguments)
            args = _js_repl_arguments(arguments)
        except ExecutionAuthorizationError as error:
            return _authorization_failure(
                executor,
                tool=JS_REPL_TOOL,
                arguments=arguments,
                error=error,
            )

        async def call_nested_tool(
            tool_name: str,
            tool_arguments: dict[str, typing.Any],
            call_id: str,
        ) -> NestedToolOutput:
            """在当前 Turn 的工具生命周期内执行嵌套调用。"""
            await _authorize_nested_tool(
                runtime,
                tool=tool_name,
                arguments=tool_arguments,
                approval_coordinator=approval_coordinator,
                execution_policy=execution_policy,
                call_id=call_id,
            )
            if runtime.nested_tool_dispatch is None:
                raise RuntimeError("nested tool dispatch is unavailable")
            return await runtime.nested_tool_dispatch(
                tool_name,
                tool_arguments,
                call_id,
            )

        turn = runtime.turn_context
        try:
            execution = await executor.execute(
                request=JavaScriptExecutionRequest(
                    session_id=turn.sid,
                    code=str(args["code"]),
                    cwd=turn.cwd,
                    access_mode=turn.permissions.sandbox_mode,
                    timeout_ms=int(args["timeout_ms"]),
                ),
                call_tool=call_nested_tool,
            )
        except JavaScriptExecutionError as error:
            result = _javascript_failure_result(
                "js_repl_execution_failed",
                error,
            )
        else:
            result = _javascript_execution_result(execution)
        return client_execution_result(
            tool=JS_REPL_TOOL,
            arguments=args,
            result=result,
            target=executor.agent_id,
        )

    async def js_repl_reset_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """重置当前会话的 JavaScript 内核。"""
        try:
            reject_model_execution(arguments)
            args = _js_repl_reset_arguments(arguments)
        except ExecutionAuthorizationError as error:
            return _authorization_failure(
                executor,
                tool=JS_REPL_RESET_TOOL,
                arguments=arguments,
                error=error,
            )

        try:
            disposition = await executor.reset_session(runtime.turn_context.sid)
        except JavaScriptExecutionError as error:
            result = _javascript_failure_result(
                "js_repl_reset_failed",
                error,
            )
        else:
            result = {
                "ok": True,
                "text": "JavaScript kernel reset.",
                "data": {
                    "reset": disposition is JavaScriptResetDisposition.RESET,
                },
                "logs": [],
            }
        return client_execution_result(
            tool=JS_REPL_RESET_TOOL,
            arguments=args,
            result=result,
            target=executor.agent_id,
        )

    return [
        ClientTool(
            name=JS_REPL_TOOL,
            description=JS_REPL_DESCRIPTION,
            input_schema=JS_REPL_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=js_repl_handler,
        ),
        ClientTool(
            name=JS_REPL_RESET_TOOL,
            description=(
                "重置当前对话会话的持久 JavaScript 内核。下次调用 js_repl 时会按需启动"
                "新的 Node.js 进程，之前定义的变量和对象将不可用。"
            ),
            input_schema=JS_REPL_RESET_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=js_repl_reset_handler,
        ),
    ]


def _js_repl_arguments(arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """校验并补齐 JavaScript 单元参数。"""
    extra = set(arguments).difference({"code", "timeout_ms"})
    if extra:
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            f"js_repl arguments contain unsupported fields: {sorted(extra)}",
        )

    code = arguments.get("code")
    if not isinstance(code, str):
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            "js_repl code must be a string",
        )

    timeout_ms = arguments.get("timeout_ms", 30000)
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or timeout_ms < 0
    ):
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            "js_repl timeout_ms must be a non-negative integer",
        )
    return {"code": code, "timeout_ms": timeout_ms}


def _js_repl_reset_arguments(
    arguments: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """校验 JavaScript 内核重置参数。"""
    if arguments:
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            "js_repl_reset arguments contain unsupported fields: "
            f"{sorted(arguments)}",
        )
    return {}


def _nested_canonical_arguments(
    tool: str,
    arguments: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """补齐嵌套进程工具需要的 canonical 默认参数。"""
    if tool == "shell_command":
        canonical = {
            "command": str(arguments.get("command") or ""),
            "cwd": str(arguments.get("cwd") or "."),
            "timeout_sec": int(arguments.get("timeout_sec") or 60),
            "output_encoding": str(arguments.get("output_encoding") or "auto"),
            "sandbox_permissions": str(
                arguments.get("sandbox_permissions") or "use_default"
            ),
            **(
                {"justification": str(arguments.get("justification") or "")}
                if "justification" in arguments
                else {}
            ),
        }
        if arguments.get("environment_id") not in (None, ""):
            canonical["environment_id"] = str(arguments["environment_id"])
        if arguments.get("additional_permissions") is not None:
            canonical["additional_permissions"] = arguments[
                "additional_permissions"
            ]
        return canonical
    if tool == "exec_command":
        canonical = {
            "command": str(arguments.get("command") or ""),
            "cwd": str(arguments.get("cwd") or "."),
            "shell": str(arguments.get("shell") or "") or None,
            "tty": bool(arguments.get("tty", False)),
            "terminal_rows": int(arguments.get("terminal_rows") or 24),
            "terminal_columns": int(arguments.get("terminal_columns") or 80),
            "yield_time_ms": int(arguments.get("yield_time_ms", 1000)),
            "max_output_chars": int(arguments.get("max_output_chars") or 24000),
            "timeout_sec": int(arguments.get("timeout_sec") or 1800),
            "idle_timeout_sec": int(arguments.get("idle_timeout_sec") or 300),
            "sandbox_permissions": str(
                arguments.get("sandbox_permissions") or "use_default"
            ),
            **(
                {"justification": str(arguments.get("justification") or "")}
                if "justification" in arguments
                else {}
            ),
        }
        if arguments.get("environment_id") not in (None, ""):
            canonical["environment_id"] = str(arguments["environment_id"])
        if arguments.get("additional_permissions") is not None:
            canonical["additional_permissions"] = arguments[
                "additional_permissions"
            ]
        return canonical
    if tool == "write_stdin":
        return {
            "session_id": str(arguments.get("session_id") or ""),
            "stdin": str(arguments.get("stdin") or ""),
            "wait_ms": int(arguments.get("wait_ms", 1000)),
            "max_output_chars": int(arguments.get("max_output_chars") or 12000),
            "control": str(arguments.get("control") or "none"),
            **(
                {
                    "terminal_rows": int(arguments["terminal_rows"]),
                    "terminal_columns": int(arguments["terminal_columns"]),
                }
                if str(arguments.get("control") or "none") == "resize"
                else {}
            ),
        }
    return dict(arguments)


async def _authorize_nested_tool(
    runtime: ToolHandlerContext,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    approval_coordinator: ApprovalCoordinatorPort | None,
    execution_policy: ExecutionPolicy | None,
    call_id: str,
) -> None:
    """按本地规则审批 JavaScript 发起的嵌套进程调用。"""
    if tool not in NESTED_PROCESS_TOOLS:
        return None
    if execution_policy is None:
        raise ExecutionAuthorizationError(
            "nested_tool_policy_unavailable",
            f"execution policy is required for nested {tool} command",
        )

    permissions = runtime.turn_context.permissions
    try:
        sandbox_permissions = validate_sandbox_permission_arguments(arguments)
    except ValueError as error:
        raise ExecutionAuthorizationError(
            "sandbox_permissions_invalid",
            str(error),
        ) from error

    command_cwd = arguments.get("cwd") or runtime.turn_context.cwd
    additional_permissions = arguments.get("additional_permissions")
    if additional_permissions is not None:
        try:
            additional_permissions = normalize_permission_profile(
                additional_permissions,
                cwd=command_cwd,
            )
        except ValueError as error:
            raise ExecutionAuthorizationError(
                "additional_permissions_invalid",
                str(error),
            ) from error

    requirement = execution_policy.create_exec_approval_requirement_for_command(
        str(arguments.get("command") or ""),
        approval_policy=permissions.approval_policy,
        sandbox_mode=permissions.sandbox_mode,
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
    if requirement.state == "forbidden":
        raise ExecutionAuthorizationError(
            "local_exec_policy_forbidden",
            requirement.reason
            or f"local execution policy forbids nested {tool} command",
        )

    additional_approval_required = (
        sandbox_permissions == "with_additional_permissions"
        and bool(additional_permissions)
        and not _nested_permission_granted(
        runtime,
        arguments,
        permissions=additional_permissions,
        cwd=command_cwd,
    )
    )
    if (
        additional_approval_required
        and runtime.turn_context.permissions.approval_policy == "never"
    ):
        raise ExecutionAuthorizationError(
            "additional_permissions_approval_required",
            "additional permissions require approval, but approval policy is never",
        )

    requires_approval = (
        requirement.state == "needs_approval"
        or additional_approval_required
    )
    approved = not requires_approval
    canonical = _nested_canonical_arguments(tool, arguments)
    if additional_permissions is not None:
        canonical["additional_permissions"] = additional_permissions

    if requires_approval:
        if approval_coordinator is None:
            raise ExecutionAuthorizationError(
                "nested_tool_approval_unavailable",
                f"approval coordinator is required for nested {tool} command",
            )

        agent = runtime.turn_context.agent
        approval: dict[str, typing.Any] = {
            "id": f"nested_{call_id}",
            "call_id": call_id,
            "tool": tool,
            "arguments": canonical,
            "command": str(canonical.get("command") or ""),
            "environment": (
                "host" if sandbox_permissions == "require_escalated" else "local"
            ),
            "justification": str(
                arguments.get("justification")
                or "JavaScript requested a nested local process tool."
            ),
            "agent_id": agent.agent_id,
            "agent_type": agent.agent_type,
            "agent_depth": agent.depth,
        }

        amendment = requirement.proposed_execpolicy_amendment
        if amendment is not None:
            approval["proposed_execpolicy_amendment"] = {
                "id": amendment.id,
                "command_prefix": list(amendment.command_prefix),
                "display": amendment.display,
            }

        outcome = await approval_coordinator.request_outcome(approval)
        decision = outcome.decision
        if decision == "cancel":
            if runtime.interrupt_turn is None:
                raise ExecutionAuthorizationError(
                    "nested_tool_approval_cancelled",
                    f"nested {tool} approval was cancelled",
                )
            interrupted = await runtime.interrupt_turn(call_id)
            if not interrupted:
                raise ExecutionAuthorizationError(
                    "nested_tool_approval_cancelled",
                    f"nested {tool} approval could not interrupt the turn",
                )
            raise ToolTurnInterrupted(
                f"nested {tool} approval cancelled the turn"
            )

        approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS
        if decision == "acceptForSession":
            execution_policy.add_approval_for_session(
                str(canonical.get("command") or ""),
                tool=tool,
                cwd=canonical.get("cwd") or runtime.turn_context.cwd,
                sandbox_permissions=sandbox_permissions,
                environment_id=canonical.get("environment_id"),
                tty=canonical.get("tty"),
                additional_permissions=canonical.get("additional_permissions"),
                policy_fingerprint=canonical.get("policy_fingerprint"),
                patch_scope=canonical.get("patch_scope"),
            )
        elif decision == "acceptWithExecpolicyAmendment":
            proposal = approval_execpolicy_amendment(approval)
            if proposal is None:
                approved = False
            else:
                try:
                    execution_policy.persist_execpolicy_amendment({
                        "command_prefix": list(proposal.command_prefix),
                    })
                except (OSError, UnicodeError, ValueError):
                    approved = False

    if not approved:
        raise ExecutionAuthorizationError(
            "nested_tool_approval_denied",
            f"nested {tool} call was not approved",
        )


def _nested_permission_granted(
    runtime: ToolHandlerContext,
    arguments: dict[str, typing.Any],
    *,
    permissions: dict[str, typing.Any],
    cwd: typing.Any,
) -> bool:
    """判断嵌套命令是否已有覆盖申请的权限。"""
    store = runtime.turn_context.permission_grants
    if store is None:
        return False
    return bool(store.has_grant(
        cid=runtime.turn_context.cid,
        sid=runtime.turn_context.sid,
        turn_id=runtime.turn_context.turn_id,
        environment_id=arguments.get("environment_id"),
        cwd=cwd,
        permissions=permissions,
    ))


def _authorization_failure(
    executor: JavaScriptExecutionPort,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    error: ExecutionAuthorizationError,
) -> LocalToolResult:
    """把 JavaScript 参数门禁失败投影为稳定工具结果。"""
    return client_execution_failure(
        tool=tool,
        arguments={
            key: value
            for key, value in arguments.items()
            if key != "execution"
        },
        target=executor.agent_id,
        reason=error.reason,
        details={
            "tool": tool,
            "error": "execution_policy_blocked",
            "detail": error.detail,
        },
    )


def _javascript_execution_result(
    execution: JavaScriptExecution,
) -> dict[str, typing.Any]:
    """把 Sidecar 结果投影为既有本地工具结果信封。"""
    output = _clip_javascript_output(execution.output)
    return {
        "ok": True,
        "text": output or "JavaScript cell completed.",
        "attachments": list(execution.attachments),
        "data": {
            "output": output,
            "output_truncated": output != execution.output,
        },
        "logs": [],
    }


def _javascript_failure_result(
    reason: str,
    error: JavaScriptExecutionError,
) -> dict[str, typing.Any]:
    """把 Sidecar 失败投影为既有本地工具失败信封。"""
    detail = error.detail.strip() or type(error).__name__
    return {
        "ok": False,
        "text": f"native coding failed: {reason}",
        "attachments": [],
        "data": {
            "reason": reason,
            "error": detail,
            "failure_context": {"error": detail},
        },
        "logs": [],
    }


def _clip_javascript_output(text: str, *, max_chars: int = 24000) -> str:
    """按既有工具上限截断 JavaScript 显式输出。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


if __name__ == '__main__':
    pass
