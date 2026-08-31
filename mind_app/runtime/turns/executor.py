# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
import asyncio
from collections.abc import Mapping
from observability import (
    observe,
    observe_exception
)
from protocol.transport.events import EventReport
from mind_app.history.contracts import TranscriptSink
from agent.application import (
    HookExecutionContext,
    TurnExecution,
)
from agent.application.turns.context import TurnContext
from agent.ports import (
    TurnOperation,
    TurnResultValue,
)
from protocol.client.reports import (
    EventReportLifetime,
    TurnEventReportHandle,
)
from agent.domain.tool_policy import (
    ToolFilterMode,
    filter_mode_tools
)
from mind_app.runtime.hooks.scope import HookExecutionScope

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from agent.ports import McpSessionPort


class _UnspecifiedToolFilterMode(object):
    """标记调用方未固定单轮工具过滤模式。"""

    __slots__ = ()


_UNSPECIFIED_TOOL_FILTER_MODE: typing.Final[_UnspecifiedToolFilterMode] = (
    _UnspecifiedToolFilterMode()
)


def build_turn_input_payload(
    message: str,
    *,
    attachments: typing.Iterable[typing.Mapping[str, typing.Any]] = (),
    extras: typing.Mapping[str, typing.Any] | None = None
) -> dict[str, typing.Any]:
    """构建轮次记录使用的用户输入载荷。"""
    payload: dict[str, typing.Any] = {"content": str(message)}

    attachment_items = [
        dict(item)
        for item in attachments
        if isinstance(item, Mapping)
    ]
    if attachment_items:
        payload["attachments"] = attachment_items
    if isinstance(extras, Mapping) and extras:
        payload["extras"] = dict(extras)

    return payload


def record_turn_started(
    transcript: TranscriptSink,
    execution: "TurnExecution",
) -> None:
    """写入会话边界、轮次边界和用户输入。"""
    context = execution.context

    if context.session_started:
        session_payload: dict[str, typing.Any] = {
            "cwd": context.cwd,
            "source": context.source,
            "reason": context.session_start_reason,
            "model": context.model,
        }
        if context.agent.depth > 0:
            session_payload.update({
                "parent_session_id": context.agent.root_session_id,
                "agent_id": context.agent.agent_id,
                "agent_type": context.agent.agent_type,
                "task_name": context.agent.task_name,
                "task_path": context.agent.task_path,
            })
        transcript.append(
            "session.started",
            actor="system",
            payload=session_payload,
        )

    transcript.append("turn.started", actor="system")
    transcript.append(
        "message.created",
        actor="user",
        payload=dict(execution.input_payload),
    )


def record_turn_finished(
    transcript: TranscriptSink,
    *,
    status: str,
    usage: typing.Mapping[str, typing.Any] | None = None,
    error: str | None = None,
    terminal_meta: typing.Mapping[str, typing.Any] | None = None
) -> None:
    """写入轮次的稳定终态。"""
    normalized_status = str(status or "failed").strip() or "failed"

    event = (
        "turn.interrupted"
        if normalized_status == "interrupted"
        else "turn.completed"
        if normalized_status == "completed"
        else "turn.incomplete"
        if normalized_status == "incomplete"
        else "turn.reconciliation_required"
        if normalized_status == "reconciliation_required"
        else "turn.failed"
    )

    payload: dict[str, typing.Any] = {
        "status": normalized_status,
        "usage": copy.deepcopy(dict(usage or {})),
    }

    payload.update(copy.deepcopy(dict(terminal_meta or {})))

    if error:
        payload["error"] = str(error)

    transcript.append(event, actor="system", payload=payload)


def resolve_turn_hook_scope(
    controller: "Mind",
    context: TurnContext
) -> HookExecutionScope:
    """解析并固定模型轮次使用的 Hook 作用域。"""
    hook_context = HookExecutionContext.from_turn(context)

    try:
        return controller.hook_scope(hook_context)
    except (OSError, TypeError, ValueError) as error:
        observe_exception(
            "hooks.resolve.failed",
            error,
            level="WARNING",
        )
        return HookExecutionScope.empty(hook_context)


def turn_continuation_count(execution: TurnExecution) -> int:
    """读取模型执行的续跑次数。"""
    value = execution.metadata.get("continuation_count")
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def _resolve_tool_filter_mode(
    controller: object,
    selected: ToolFilterMode | None | _UnspecifiedToolFilterMode,
) -> ToolFilterMode | None:
    """解析并校验单轮工具过滤模式。"""
    if not isinstance(selected, _UnspecifiedToolFilterMode):
        return selected

    profile_for_turn = getattr(controller, "tool_profile_for_turn", None)
    if not callable(profile_for_turn):
        return None

    profile_mode = profile_for_turn()
    if profile_mode is None:
        return None
    if profile_mode == "app":
        return "app"
    if profile_mode == "api":
        return "api"
    raise ValueError(f"Invalid tool filter mode: {profile_mode}")


async def execute_turn(
    mind: "Mind",
    pref_config: dict[str, typing.Any],
    execution: TurnExecution,
    operation: TurnOperation[TurnResultValue],
    *,
    event_report: EventReport | None = None,
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
            EventReportLifetime.SESSION
            if context.agent.depth == 0
            else EventReportLifetime.TURN
        )
        report_handle = await mind.event_reporting.acquire(
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
        selected_mode = _resolve_tool_filter_mode(mind, tool_filter_mode)
        visible_tools = filter_mode_tools(selected_mode, tools)

        operation_started.set()

        return await operation(execution, session, visible_tools, report)

    interrupted: bool = False

    try:
        result = await mind.with_mcp_session(pref_config, run_with_session)
    except asyncio.CancelledError:
        interrupted = True
        if not operation_started.is_set():
            _record_session_setup_failure(
                mind,
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
                mind,
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
            await mind.await_cleanup(
                report_handle.release(interrupted=interrupted)
            )


def _record_session_setup_failure(
    controller: "Mind",
    execution: TurnExecution,
    *,
    status: str,
    error: str | None = None
) -> None:
    """记录工具会话建立完成前结束的模型轮次。"""
    context = execution.context

    try:
        transcript = controller.transcripts.writer(
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
