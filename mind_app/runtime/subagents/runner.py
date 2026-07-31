# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import replace
from engine.observability import (
    observe,
    observe_exception
)
from mind_nova.events import EventReport
from mind_nova.identifiers import short_uid
from mind_app.runtime.hooks.models import SubagentStopDecision
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.hooks.subagent import SubagentHookEvents
from mind_app.runtime.turns.executor import (
    TurnExecution,
    TurnResultValue,
    execute_turn
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike

SubagentOutcome = typing.Literal[
    "completed",
    "failed",
    "incomplete",
    "interrupted",
]

MAX_SUBAGENT_STOP_CONTINUATIONS = 3


class SubagentOperation(typing.Protocol[TurnResultValue]):
    """定义使用固定 Hook 作用域执行子轮次的操作。"""

    async def __call__(
        self,
        execution: TurnExecution,
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
        event_report: EventReport
    ) -> TurnResultValue:
        """执行子轮次并返回稳定结果。"""
        ...


class SubagentRunner:
    """执行一个已经完成身份和会话分配的本地子轮次。"""

    def __init__(self, controller: "Mind") -> None:
        self._controller = controller

    async def run(
        self,
        pref_config: dict[str, typing.Any],
        execution: TurnExecution,
        operation: SubagentOperation[TurnResultValue],
        *,
        event_report: EventReport | None = None
    ) -> TurnResultValue:
        """执行子轮次并应用开始上下文与停止继续决定。"""
        if execution.context.agent.depth == 0:
            raise ValueError("subagent execution requires a child agent context")
        if not execution.message.strip():
            raise ValueError("subagent task is required")

        async def run_child_turn(
            turn_execution: TurnExecution,
            session: "McpSessionLike",
            tools: list[dict[str, typing.Any]],
            report: EventReport
        ) -> TurnResultValue:
            """执行使用固定轮次上下文的子轮次操作。"""
            return await operation(
                turn_execution,
                session,
                tools,
                report,
            )

        current_execution = execution
        if execution.context.session_started:
            start_result = await SubagentHookEvents(
                execution.hook_scope
            ).start(execution.message)
            if start_result.additional_context:
                current_execution = replace(
                    execution,
                    additional_context=(
                        *execution.additional_context,
                        *start_result.additional_context,
                    ),
                )

        continuation_count: int = 0

        while True:
            hook_events = SubagentHookEvents(current_execution.hook_scope)

            try:
                result = await execute_turn(
                    self._controller,
                    pref_config,
                    current_execution,
                    run_child_turn,
                    event_report=event_report,
                )
            except asyncio.CancelledError:
                await self._dispatch_stop(
                    hook_events,
                    current_execution,
                    outcome="interrupted",
                    error="subagent execution cancelled",
                    continuation_count=continuation_count,
                    cleanup=True,
                )
                raise
            except BaseException as error:
                await self._dispatch_stop(
                    hook_events,
                    current_execution,
                    outcome="failed",
                    error=_bounded_error(error),
                    continuation_count=continuation_count,
                    cleanup=True,
                )
                raise

            decision = await self._dispatch_stop(
                hook_events,
                current_execution,
                outcome=_result_outcome(result),
                error=_result_error(result),
                usage=_result_usage(result),
                last_assistant_message=_result_assistant_text(result),
                continuation_count=continuation_count,
            )
            if not decision.should_continue:
                return result

            if continuation_count >= MAX_SUBAGENT_STOP_CONTINUATIONS:
                observe(
                    "hooks.subagent_stop.limit_reached",
                    level="WARNING",
                    agent_id=current_execution.context.agent.agent_id,
                    agent_type=current_execution.context.agent.agent_type,
                    continuation_count=continuation_count,
                    hook_keys=list(decision.hook_keys),
                )
                return result

            continuation_count += 1

            current_execution = _continuation_execution(
                current_execution,
                decision.reason,
                continuation_count,
            )

    async def _dispatch_stop(
        self,
        hook_events: SubagentHookEvents,
        execution: TurnExecution,
        *,
        outcome: SubagentOutcome,
        error: str = "",
        usage: dict[str, typing.Any] | None = None,
        last_assistant_message: str = "",
        continuation_count: int,
        cleanup: bool = False
    ) -> SubagentStopDecision:
        """分发停止事件，并避免 Hook 故障替换模型结果。"""
        async def dispatch_stop() -> SubagentStopDecision:
            """执行一次需要返回聚合决定的停止事件。"""
            return await hook_events.stop(
                outcome=outcome,
                error=error,
                usage=usage,
                last_assistant_message=last_assistant_message,
                continuation_count=continuation_count,
            )

        async def cleanup_stop() -> None:
            """执行停止事件并显式丢弃清理阶段不需要的决定。"""
            await dispatch_stop()

        try:
            if cleanup:
                await self._controller.await_cleanup(cleanup_stop())
                return SubagentStopDecision.stop()
            return await dispatch_stop()
        except Exception as hook_error:
            observe_exception(
                "hooks.subagent_stop.failed",
                hook_error,
                level="WARNING",
                agent_id=execution.context.agent.agent_id,
                agent_type=execution.context.agent.agent_type,
            )
            return SubagentStopDecision.stop()


def _result_outcome(result: typing.Any) -> SubagentOutcome:
    """把模型结果状态规范为子执行主体结束状态。"""
    status = str(getattr(result, "status", "") or "").strip().lower()
    if status in {"completed", "failed", "incomplete"}:
        return typing.cast(SubagentOutcome, status)
    return "failed"


def _result_error(result: typing.Any) -> str:
    """返回模型结果中的有界错误摘要。"""
    value = getattr(result, "error", "")
    return _bounded_text(str(value or ""))


def _result_usage(result: typing.Any) -> dict[str, typing.Any]:
    """复制模型结果中的用量数据。"""
    value = getattr(result, "usage", None)
    return dict(value) if isinstance(value, dict) else {}


def _result_assistant_text(result: typing.Any) -> str:
    """返回模型结果中的有界最后回复。"""
    value = getattr(result, "assistant_text", "")
    return _bounded_text(str(value or ""))


def _continuation_execution(
    execution: TurnExecution,
    message: str,
    continuation_count: int
) -> TurnExecution:
    """创建同一子执行线程中的停止 Hook 续跑轮次。"""
    context = replace(
        execution.context,
        turn_id=short_uid(12),
        session_started=False,
        session_start_reason="",
    )

    metadata = dict(execution.metadata)

    metadata.update({
        "continuation_of_turn_id": (
            metadata.get("continuation_of_turn_id")
            or execution.context.turn_id
        ),
        "continuation_count": continuation_count,
    })

    return TurnExecution(
        context=context,
        message=message,
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=execution.hook_scope.dispatcher,
        ),
        metadata=metadata,
    )


def _bounded_error(error: BaseException) -> str:
    """返回包含异常类型的有界错误摘要。"""
    detail = str(error).strip()
    text   = f"{type(error).__name__}: {detail}" if detail else type(error).__name__
    return _bounded_text(text)


def _bounded_text(value: str, limit: int = 2000) -> str:
    """返回适合生命周期载荷的有界文本。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
