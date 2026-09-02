# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import (
    ApprovalCompleted,
    ApprovalStarted,
    AssistantBuffered,
    AssistantSettled,
    LogicalSettled,
    ModelWaitReason,
    ModelWaitRequested,
    OutputActivityPort,
    OutputSurfaceContext,
    RecoveryActivityMode,
    RecoveryChanged,
    ResponseIdentity,
    RetryActivitySource,
    RetryActivityState,
    RetryChanged,
    TerminalWaitCompleted,
    TerminalWaitStarted,
    ToolActivityKind,
    ToolBatchCompleted,
    ToolBatchStarted,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
    TurnTerminalStatus,
)


class TurnActivityProjector:
    """把已校验的 Turn 事实投影为单一输出会话的展示事件。"""

    def __init__(
        self,
        context: OutputSurfaceContext,
        activity: OutputActivityPort,
    ) -> None:
        """绑定不可变的输出 scope 和其活动事件出口。"""
        if not isinstance(context, OutputSurfaceContext):
            raise TypeError("output surface context is required")
        self.context = context
        self.activity = activity
        self._model_wait_revision: int = 0

    async def request_model_wait(self, reason: ModelWaitReason) -> None:
        """以当前输出会话内单调 revision 登记模型等待。"""
        self._model_wait_revision += 1
        await self.activity.emit(ModelWaitRequested(
            **self._scope(),
            revision=self._model_wait_revision,
            reason=reason,
        ))

    async def assistant_buffered(
        self,
        identity: ResponseIdentity,
        item_id: str,
    ) -> None:
        """登记尚未实际可见的 assistant 正文。"""
        await self.activity.emit(AssistantBuffered(
            **self._scope(),
            identity=identity,
            item_id=item_id,
        ))

    async def assistant_settled(
        self,
        identity: ResponseIdentity,
        item_id: str,
    ) -> None:
        """登记已稳定的 assistant 正文段。"""
        await self.activity.emit(AssistantSettled(
            **self._scope(),
            identity=identity,
            item_id=item_id,
        ))

    async def tool_batch_started(self, batch_id: str) -> None:
        """登记已开始接收的工具批次。"""
        await self.activity.emit(ToolBatchStarted(
            **self._scope(),
            batch_id=batch_id,
        ))

    async def tool_batch_completed(self, batch_id: str) -> None:
        """登记已完整接收的工具批次。"""
        await self.activity.emit(ToolBatchCompleted(
            **self._scope(),
            batch_id=batch_id,
        ))

    async def tool_started(
        self,
        tool_id: str,
        tool_kind: ToolActivityKind,
        *,
        name: str = "",
    ) -> None:
        """为一个具名工具登记活动 lease。"""
        await self.activity.emit(ToolStarted(
            **self._scope(),
            tool_id=tool_id,
            tool_kind=tool_kind,
            name=name,
        ))

    async def tool_completed(
        self,
        tool_id: str,
        tool_kind: ToolActivityKind,
        *,
        name: str = "",
    ) -> None:
        """释放匹配的具名工具活动 lease。"""
        await self.activity.emit(ToolCompleted(
            **self._scope(),
            tool_id=tool_id,
            tool_kind=tool_kind,
            name=name,
        ))

    async def terminal_wait_started(
        self,
        call_id: str,
        session_id: str,
        *,
        command: str = "",
    ) -> None:
        """登记一个工具派生的后台终端等待。"""
        await self.activity.emit(TerminalWaitStarted(
            **self._scope(),
            call_id=call_id,
            session_id=session_id,
            command=command,
        ))

    async def terminal_wait_completed(
        self,
        call_id: str,
        session_id: str,
        *,
        command: str = "",
    ) -> None:
        """释放匹配的后台终端等待。"""
        await self.activity.emit(TerminalWaitCompleted(
            **self._scope(),
            call_id=call_id,
            session_id=session_id,
            command=command,
        ))

    async def approval_started(self, approval_id: str, call_id: str) -> None:
        """登记一个获得独占交互权的审批。"""
        await self.activity.emit(ApprovalStarted(
            **self._scope(),
            approval_id=approval_id,
            call_id=call_id,
        ))

    async def approval_completed(self, approval_id: str, call_id: str) -> None:
        """释放匹配的审批交互权。"""
        await self.activity.emit(ApprovalCompleted(
            **self._scope(),
            approval_id=approval_id,
            call_id=call_id,
        ))

    async def retry_changed(
        self,
        source: RetryActivitySource,
        state: RetryActivityState,
        *,
        presentation_epoch: int,
        round_no: int,
        attempt: int,
    ) -> None:
        """登记一个具名重试来源的状态变化。"""
        await self.activity.emit(RetryChanged(
            **self._scope(),
            source=source,
            state=state,
            presentation_epoch=presentation_epoch,
            round=round_no,
            attempt=attempt,
        ))

    async def recovery_changed(
        self,
        mode: RecoveryActivityMode,
        *,
        event_seq: int,
    ) -> None:
        """登记当前输出会话的恢复模式和水位。"""
        await self.activity.emit(RecoveryChanged(
            **self._scope(),
            mode=mode,
            event_seq=event_seq,
        ))

    async def turn_terminal(self, status: TurnTerminalStatus) -> None:
        """登记 Turn 的确定终态或对账暂停态。"""
        await self.activity.emit(TurnTerminal(
            **self._scope(),
            status=status,
        ))

    async def logical_settled(self) -> None:
        """登记 Turn 的逻辑交互已完成结算。"""
        await self.activity.emit(LogicalSettled(**self._scope()))

    def _scope(self) -> dict[str, str]:
        """返回当前输出会话的事件 scope。"""
        return {
            "surface_id": self.context.surface_id,
            "turn_id": self.context.turn_id,
        }


if __name__ == '__main__':
    pass
