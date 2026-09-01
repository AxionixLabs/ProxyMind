# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import types as mcp_types
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.authorization import (
    ExecutionAuthorizationError,
    ToolTurnInterrupted,
    reject_model_execution,
)
from agent.application.tools.results import (
    LocalToolResult,
)
from agent.application.tools.execution_results import client_execution_result
from agent.application.tools.patching import patch_tools
from protocol.schema.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS
from agent.application.approvals.amendments import approval_execpolicy_amendment
from agent.domain.permission_profiles import normalize_permission_profile
from mind_app.native_coding import NativeCoding
from infrastructure.config.execution_policy_manager import (
    ExecPolicyManager,
    validate_sandbox_permission_arguments
)
from infrastructure.mcp.tool_results import normalize_call_tool_result
from agent.application.tools.coding_schemas import (
    JS_REPL_INPUT_SCHEMA,
    JS_REPL_RESET_INPUT_SCHEMA,
    exec_command_input_schema,
    shell_command_input_schema,
    WRITE_STDIN_INPUT_SCHEMA
)

if typing.TYPE_CHECKING:
    from agent.application.approvals.coordinator import ApprovalCoordinator

NESTED_PROCESS_TOOLS = {
    "shell_command",
    "exec_command",
    "write_stdin"
}

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


def build_coding_result(
    *,
    tool: str,
    args: dict[str, typing.Any],
    raw: dict[str, typing.Any],
    target: str
) -> LocalToolResult:
    """构造编码工具调用结果。"""
    return client_execution_result(
        tool=tool,
        arguments=args,
        result=raw,
        target=target,
    )


def authorization_failure_result(
    coding: NativeCoding,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    error: ExecutionAuthorizationError
) -> LocalToolResult:
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
    arguments: dict[str, typing.Any]
) -> LocalToolResult:
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


