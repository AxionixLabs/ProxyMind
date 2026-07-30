# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from engine.observability import observe_exception
from mind_nova.events import EventReport
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


class SubagentOperation(typing.Protocol[TurnResultValue]):
    """定义使用固定 Hook 作用域执行子轮次的操作。"""

    async def __call__(
        self,
        execution: TurnExecution,
        hook_scope: HookExecutionScope,
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
        """执行子轮次并完整分发开始和停止通知。"""
        if execution.context.agent.depth == 0:
            raise ValueError("subagent execution requires a child agent context")

        hook_context = HookExecutionContext.from_turn(execution.context)

        try:
            hook_scope = self._controller.hook_scope(hook_context)
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "hooks.resolve.failed",
                error,
                level="WARNING",
            )
            hook_scope = HookExecutionScope.empty(hook_context)

        hook_events = SubagentHookEvents(hook_scope)

        outcome: SubagentOutcome = "failed"

        error_text: str              = ""
        usage: dict[str, typing.Any] = {}
        attempted: bool              = False

        async def run_with_scope(
            prepared: TurnExecution,
            session: "McpSessionLike",
            tools: list[dict[str, typing.Any]],
            report: EventReport
        ) -> TurnResultValue:
            """把固定 Hook 作用域交给具体子轮次操作。"""
            return await operation(
                prepared,
                hook_scope,
                session,
                tools,
                report,
            )

        try:
            attempted = True

            await hook_events.start(execution.message)

            result = await execute_turn(
                self._controller,
                pref_config,
                execution,
                run_with_scope,
                event_report=event_report,
            )
        except asyncio.CancelledError:
            outcome    = "interrupted"
            error_text = "subagent execution cancelled"
            raise

        except BaseException as error:
            outcome    = "failed"
            error_text = _bounded_error(error)
            raise

        else:
            outcome    = _result_outcome(result)
            error_text = _result_error(result)
            usage      = _result_usage(result)
            return result

        finally:
            if attempted:
                try:
                    await self._controller.await_cleanup(hook_events.stop(
                        outcome=outcome,
                        error=error_text,
                        usage=usage,
                    ))
                except Exception as error:
                    observe_exception(
                        "hooks.subagent_stop.failed",
                        error,
                        level="WARNING",
                        agent_id=execution.context.agent.agent_id,
                        agent_type=execution.context.agent.agent_type,
                    )


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
