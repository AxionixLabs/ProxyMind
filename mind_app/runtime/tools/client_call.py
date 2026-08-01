# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from mind_app.mcp.contracts import McpSessionLike
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.runtime.execution import ToolInvocation
from mind_app.runtime.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
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
    execution: dict[str, typing.Any] | None,
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

            tool_run = await run_tool_step(
                self.session,
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
            ),
            additional_context=visible.additional_context,
            system_message=visible.system_message,
        )

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
