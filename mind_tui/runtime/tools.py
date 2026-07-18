# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from engine.enhance import exchange_arguments
from mind_app.approval import (
    ApprovalStore,
    approval_expired,
    approval_from_event,
    approval_id_from_event,
    validate_tool_approval
)
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.mcp import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.runtime.tools.execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from mind_app.runtime.tools.plan_steps import StepPlanExecutor
from mind_app.runtime.tools.run import (
    run_tool_step,
    server_tool_output_result
)
from mind_app.stream_events.tool_trace import coding_trace_tool
from mind_nova import request
from ..events import AppEvent
from .bridge import (
    EmitEvent,
    RuntimeDisplayBridge
)

if typing.TYPE_CHECKING:
    from mind_app.mind_core import Mind


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """保存一次工具执行的界面输出结果。"""

    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    text: str
    fields: typing.Any


class TuiToolRuntime:
    """处理审批、工具执行和服务端结果回填。"""

    def __init__(
        self,
        *,
        mind: "Mind",
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        pref_config: dict[str, typing.Any],
        emit: EmitEvent
    ) -> None:
        """初始化单轮工具运行时。"""
        self.mind        = mind
        self.session     = session
        self.tools       = tools
        self.pref_config = pref_config
        self.emit        = emit
        self.display     = RuntimeDisplayBridge(emit)
        self.approvals   = ApprovalStore()

    async def handle_approval(self, event: dict[str, typing.Any]) -> None:
        """显示审批请求并把决定回传给服务端。"""
        approval = approval_from_event(event)
        if approval_expired(approval):
            await self.emit(
                AppEvent(
                    "display.block",
                    text="• Approval expired",
                    payload={"kind": "approval"}
                )
            )
            return

        reply = asyncio.get_running_loop().create_future()

        await self.emit(
            AppEvent(
                "tool.approval_required",
                payload=approval,
                reply=reply
            )
        )

        decision    = await reply
        approved    = decision in {"accept", "acceptForSession"}
        approval_id = approval_id_from_event(event)

        self.approvals.mark_decision(
            call_id=str(event.get("call_id") or ""),
            approval=approval,
            decision=decision
        )

        try:
            await request.post_tool_approval(
                event["cid"],
                event["sid"],
                event["call_id"],
                approval_id,
                decision=decision,
                reason=None if approved else "user denied"
            )
        except request.ToolApprovalExpired:
            return

    async def execute_call(
        self,
        event: dict[str, typing.Any]
    ) -> ToolOutcome | None:
        """校验并执行一次客户端工具调用。"""
        name          = str(event.get("name") or event.get("tool") or "").strip()
        raw_arguments = event.get("arguments")
        arguments     = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        execution     = event.get("execution") if isinstance(event.get("execution"), dict) else None

        if not name:
            return await self._reject_call(event, name, arguments, "tool.call missing name/tool")

        event_meta = event.get("meta") if isinstance(event.get("meta"), dict) else None
        local_meta = meta_for_tool(self.tools, name)

        approval = validate_tool_approval(
            event=event,
            name=name,
            arguments=arguments,
            store=self.approvals,
            meta=event_meta,
            local_meta=local_meta
        )
        if approval.action == "wait":
            await self.display.begin_custom_tool_status("Waiting for approval")
            return None
        if approval.action == "reject":
            result = approval.result or {}
            await self._post_result(event, name, False, result, execution)
            return ToolOutcome(name, arguments, False, _result_text(result), result)

        if policy_result := validate_execution_policy(name=name, execution=execution):
            if is_execution_ignored(policy_result):
                return None
            await self._post_result(event, name, False, policy_result, execution)
            return ToolOutcome(
                name,
                arguments,
                False,
                _result_text(policy_result),
                policy_result
            )

        if name == PLAN_STEPS_TOOL:
            return await self._execute_plan(event, arguments, execution)

        try:
            exchanged = exchange_arguments(name, arguments, self.mind.report)
            if not isinstance(exchanged, dict):
                raise TypeError(f"invalid arguments for {name}")
            tool_run = await run_tool_step(
                self.session,
                stream_ui=self.display,
                tools=self.tools,
                name=name,
                arguments=exchanged,
                meta={**local_meta, **(event_meta or {})} or None,
                pref_config=self.pref_config,
                enable_progress_notify=True,
                stream_callback=lambda text: self.display.feed(
                    text,
                    display=self.display.BLOCK
                ),
                status_text="coding" if coding_trace_tool(name) else None,
                code_status=coding_trace_tool(name),
                execution=execution,
                cid=str(event.get("cid") or ""),
                sid=str(event.get("sid") or ""),
                call_id=str(event.get("call_id") or "")
            )
        except Exception as exc:
            return await self._reject_call(
                event,
                name,
                arguments,
                f"{type(exc).__name__}: {exc}"
            )

        await self._post_result(
            event,
            name,
            tool_run.ok,
            tool_run.fields,
            execution
        )
        return ToolOutcome(
            name,
            exchanged,
            tool_run.ok,
            tool_run.text,
            tool_run.fields
        )

    def server_output(self, event: dict[str, typing.Any]) -> ToolOutcome | None:
        """将服务端已执行工具的输出转换为界面结果。"""
        name = str(event.get("name") or event.get("tool") or "").strip()
        if not name:
            return None
        raw_arguments = event.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        result = server_tool_output_result(name, event)
        return ToolOutcome(name, arguments, result.ok, result.text, result.fields)

    async def _execute_plan(
        self,
        event: dict[str, typing.Any],
        arguments: dict[str, typing.Any],
        execution: dict[str, typing.Any] | None
    ) -> ToolOutcome:
        """执行步骤计划并回填汇总结果。"""
        executor = StepPlanExecutor(
            session=self.session,
            stream_ui=self.display,
            tools=self.tools,
            report=self.mind.report
        )
        report = await executor.execute_tool_call(
            arguments=arguments,
            cid=str(event.get("cid") or ""),
            sid=str(event.get("sid") or ""),
            call_id=str(event.get("call_id") or "")
        )
        await self._post_result(
            event,
            PLAN_STEPS_TOOL,
            report.ok,
            report.fields,
            execution
        )
        return ToolOutcome(
            PLAN_STEPS_TOOL,
            arguments,
            report.ok,
            report.text,
            report.fields
        )

    async def _reject_call(
        self,
        event: dict[str, typing.Any],
        name: str,
        arguments: dict[str, typing.Any],
        message: str
    ) -> ToolOutcome:
        """回填工具执行失败结果。"""
        result = {"ok": False, "error": message, "text": message}
        execution = event.get("execution") if isinstance(event.get("execution"), dict) else None
        await self._post_result(event, name, False, result, execution)
        return ToolOutcome(name or "tool", arguments, False, message, result)

    @staticmethod
    async def _post_result(
        event: dict[str, typing.Any],
        name: str,
        ok: bool,
        result: typing.Any,
        execution: dict[str, typing.Any] | None
    ) -> None:
        """把工具结果回填给服务端。"""
        await request.post_tool_result(
            event["cid"],
            event["sid"],
            event["call_id"],
            name,
            ok,
            result,
            execution=execution
        )


def _result_text(result: typing.Any) -> str:
    """从工具结果中提取界面可展示的文本。"""
    if isinstance(result, dict):
        return str(result.get("text") or result.get("error") or result)
    return str(result or "")


if __name__ == '__main__':
    pass
