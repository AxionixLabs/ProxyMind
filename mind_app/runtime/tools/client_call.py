# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
    replace
)
from mcp import types as mcp_types
from mind_app.client_tools.types import NESTED_TOOL_DISPATCH_META_KEY
from mind_app.mcp.contracts import McpSessionLike
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.stream_events.tool_trace import coding_trace_tool
from mind_app.stream_events.tool_policy import (
    is_two_stage_tool,
    tool_status_text
)
from .display import (
    show_tool_result,
    show_tool_start
)
from .run import (
    ToolRunResult,
    run_tool_step
)


@dataclass(slots=True)
class ClientToolCallResult:
    """描述一次客户端工具执行结果。"""
    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    text: str
    cost_ms: int = 0
    call_id: str = ""
    fields: dict[str, typing.Any] = field(default_factory=dict)
    hook_response: typing.Any = None
    response: mcp_types.CallToolResult | None = None


@dataclass(frozen=True, slots=True)
class ClientToolCallOutcome:
    """描述客户端工具执行和 Hook 反馈的组合结果。"""

    result: ClientToolCallResult
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

    def __post_init__(self) -> None:
        """规范化 Hook 反馈文本。"""
        if not isinstance(self.result, ClientToolCallResult):
            raise TypeError(
                "client tool execution must return ClientToolCallResult"
            )
        if not isinstance(self.additional_context, tuple) or any(
            not isinstance(value, str)
            for value in self.additional_context
        ):
            raise TypeError("additional context must be a tuple of strings")
        if not isinstance(self.system_message, str):
            raise TypeError("system message must be a string")

        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [value.strip()]
                if text
            ),
        )
        object.__setattr__(
            self,
            "system_message",
            self.system_message.strip(),
        )


def build_client_tool_post_kwargs(
    outcome: ClientToolCallOutcome,
    *,
    execution: dict[str, typing.Any] | None
) -> dict[str, typing.Any]:
    """构建客户端工具结果回传参数。"""
    post_kwargs: dict[str, typing.Any] = {
        "execution": execution,
    }

    if outcome.additional_context:
        post_kwargs["additional_context"] = outcome.additional_context

    if outcome.system_message:
        post_kwargs["system_message"] = outcome.system_message

    return post_kwargs