def coding_tools(
    native_coding: NativeCoding,
    *,
    approval_coordinator: "ApprovalCoordinator | None" = None,
    exec_policy_manager: ExecPolicyManager | None = None,
    exec_permission_approvals_enabled: bool = False,
) -> list[ClientTool]:
    """返回编码工具列表。"""
    coding = native_coding
    local_exec_policy = exec_policy_manager or ExecPolicyManager(
        workspace_root=coding.root
    )

    def validate_inline_permission_feature(
        arguments: dict[str, typing.Any],
    ) -> None:
        """校验 inline 权限能力是否对当前工具开放。"""
        if (
            not exec_permission_approvals_enabled
            and str(arguments.get("sandbox_permissions") or "")
                .strip()
                .casefold() == "with_additional_permissions"
        ):
            raise ExecutionAuthorizationError(
                "additional_permissions_disabled",
                "inline additional permissions are disabled",
            )

    async def js_repl_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """执行一个持久 JavaScript 单元。"""
        try:
            reject_model_execution(arguments)
            args = _js_repl_arguments(arguments)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding,
                tool="js_repl",
                arguments=arguments,
                error=exc,
            )

        async def call_nested_tool(
            tool_name: str,
            tool_arguments: dict[str, typing.Any],
            call_id: str
        ) -> dict[str, typing.Any]:
            """通过当前复合工具会话执行内核请求。"""
            await _authorize_nested_tool(
                runtime,
                tool=tool_name,
                arguments=tool_arguments,
                approval_coordinator=approval_coordinator,
                exec_policy_manager=local_exec_policy,
                call_id=call_id,
            )

            if runtime.nested_tool_dispatch is not None:
                result = await runtime.nested_tool_dispatch(
                    tool_name,
                    tool_arguments,
                    call_id,
                )
            else:
                result = await runtime.session.call_tool(
                    tool_name,
                    tool_arguments,
                    read_timeout_seconds=runtime.read_timeout_seconds,
                    progress_callback=runtime.progress_callback,
                    call_id=call_id,
                    turn_context=runtime.turn_context,
                    pref_config=runtime.pref_config,
                )
            return _nested_tool_response(
                result,
                call_id=call_id,
                mcp_result=_nested_tool_returns_mcp(runtime.session, tool_name),
            )

        turn = runtime.turn_context

        raw = await coding.js_repl(
            session_id=turn.sid,
            code=str(args["code"]),
            cwd=turn.cwd,
            access_mode=turn.permissions.sandbox_mode,
            timeout_ms=int(args["timeout_ms"]),
            call_tool=call_nested_tool,
        )

        return build_coding_result(
            tool="js_repl",
            args=args,
            raw=raw,
            target=coding.agent_id,
        )

    async def js_repl_reset_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """重置当前会话的 JavaScript 内核。"""
        try:
            reject_model_execution(arguments)
            args = _js_repl_reset_arguments(arguments)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding,
                tool="js_repl_reset",
                arguments=arguments,
                error=exc,
            )

        raw = await coding.reset_js_repl(runtime.turn_context.sid)

        return build_coding_result(
            tool="js_repl_reset",
            args=args,
            raw=raw,
            target=coding.agent_id,
        )

    async def shell_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """执行单条命令。"""
        try:
            reject_model_execution(arguments)
            args = dict(arguments)
            validate_inline_permission_feature(args)
            validate_sandbox_permission_arguments(args)
            execution_args = dict(args)
            execution_args.pop("justification", None)
            for field_name in (
                "environment_id",
                "policy_fingerprint",
                "patch_scope",
                "tty",
            ):
                execution_args.pop(field_name, None)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="shell_command", arguments=arguments, error=exc
            )
        except ValueError as exc:
            return authorization_failure_result(
                coding,
                tool="shell_command",
                arguments=arguments,
                error=ExecutionAuthorizationError(
                    "sandbox_permissions_invalid",
                    str(exc),
                ),
            )

        raw = await coding.shell_command(
            **execution_args,
            sandbox_mode=runtime.turn_context.permissions.sandbox_mode,
        )

        return build_coding_result(
            tool="shell_command",
            args=execution_args,
            raw=raw,
            target=coding.agent_id
        )

    async def exec_command_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """启动可持续命令会话。"""
        try:
            reject_model_execution(arguments)
            args = dict(arguments)
            validate_inline_permission_feature(args)
            validate_sandbox_permission_arguments(args)
            execution_args = dict(args)
            execution_args.pop("justification", None)
            for field_name in (
                "environment_id",
                "policy_fingerprint",
                "patch_scope",
                "tty",
            ):
                execution_args.pop(field_name, None)
        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="exec_command", arguments=arguments, error=exc
            )
        except ValueError as exc:
            return authorization_failure_result(
                coding,
                tool="exec_command",
                arguments=arguments,
                error=ExecutionAuthorizationError(
                    "sandbox_permissions_invalid",
                    str(exc),
                ),
            )

        raw = await coding.exec_command(
            **execution_args,
            cid=runtime.turn_context.cid,
            sid=runtime.turn_context.sid,
            sandbox_mode=runtime.turn_context.permissions.sandbox_mode,
        )

        return build_coding_result(
            tool="exec_command",
            args=execution_args,
            raw=raw,
            target=coding.agent_id
        )

    async def write_stdin_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """写入或轮询命令会话。"""
        try:
            reject_model_execution(arguments)

            args = dict(arguments)

        except ExecutionAuthorizationError as exc:
            return authorization_failure_result(
                coding, tool="write_stdin", arguments=arguments, error=exc
            )

        raw = await coding.write_stdin(
            **args,
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

    tools: list[ClientTool] = [
        ClientTool(
            name="js_repl",
            description=JS_REPL_DESCRIPTION,
            input_schema=JS_REPL_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=js_repl_handler,
        ),
        ClientTool(
            name="js_repl_reset",
            description=(
                "重置当前对话会话的持久 JavaScript 内核。下次调用 js_repl 时会按需启动"
                "新的 Node.js 进程，之前定义的变量和对象将不可用。"
            ),
            input_schema=JS_REPL_RESET_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "shell"},
            handler=js_repl_reset_handler,
        ),
        ClientTool(
            name="shell_command",
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
            name="exec_command",
            description=(
                "启动可持续读写的本地 shell 命令会话。适合长耗时任务、交互式任务和持续输出。"
                "该实现使用标准输入输出管道，不提供真实 PTY。"
            ),
            input_schema=exec_command_input_schema(
                exec_permission_approvals_enabled=exec_permission_approvals_enabled,
            ),
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
    ]
    return [*tools, *patch_tools(coding)]


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


def _js_repl_reset_arguments(arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """校验 JavaScript 内核重置参数。"""
    if arguments:
        raise ExecutionAuthorizationError(
            "canonical_contract_invalid",
            f"js_repl_reset arguments contain unsupported fields: {sorted(arguments)}",
        )

    return {}


def _nested_canonical_arguments(
    tool: str,
    arguments: dict[str, typing.Any]
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
            canonical["additional_permissions"] = arguments["additional_permissions"]
        return canonical
    if tool == "exec_command":
        canonical = {
            "command": str(arguments.get("command") or ""),
            "cwd": str(arguments.get("cwd") or "."),
            "shell": str(arguments.get("shell") or "") or None,
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
            canonical["additional_permissions"] = arguments["additional_permissions"]
        return canonical
    if tool == "write_stdin":
        return {
            "session_id": str(arguments.get("session_id") or ""),
            "stdin": str(arguments.get("stdin") or ""),
            "wait_ms": int(arguments.get("wait_ms", 1000)),
            "max_output_chars": int(arguments.get("max_output_chars") or 12000),
            "control": str(arguments.get("control") or "none"),
        }
    return dict(arguments)


def _nested_tool_returns_mcp(session: typing.Any, tool_name: str) -> bool:
    """判断嵌套工具是否由 MCP 会话提供。"""
    registry = getattr(session, "client_registry", None)
    if registry is not None and registry.has_tool(tool_name):
        return False

    external_group = getattr(session, "external_group", None)
    external_tools = getattr(external_group, "tools", {})
    if tool_name in external_tools:
        return True

    return getattr(session, "service_session", None) is not None


def _nested_tool_response(
    result: mcp_types.CallToolResult,
    *,
    call_id: str,
    mcp_result: bool = False
) -> dict[str, typing.Any]:
    """把 MCP 工具结果转换为内核可消费的函数输出。"""
    normalized = normalize_call_tool_result(result)
    if not normalized.ok and not mcp_result:
        raise RuntimeError(normalized.display_text)

    if mcp_result:
        output = result.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        return {
            "type": "mcp_tool_call_output",
            "call_id": call_id,
            "output": output,
            # 旧 Kernel 的 emitImage MCP 分支读取 result，而协议对象使用 output。
            "result": (
                {"Ok": output}
                if normalized.ok
                else {"Err": normalized.display_text}
            ),
        }

    content_items: list[dict[str, typing.Any]] = []
    content_has_image = False
    for item in result.content:
        if isinstance(item, mcp_types.TextContent):
            if item.text:
                content_items.append({"type": "input_text", "text": item.text})
            continue
        if isinstance(item, mcp_types.ImageContent):
            content_has_image = True
            detail = None
            meta = item.meta if isinstance(item.meta, dict) else {}
            for key, value in meta.items():
                if str(key).endswith("/imageDetail") and value in {
                    "auto",
                    "low",
                    "high",
                    "original",
                }:
                    detail = value
                    break
            content_items.append({
                "type": "input_image",
                "image_url": f"data:{item.mimeType};base64,{item.data}",
                **({"detail": detail} if detail else {}),
            })

    if content_has_image:
        output: typing.Any = content_items
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }

    images = [
        item
        for item in normalized.fields.get("attachments", [])
        if isinstance(item, dict)
        and item.get("kind") == "image"
        and str(item.get("data_url") or "").lower().startswith("data:")
    ]
    if images:
        output: typing.Any = [
            {
                "type": "input_image",
                "image_url": str(item["data_url"]),
                **(
                    {"detail": item["detail"]}
                    if item.get("detail") in {"auto", "low", "high", "original"}
                    else {}
                ),
            }
            for item in images
        ]
    else:
        data = normalized.data
        output = (
            data
            if data not in (None, {}, [], "")
            else str(normalized.fields.get("text") or normalized.display_text or "")
        )

    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": output,
    }


async def _authorize_nested_tool(
    runtime: ToolHandlerContext,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    approval_coordinator: "ApprovalCoordinator | None",
    exec_policy_manager: ExecPolicyManager,
    call_id: str
) -> None:
    """按本地规则审批 JavaScript 发起的嵌套进程调用。"""
    if tool not in NESTED_PROCESS_TOOLS:
        return None

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

    requirement = exec_policy_manager.create_exec_approval_requirement_for_command(
        str(arguments.get("command") or ""),
        approval_policy=permissions.approval_policy,
        sandbox_mode=permissions.sandbox_mode,
        cwd=arguments.get("cwd") or runtime.turn_context.cwd,
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
            requirement.reason or f"local execution policy forbids nested {tool} command",
        )

    if (
        sandbox_permissions == "with_additional_permissions"
        and additional_permissions
        and not _nested_permission_granted(
            runtime,
            arguments,
            permissions=additional_permissions,
            cwd=command_cwd,
        )
    ):
        if runtime.turn_context.permissions.approval_policy == "never":
            raise ExecutionAuthorizationError(
                "additional_permissions_approval_required",
                "additional permissions require approval, but approval policy is never",
            )
        requirement = ExecApprovalRequirement.needs_approval(
            reason="additional permissions require approval",
            proposed_execpolicy_amendment=requirement.proposed_execpolicy_amendment,
        )

    requires_approval = requirement.state == "needs_approval"

    approved  = not requires_approval
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

        approval = {
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

        decision = await approval_coordinator.request(approval)
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
            exec_policy_manager.add_approval_for_session(
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
                    exec_policy_manager.persist_execpolicy_amendment({
                        "command_prefix": list(proposal.command_prefix),
                    })
                except (OSError, UnicodeError, ValueError):
                    approved = False

    if not approved:
        raise ExecutionAuthorizationError(
            "nested_tool_approval_denied",
            f"nested {tool} call was not approved",
        )

    return None


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


if __name__ == '__main__':
    pass
