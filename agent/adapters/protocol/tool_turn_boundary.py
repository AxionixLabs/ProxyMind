# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from agent.adapters.protocol.approval_events import ApprovalEventHandler
from agent.adapters.protocol.tool_dispatch import (
    StreamToolDispatcher,
    ToolDispatchResult,
)
from agent.application.turns.context import TurnContext
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.ports import (
    ApprovalLedger,
    ProtocolCommandError,
    TransportRecoveryPhase,
)
from observability import observe
from protocol.client.tools import ToolResultRequestError
from protocol.schema.stream_events import (
    StreamEvent,
    ToolApprovalRequiredEvent,
    ToolEvent,
)
from protocol.schema.tool_approval import ToolApprovalSnapshot

_DETERMINISTIC_APPROVAL_CLOSURE_CODES = frozenset({
    "approval_decision_conflict",
    "approval_not_pending",
})


def record_unhandled_tool_error(
    outcome: StreamTurnOutcome,
    error: ToolResultRequestError | ProtocolCommandError,
) -> str:
    """记录无法继续消费事件流的工具命令错误，并返回展示阶段。"""
    message = f"{error.code}: {error}"
    if error.is_deterministic_terminal:
        outcome.mark_delivery_incomplete(message, error_code=error.code)
        return "turn.incomplete"
    outcome.require_reconciliation(message)
    return "turn.tool_result_delivery_failed"


class ToolTurnBoundary:
    """把工具命令终态适配为不越权结束远端 Turn 的流处理语义。"""

    def __init__(
        self,
        turn_context: TurnContext,
        outcome: StreamTurnOutcome,
        approval_handler: ApprovalEventHandler,
        dispatcher: StreamToolDispatcher,
    ) -> None:
        """绑定单个远端 Turn 的结果聚合器、审批账本和事件处理器。"""
        ledger = turn_context.approval_ledger
        if not isinstance(ledger, ApprovalLedger):
            raise TypeError("approval ledger is required")
        self._cid = turn_context.cid
        self._sid = turn_context.sid
        self._turn_id = turn_context.turn_id
        self._outcome = outcome
        self._ledger = ledger
        self._approval_handler = approval_handler
        self._dispatcher = dispatcher

    async def restore_approval_snapshot(
        self,
        snapshot: ToolApprovalSnapshot,
    ) -> None:
        """恢复审批；调用已关闭时继续等待同一流的权威终态。"""
        for item in snapshot.approvals:
            try:
                await self._approval_handler.restore_snapshot(replace(
                    snapshot,
                    approvals=(item,),
                ))
            except (ToolResultRequestError, ProtocolCommandError) as error:
                if not self._record_closed_delivery(error, call_id=item.call_id):
                    raise
                self._record_approval_terminal(item.call_id)

    async def transport_recovery_changed(
        self,
        phase: TransportRecoveryPhase,
        event_seq: int,
    ) -> None:
        """恢复历史工具；调用已关闭时继续观察同一远端 Turn。"""
        try:
            await self._dispatcher.transport_recovery_changed(phase, event_seq)
        except (ToolResultRequestError, ProtocolCommandError) as error:
            if not self._record_closed_delivery(error):
                raise

    async def handle_approval(self, event: ToolApprovalRequiredEvent) -> None:
        """提交审批决定，并阻止已关闭调用被迟到事件重新打开。"""
        try:
            await self._approval_handler.handle(event)
        except (ToolResultRequestError, ProtocolCommandError) as error:
            if not self._record_closed_delivery(error, call_id=event.call_id):
                raise
            self._record_approval_terminal(event.call_id)

    async def dispatch(self, event: StreamEvent) -> ToolDispatchResult:
        """分派工具事件；调用关闭不替代远端 Turn 终态。"""
        try:
            return await self._dispatcher.dispatch(event)
        except (ToolResultRequestError, ProtocolCommandError) as error:
            call_id = event.call_id if isinstance(event, ToolEvent) else None
            if not self._record_closed_delivery(error, call_id=call_id):
                raise
            return ToolDispatchResult("handled")

    async def read_event(self, events: AsyncIterator[StreamEvent]) -> StreamEvent:
        """在工具运行期间消费远端事件，并在同一所有者内归约工具完成。"""
        event_task = asyncio.create_task(anext(events))
        completion_task = asyncio.create_task(self._dispatcher.next_completion())
        try:
            while True:
                await asyncio.wait(
                    (event_task, completion_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if completion_task.done():
                    try:
                        result = await completion_task
                    except (ToolResultRequestError, ProtocolCommandError) as error:
                        if not self._record_closed_delivery(error):
                            raise
                    else:
                        if result.status == "interrupted":
                            self._outcome.interrupt(result.error)
                    completion_task = asyncio.create_task(self._dispatcher.next_completion())
                if event_task.done():
                    return event_task.result()
        finally:
            for task in (event_task, completion_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(event_task, completion_task, return_exceptions=True)

    def _record_closed_delivery(
        self,
        error: ToolResultRequestError | ProtocolCommandError,
        *,
        call_id: str | None = None,
    ) -> bool:
        """记录确定关闭的调用，但不合成本地 Turn 终态。"""
        if (
            not error.is_deterministic_terminal
            and error.code not in _DETERMINISTIC_APPROVAL_CLOSURE_CODES
        ):
            return False
        resolved_call_id = str(
            call_id or error.details.get("call_id") or ""
        ).strip()
        self._outcome.mark_delivery_incomplete(
            f"{error.code}: {error}",
            error_code=error.code,
        )
        observe(
            "stream.tool_result_delivery_stopped",
            level="WARNING",
            turn_id=self._turn_id,
            call_id=resolved_call_id or None,
            code=error.code,
            status_code=error.status_code,
            trace_id=error.trace_id,
        )
        return True

    def _record_approval_terminal(self, call_id: str | None) -> None:
        """把具备有效身份的关闭审批写入当前 Turn 账本。"""
        resolved_call_id = str(call_id or "").strip()
        if not resolved_call_id:
            return None
        self._ledger.record_terminal(
            cid=self._cid,
            sid=self._sid,
            turn_id=self._turn_id,
            call_id=resolved_call_id,
        )


if __name__ == '__main__':
    pass