class ClientToolCallRunner:
    """执行单个客户端工具调用并生成本地展示。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        output_control: OutputControlPort,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        pref_config: dict[str, typing.Any],
        tool_call_coordinator: ToolCallCoordinator
    ) -> None:
        self.session               = session
        self.output_control        = output_control
        self.status_control        = status_control
        self.presentation          = presentation
        self.tools                 = tools
        self.pref_config           = pref_config
        self.tool_call_coordinator = tool_call_coordinator

    @staticmethod
    def _denied_result(
        invocation: ToolInvocation,
        reason: str
    ) -> ClientToolCallResult:
        """构建被前置 Hook 阻止的工具结果。"""
        text = str(reason or "tool use denied by hook")

        fields = {
            "ok": False,
            "text": text,
            "data": {
                "hook_denied": True,
                "error": text,
            },
        }

        return ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=False,
            text=text,
            call_id=invocation.call_id,
            fields=fields,
        )

    async def _execute_allowed_call(
        self,
        invocation: ToolInvocation,
        *,
        use_coding_trace: bool,
        display: bool,
    ) -> ClientToolCallResult:
        """执行已通过前置检查的客户端工具调用。"""
        name      = invocation.name
        arguments = dict(invocation.arguments)
        call_id   = invocation.call_id
        cost_ms   = 0

        response: mcp_types.CallToolResult | None = None

        if name == "js_repl":
            async def dispatch_nested_tool(
                tool_name: str,
                tool_arguments: dict[str, typing.Any],
                nested_call_id: str,
                execution: dict[str, typing.Any] | None,
            ) -> mcp_types.CallToolResult:
                return await self._execute_nested_tool(
                    invocation.turn,
                    tool_name=tool_name,
                    arguments=tool_arguments,
                    call_id=nested_call_id,
                    execution=execution,
                )

            invocation = replace(
                invocation,
                meta={
                    **(invocation.meta or {}),
                    NESTED_TOOL_DISPATCH_META_KEY: dispatch_nested_tool,
                },
            )

        try:
            if display:
                self.output_control.record_tool_arguments(
                    name,
                    arguments,
                    call_id=call_id,
                )
                if not use_coding_trace or is_two_stage_tool(name):
                    await show_tool_start(
                        self.presentation,
                        name,
                        arguments,
                        call_id=call_id,
                    )

            tool_run = await run_tool_step(
                self.session,
                status_control=self.status_control,
                presentation=self.presentation,
                tools=self.tools,
                invocation=invocation,
                pref_config=self.pref_config,
                enable_progress_notify=True,
                status_text=tool_status_text(name),
            )

            ok      = tool_run.ok
            fields  = tool_run.fields
            text    = tool_run.text
            cost_ms = tool_run.cost_ms

            raw_response = getattr(tool_run, "result", None)
            if isinstance(raw_response, mcp_types.CallToolResult):
                response = raw_response

            hook_response = getattr(tool_run, "hook_response", fields)

        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            ok   = False

            fields = {
                "ok": False,
                "text": text,
                "data": {"error": text},
            }

            hook_response = None

            tool_run = ToolRunResult(
                result=None,
                ok=False,
                fields=fields,
                text=text,
                data=fields["data"],
                hook_response=None,
                cost_ms=cost_ms,
                status="failed",
            )

        if display:
            if use_coding_trace:
                await self.status_control.end_status()
            await show_tool_result(
                self.presentation,
                name,
                arguments,
                tool_run,
                ok=ok,
                text=text,
                use_coding_trace=use_coding_trace,
                call_id=call_id,
            )

        return ClientToolCallResult(
            name=name,
            arguments=arguments,
            ok=ok,
            text=str(text or ""),
            cost_ms=cost_ms,
            call_id=call_id,
            fields=fields,
            hook_response=hook_response,
            response=response,
        )

    async def _execute_nested_tool(
        self,
        turn: TurnContext,
        *,
        tool_name: str,
        arguments: dict[str, typing.Any],
        call_id: str,
        execution: dict[str, typing.Any] | None
    ) -> mcp_types.CallToolResult:
        """通过普通工具生命周期执行内核发起的嵌套调用。"""
        outcome = await self.execute(
            ToolInvocation(
                turn=turn,
                call_id=call_id,
                name=tool_name,
                arguments=arguments,
                execution=execution,
            ),
            use_coding_trace=coding_trace_tool(tool_name),
            display=False,
        )
        result = outcome.result
        if result.response is None:
            raise RuntimeError(result.text or f"nested {tool_name} call failed")
        return result.response

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        use_coding_trace: bool,
        display: bool = True
    ) -> ClientToolCallOutcome:
        """执行经过 Hook 协调的客户端工具调用。"""

        async def operation(
            prepared: ToolInvocation
        ) -> ToolOperationResult[ClientToolCallResult]:
            """执行已获准的本地操作。"""
            result = await self._execute_allowed_call(
                prepared,
                use_coding_trace=use_coding_trace,
                display=display,
            )
            return ToolOperationResult(
                value=result,
                snapshot=ToolResultSnapshot(
                    ok=result.ok,
                    text=result.text,
                    fields=result.fields,
                ),
                hook_response=result.hook_response,
            )

        hook_run = await self.tool_call_coordinator.run_invocation(
            invocation,
            operation,
        )

        if not hook_run.allowed:
            return ClientToolCallOutcome(
                result=self._denied_result(invocation, hook_run.reason),
                additional_context=hook_run.additional_context,
            )
        if hook_run.value is None:
            raise RuntimeError("tool execution returned no result")
        if hook_run.visible_result is None:
            raise RuntimeError("tool execution returned no visible result")

        visible = hook_run.visible_result

        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=hook_run.value.name,
                arguments=dict(hook_run.value.arguments),
                ok=visible.ok,
                text=visible.text,
                cost_ms=hook_run.value.cost_ms,
                call_id=hook_run.value.call_id,
                fields=visible.fields,
                hook_response=hook_run.value.hook_response,
                response=hook_run.value.response,
            ),
            additional_context=visible.additional_context,
            system_message=visible.system_message,
        )


if __name__ == '__main__':
    pass
