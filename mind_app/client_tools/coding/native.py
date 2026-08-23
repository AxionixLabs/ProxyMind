# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mcp import types as mcp_types
from mind_nova.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS
from mind_app.native_coding import NativeCoding
from mind_app.mcp.tool_result import normalize_call_tool_result
from mind_app.native_coding.execution_authorization import (
    ExecutionAuthorizationError,
    authorized_arguments,
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
    JS_REPL_INPUT_SCHEMA,
    JS_REPL_RESET_INPUT_SCHEMA,
    SHELL_COMMAND_INPUT_SCHEMA,
    WRITE_STDIN_INPUT_SCHEMA
)

if typing.TYPE_CHECKING:
    from mind_app.approval.coordinator import ApprovalCoordinator

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
    "host.tool(\"shell_command\", {command: \"...\"})；Windows 打开网页示例为 await "
    "host.tool(\"shell_command\", {command: 'Start-Process \"https://example.com\"'})。"
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
    error: ExecutionAuthorizationError
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
    arguments: dict[str, typing.Any]
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


def trusted_arguments(
    runtime: ClientToolRuntime,
    arguments: dict[str, typing.Any],
    *,
    tool: str,
    require_grant: bool = True
) -> dict[str, typing.Any]:
    """校验调用参数、运行身份和可信执行授权。"""
    turn = runtime.turn_context
    validate_runtime_identity(cid=turn.cid, sid=turn.sid, call_id=runtime.call_id)
    validate_execution_authorization(runtime.execution, require_grant=require_grant)
    return authorized_arguments(runtime.execution, arguments, tool=tool)


def reject_model_execution(arguments: dict[str, typing.Any]) -> None:
    """拒绝从模型工具参数传入执行授权。"""
    if "execution" in arguments:
        raise ExecutionAuthorizationError(
            "model_execution_forbidden", "execution must come from the trusted tool event"
        )


def coding_tools(
    native_coding: NativeCoding | None = None,
    *,
    approval_coordinator: ApprovalCoordinator | None = None
) -> list[ClientTool]:
    """返回编码工具列表。"""
    coding = native_coding or NativeCoding()

    async def js_repl_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
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
            execution = await _nested_execution(
                runtime,
                tool=tool_name,
                arguments=tool_arguments,
                approval_coordinator=approval_coordinator,
                call_id=call_id,
            )

            if runtime.nested_tool_dispatch is not None:
                result = await runtime.nested_tool_dispatch(
                    tool_name,
                    tool_arguments,
                    call_id,
                    execution,
                )
            else:
                result = await runtime.session.call_tool(
                    tool_name,
                    tool_arguments,
                    read_timeout_seconds=runtime.read_timeout_seconds,
                    progress_callback=runtime.progress_callback,
                    execution=execution,
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
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
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
            args = trusted_arguments(
                runtime,
                arguments,
                tool="shell_command",
            )
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
            args = trusted_arguments(
                runtime,
                arguments,
                tool="exec_command",
            )
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

            args = trusted_arguments(
                runtime,
                arguments,
                tool="write_stdin",
            )

            mutates_process = bool(args["stdin"]) or args.get("control") != "none"

            if read_only_sandbox(runtime) and mutates_process:
                return sandbox_failure_result(
                    coding,
                    tool="write_stdin",
                    arguments=arguments,
                )
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
                "应用文本补丁修改工作区文件。调用参数是对象，必填字段为 patch，形状为 "
                "{\"patch\": \"补丁文本\"}。支持 *** Begin Patch、标准 unified diff 和 "
                "git diff，以及多文件、新建、更新、删除和上下文校验。"
            ),
            input_schema=APPLY_PATCH_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "coding", "class": "workspace"},
            handler=apply_patch_handler,
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
        return {
            "command": str(arguments.get("command") or ""),
            "cwd": str(arguments.get("cwd") or "."),
            "timeout_sec": int(arguments.get("timeout_sec") or 60),
            "output_encoding": str(arguments.get("output_encoding") or "auto"),
        }
    if tool == "exec_command":
        return {
            "command": str(arguments.get("command") or ""),
            "cwd": str(arguments.get("cwd") or "."),
            "yield_time_ms": int(arguments.get("yield_time_ms", 1000)),
            "max_output_chars": int(arguments.get("max_output_chars") or 24000),
            "timeout_sec": int(arguments.get("timeout_sec") or 1800),
            "idle_timeout_sec": int(arguments.get("idle_timeout_sec") or 300),
        }
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


async def _nested_execution(
    runtime: ClientToolRuntime,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    approval_coordinator: ApprovalCoordinator | None,
    call_id: str
) -> dict[str, typing.Any] | None:
    """为嵌套进程调用取得并构造可验证的执行元数据。"""
    if tool not in NESTED_PROCESS_TOOLS:
        return None

    permissions = runtime.turn_context.permissions

    requires_approval = (
        permissions.sandbox_mode == "workspace-write"
        or permissions.approval_policy == "untrusted"
    )

    approved  = not requires_approval
    canonical = _nested_canonical_arguments(tool, arguments)

    if requires_approval:
        if approval_coordinator is None or permissions.approval_policy == "never":
            return None

        agent = runtime.turn_context.agent

        approval = {
            "id": f"nested_{call_id}",
            "call_id": call_id,
            "tool": tool,
            "arguments": canonical,
            "command": str(canonical.get("command") or ""),
            "environment": "local",
            "justification": "JavaScript requested a nested local process tool.",
            "agent_id": agent.agent_id,
            "agent_type": agent.agent_type,
            "agent_depth": agent.depth,
        }

        decision = await approval_coordinator.request(approval)
        approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS

    if not approved:
        raise ExecutionAuthorizationError(
            "nested_tool_approval_denied",
            f"nested {tool} call was not approved",
        )

    return {
        "state": "approved" if requires_approval else "allowed",
        "target": "local",
        "policyVersion": "client-js-repl-v1",
        "expiresAt": time.time() + 60,
        "grantId": f"nested_{call_id}",
        "canonicalArguments": canonical,
    }


if __name__ == '__main__':
    pass
