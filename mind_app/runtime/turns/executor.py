# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
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
    """创建同一会话中的续跑模型执行。"""
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

    async def run_with_session(
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]]
    ) -> TurnResultValue:
        """在已建立的工具会话中执行模型轮次。"""
        visible_tools = (
            filter_mode_tools(tool_filter_mode, tools)
            if tool_filter_mode is not None
            else tools
        )
        return await operation(execution, session, visible_tools, report)

    interrupted: bool = False

    try:
        result = await mind.with_mcp_session(pref_config, run_with_session)
    except asyncio.CancelledError:
        interrupted = True
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


if __name__ == '__main__':
    pass
