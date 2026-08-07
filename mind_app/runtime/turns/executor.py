# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
import asyncio
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
    replace
)
from types import MappingProxyType
from engine.observability import (
    observe,
    observe_exception
)
from mind_nova.events import EventReport
from mind_nova.identifiers import short_uid
from mind_app.history.contracts import TranscriptSink
from mind_app.runtime.execution import TurnContext
from mind_app.runtime.tools.mode_policy import (
    ToolFilterMode,
    filter_mode_tools
)
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike


@dataclass(frozen=True, slots=True)
class TurnExecution:
    """保存已经固定身份、会话和 Hook 作用域的模型执行。"""
    context: TurnContext
    message: str
    hook_scope: HookExecutionScope
    metadata: typing.Mapping[str, typing.Any] = field(default_factory=dict)
    additional_context: tuple[str, ...] = ()
    system_message: str = ""
    input_payload: typing.Mapping[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """固定执行元数据并校验会话标识一致。"""
        if not isinstance(self.context, TurnContext):
            raise TypeError("turn context is required")
        if not isinstance(self.message, str):
            raise TypeError("turn message must be a string")
        if not isinstance(self.hook_scope, HookExecutionScope):
            raise TypeError("turn hook scope is required")
        if not isinstance(self.additional_context, (tuple, list)):
            raise TypeError("turn additional context must be a sequence")
        if not isinstance(self.system_message, str):
            raise TypeError("turn system message must be a string")
        if not isinstance(self.input_payload, Mapping):
            raise TypeError("turn input payload must be a mapping")

        additional_context: list[str] = []
        for value in self.additional_context:
            if not isinstance(value, str):
                raise TypeError("turn additional context entries must be strings")
            normalized = value.strip()
            if normalized:
                additional_context.append(normalized)

        self.hook_scope.require_turn(self.context)

        metadata = dict(self.metadata)

        expected = {
            "cid": self.context.cid,
            "sid": self.context.sid,
        }

        for key, value in expected.items():
            provided = str(metadata.get(key) or "").strip()
            if provided and provided != value:
                raise ValueError(f"turn metadata {key} does not match context")
            metadata[key] = value

        object.__setattr__(self, "metadata", MappingProxyType(metadata))
        object.__setattr__(self, "additional_context", tuple(additional_context))
        object.__setattr__(self, "system_message", self.system_message.strip())
        object.__setattr__(
            self,
            "input_payload",
            MappingProxyType({
                **dict(self.input_payload),
                "content": self.message,
            }),
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


def create_continuation_execution(
    execution: TurnExecution,
    message: str,
    *,
    continuation_count: int | None = None,
    additional_context: typing.Iterable[str] = (),
    system_message: str = ""
) -> TurnExecution:
    """在当前会话中创建一次续跑模型执行。"""
    if continuation_count is None:
        next_count = turn_continuation_count(execution) + 1
    else:
        try:
            next_count = max(0, int(continuation_count))
        except (TypeError, ValueError):
            next_count = 0

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
        "continuation_count": next_count,
    })

    return TurnExecution(
        context=context,
        message=message,
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=execution.hook_scope.dispatcher,
        ),
        metadata=metadata,
        additional_context=tuple(additional_context),
        system_message=system_message,
    )


class TurnResult(typing.Protocol):
    """定义模型轮次执行器返回的最小结果契约。"""

    @property
    def status(self) -> str:
        """返回模型轮次的稳定结束状态。"""
        ...


TurnResultValue = typing.TypeVar(
    "TurnResultValue",
    bound=TurnResult,
    covariant=True,
)


class TurnOperation(typing.Protocol[TurnResultValue]):
    """定义在工具会话中执行单个模型轮次的操作。"""

    async def __call__(
        self,
        execution: TurnExecution,
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
        event_report: EventReport
    ) -> TurnResultValue:
        """执行模型轮次并返回稳定结果。"""
        ...


async def execute_turn(
    mind: "Mind",
    pref_config: dict[str, typing.Any],
    execution: TurnExecution,
    operation: TurnOperation[TurnResultValue],
    *,
    event_report: EventReport | None = None,
    tool_filter_mode: ToolFilterMode | None = None,
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

    report_pool       = None
    owns_event_report = False

    if report is None:
        if context.agent.depth == 0:
            report_pool = getattr(mind, "event_reports", None)
        if report_pool is not None:
            report = await report_pool.acquire(
                context.cid,
                context.sid,
            )
        else:
            report = EventReport(
                context.cid,
                context.sid,
            )
            owns_event_report = True
            await report.open()
    assert report is not None

    operation_started = asyncio.Event()

    async def run_with_session(
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]]
    ) -> TurnResultValue:
        """在已建立的工具会话中执行模型轮次。"""
        selected_mode = tool_filter_mode
        if selected_mode is None:
            profile_for_turn = getattr(mind, "tool_profile_for_turn", None)
            if callable(profile_for_turn):
                profile_mode = profile_for_turn()
                if profile_mode not in {None, "app", "api"}:
                    raise ValueError(
                        f"Invalid tool filter mode: {profile_mode}"
                    )
                selected_mode = profile_mode

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
        if owns_event_report:
            await mind.await_cleanup(report.close(drain=not interrupted))
        elif interrupted and report_pool is not None:
            await mind.await_cleanup(report_pool.close_session(
                context.cid,
                context.sid,
                drain=False,
            ))


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
