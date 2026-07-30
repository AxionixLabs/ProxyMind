# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from dataclasses import (
    dataclass,
    field
)
from types import MappingProxyType
from engine.observability import (
    observe,
    observe_exception
)
from mind_nova.events import EventReport
from mind_app.runtime.execution import TurnContext

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike


@dataclass(frozen=True, slots=True)
class TurnExecution:
    """保存一次已经完成身份和会话分配的模型执行。"""
    context: TurnContext
    message: str
    metadata: typing.Mapping[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """固定执行元数据并校验会话标识一致。"""
        if not isinstance(self.context, TurnContext):
            raise TypeError("turn context is required")
        if not str(self.message or "").strip():
            raise ValueError("turn message is required")

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
    event_report: EventReport | None = None
) -> TurnResultValue:
    """在独立工具和报告生命周期中执行显式模型轮次。"""
    context    = execution.context
    started_at = time.perf_counter()

    observe(
        "call.start",
        mode=context.mode,
        cid=context.cid,
        sid=context.sid,
        turn_id=context.turn_id,
        agent_id=context.agent.agent_id,
        message_chars=len(execution.message),
        sandbox_mode=context.permissions.sandbox_mode,
        approval_policy=context.permissions.approval_policy,
    )

    owns_event_report = event_report is None

    report = event_report
    if report is None:
        report = EventReport(
            context.mode,
            context.cid,
            context.sid,
        )
    if owns_event_report:
        await report.open()

    async def run_with_session(
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]]
    ) -> TurnResultValue:
        """在已建立的工具会话中执行模型轮次。"""
        return await operation(execution, session, tools, report)

    interrupted: bool = False

    try:
        result = await mind.with_mcp_session(pref_config, run_with_session)
    except asyncio.CancelledError:
        interrupted = True
        observe(
            "call.interrupted",
            level="WARNING",
            mode=context.mode,
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
            mode=context.mode,
            cid=context.cid,
            sid=context.sid,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "call.complete",
            mode=context.mode,
            cid=context.cid,
            sid=context.sid,
            outcome=result.status,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return result
    finally:
        if owns_event_report:
            await mind.await_cleanup(report.close(drain=not interrupted))


if __name__ == '__main__':
    pass
