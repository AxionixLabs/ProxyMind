# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from engine.enhance import exchange_arguments
from mind_app.mcp.contracts import McpSessionLike
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.runtime.execution import ToolInvocation
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from .display import (
    show_tool_result,
    show_tool_start
)
from .run import run_tool_step


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
        report: typing.Any,
        tool_call_coordinator: ToolCallCoordinator
    ) -> None:
        self.session               = session
        self.output_control        = output_control
        self.status_control        = status_control
        self.presentation          = presentation
        self.tools                 = tools
        self.pref_config           = pref_config
        self.report                = report
        self.tool_call_coordinator = tool_call_coordinator

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

        try:
            if display:
                self.output_control.record_tool_arguments(
                    name,
                    arguments,
                    call_id=call_id,
                )
                if not use_coding_trace:
                    await show_tool_start(
                        self.presentation,
                        name,
                        arguments,
                        call_id=call_id,
                    )

            arguments  = exchange_arguments(name, arguments, self.report)
            invocation = invocation.with_arguments(arguments)

            tool_run = await run_tool_step(
                self.session,
                output_control=self.output_control,
                status_control=self.status_control,
                presentation=self.presentation,
                tools=self.tools,
                invocation=invocation,
                pref_config=self.pref_config,
                enable_progress_notify=True,
                status_text=None,
            )

            ok      = tool_run.ok
            fields  = tool_run.fields
            text    = tool_run.text
            cost_ms = tool_run.cost_ms

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

        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            ok   = False

            fields = {
                "ok": False,
                "text": text,
                "data": {"error": text},
            }

        return ClientToolCallResult(
            name=name,
            arguments=arguments,
            ok=ok,
            text=str(text or ""),
            cost_ms=cost_ms,
            call_id=call_id,
            fields=fields,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        use_coding_trace: bool,
        display: bool = True
    ) -> ClientToolCallResult:
        """执行经过 Hook 协调的客户端工具调用。"""

        async def operation() -> ClientToolCallResult:
            """执行已获准的本地操作。"""
            return await self._execute_allowed_call(
                invocation,
                use_coding_trace=use_coding_trace,
                display=display,
            )

        hook_run = await self.tool_call_coordinator.run(invocation, operation)

        if not hook_run.allowed:
            return self._denied_result(invocation, hook_run.reason)
        if hook_run.value is None:
            raise RuntimeError("tool execution returned no result")

        return hook_run.value

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


if __name__ == '__main__':
    pass
