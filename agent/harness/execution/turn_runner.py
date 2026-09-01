# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing

from observability import (
    observe,
    observe_exception
)
from agent.application.turns.execution import TurnExecution
from agent.application.turns.transcript import (
    record_turn_finished,
    record_turn_started,
)
from agent.ports import (
    EventReportPort,
    TurnOperation,
    TurnResultValue,
    TurnEventReportHandle,
    TurnExecutionRuntimePort,
)
from agent.domain.tool_policy import (
    ToolFilterMode,
    filter_mode_tools
)

if typing.TYPE_CHECKING:
    from agent.ports import McpSessionPort


class _UnspecifiedToolFilterMode(object):
    """标记调用方未固定单轮工具过滤模式。"""

    __slots__ = ()


_UNSPECIFIED_TOOL_FILTER_MODE: typing.Final[_UnspecifiedToolFilterMode] = (
    _UnspecifiedToolFilterMode()
)


def turn_continuation_count(execution: TurnExecution) -> int:
    """读取模型执行的续跑次数。"""
    value = execution.metadata.get("continuation_count")
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def _resolve_tool_filter_mode(
    runtime: TurnExecutionRuntimePort,
    selected: ToolFilterMode | None | _UnspecifiedToolFilterMode,
) -> ToolFilterMode | None:
    """解析并校验单轮工具过滤模式。"""
    if not isinstance(selected, _UnspecifiedToolFilterMode):
        return selected

    profile_mode = runtime.tool_profile_for_turn()
    if profile_mode is None:
        return None
    if profile_mode == "app":
        return "app"
    if profile_mode == "api":
        return "api"
    raise ValueError(f"Invalid tool filter mode: {profile_mode}")


async def execute_turn(
    runtime: TurnExecutionRuntimePort,
    pref_config: dict[str, typing.Any],
    execution: TurnExecution,
    operation: TurnOperation[TurnResultValue],
    *,
    event_report: EventReportPort | None = None,
    tool_filter_mode: (
        ToolFilterMode | None | _UnspecifiedToolFilterMode
    ) = _UNSPECIFIED_TOOL_FILTER_MODE,
) -> TurnResultValue:
    """在独立工具和报告生命周期中执行显式模型轮次。"""
    context    = execution.context
    started_at = time.perf_counter()

    observe(
        "call.start",
        cid=context.cid,
        sid=context.sid,
        turn_id=context.turn_id,
        agent_id=context.agent.agent_id,
        message_chars=len(execution.message),
        sandbox_mode=context.permissions.sandbox_mode,
        approval_policy=context.permissions.approval_policy,
    )

    report = event_report

    report_handle: TurnEventReportHandle | None = None

    if report is None:
        lifetime = (
            "session"
            if context.agent.depth == 0
            else "turn"
        )
        report_handle = await runtime.event_reporting.acquire(
            context.cid,
            context.sid,
            lifetime=lifetime,
        )
        report = report_handle.report
    assert report is not None

    operation_started = asyncio.Event()

    async def run_with_session(
        session: "McpSessionPort",
        tools: list[dict[str, typing.Any]]
    ) -> TurnResultValue:
        """在已建立的工具会话中执行模型轮次。"""
        selected_mode = _resolve_tool_filter_mode(runtime, tool_filter_mode)
        visible_tools = filter_mode_tools(selected_mode, tools)

        operation_started.set()

        return await operation(execution, session, visible_tools, report)

    interrupted: bool = False

    try:
        result = await runtime.with_mcp_session(pref_config, run_with_session)
    except asyncio.CancelledError:
        interrupted = True
        if not operation_started.is_set():
            _record_session_setup_failure(
                execution,
                status="interrupted",
            )
        observe(
            "call.interrupted",
            level="WARNING",
            cid=context.cid,
            sid=context.sid,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
        if not operation_started.is_set():
            _record_session_setup_failure(
                execution,
                status="interrupted" if interrupted else "failed",
                error=None if interrupted else _bounded_error(error),
            )
        observe_exception(
            "call.failed",
            error,
            cid=context.cid,
            sid=context.sid,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "call.complete",
            cid=context.cid,
            sid=context.sid,
            outcome=result.status,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return result
    finally:
        if report_handle is not None:
            await runtime.await_cleanup(
                report_handle.release(interrupted=interrupted)
            )


class TurnRunner:
    """绑定模型会话运行时并执行根或子 Agent Turn。"""

    def __init__(self, runtime: TurnExecutionRuntimePort) -> None:
        """绑定报告、MCP 会话和异步清理运行时。"""
        self._runtime = runtime

    async def __call__(
        self,
        pref_config: dict[str, typing.Any],
        execution: TurnExecution,
        operation: TurnOperation[TurnResultValue],
        *,
        event_report: EventReportPort | None = None,
    ) -> TurnResultValue:
        """在绑定运行时中执行一次显式模型轮次。"""
        return await execute_turn(
            self._runtime,
            pref_config,
            execution,
            operation,
            event_report=event_report,
        )


def _record_session_setup_failure(
    execution: TurnExecution,
    *,
    status: str,
    error: str | None = None
) -> None:
    """记录工具会话建立完成前结束的模型轮次。"""
    context = execution.context

    try:
        transcript_factory = context.transcript_factory
        if not callable(transcript_factory):
            raise RuntimeError("transcript factory is required")
        transcript = transcript_factory(
            context.transcript_path,
            session_id=context.sid,
            turn_id=context.turn_id,
        )
        transcript.open()
        try:
            record_turn_started(transcript, execution)
            record_turn_finished(
                transcript,
                status=status,
                error=error,
            )
        finally:
            transcript.close()
    except Exception as transcript_error:
        observe_exception(
            "transcript.session_setup_failure.failed",
            transcript_error,
            level="WARNING",
            turn_id=context.turn_id,
        )


def _bounded_error(error: BaseException, limit: int = 2000) -> str:
    """返回适合会话记录的有界异常摘要。"""
    message = str(error).strip()
    text = (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
    )
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
