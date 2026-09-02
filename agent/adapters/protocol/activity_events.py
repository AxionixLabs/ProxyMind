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
    TransportRecoveryPhase,
)


def normalize_turn_terminal_status(status: str) -> TurnTerminalStatus:
    """把 Harness 运行结果收窄为展示 reducer 的终态分类。"""
    if status == "completed":
        return "completed"
    if status == "interrupted":
        return "interrupted"
    if status == "reconciliation_required":
        return "reconciliation_required"
    return "failed"


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
        self._presentation_epoch: int = 1
        self._round: int = 1
        self._attempt: int = 1
        self._event_seq: int = 0
        self._transport_retry_generation: int = 0
        self._active_retries: dict[
            RetryActivitySource,
            tuple[int, int, int],
        ] = {}
        self._terminal_status: TurnTerminalStatus | None = None

    def observe_presentation(
        self,
        *,
        presentation_epoch: int,
        round_no: int,
        attempt: int,
    ) -> None:
        """更新已通过 Canonical Item 验证的展示 Attempt 身份。"""
        for field_name, value in (
            ("presentation_epoch", presentation_epoch),
            ("round", round_no),
            ("attempt", attempt),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be positive")
        self._presentation_epoch = presentation_epoch
        self._round = round_no
        self._attempt = attempt

    def observe_event_seq(self, event_seq: int | None) -> None:
        """推进当前输出会话已验证的持久事件水位。"""
        if event_seq is None:
            return None
        if isinstance(event_seq, bool) or not isinstance(event_seq, int):
            raise TypeError("event_seq must be an integer")
        if event_seq < self._event_seq:
            return None
        self._event_seq = event_seq

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

    async def provider_retry_started(
        self,
        *,
        presentation_epoch: int,
        round_no: int,
        attempt: int,
    ) -> None:
        """用服务端的模型 Attempt 身份启动 provider retry lease。"""
        self.observe_presentation(
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )
        await self._start_retry(
            "provider",
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )

    async def provider_retry_completed(self) -> None:
        """释放当前 provider retry lease。"""
        await self._complete_retry("provider")

    async def transport_recovery_changed(
        self,
        phase: TransportRecoveryPhase,
        event_seq: int,
    ) -> None:
        """投影传输重连、静默 replay 和追平边界。"""
        self.observe_event_seq(event_seq)
        if phase == "reconnecting":
            if "transport" in self._active_retries:
                return None
            self._transport_retry_generation += 1
            await self._start_retry(
                "transport",
                presentation_epoch=self._presentation_epoch,
                round_no=self._round,
                attempt=self._transport_retry_generation,
            )
            return None
        if phase == "replaying":
            await self.recovery_changed(
                "replaying",
                event_seq=self._event_seq,
            )
            await self._complete_retry("transport")
            return None
        if phase == "caught_up":
            await self._complete_retry("transport")
            await self.recovery_changed(
                "caught_up",
                event_seq=self._event_seq,
            )
            return None
        if phase == "closed":
            await self._complete_retry("transport")
            return None
        raise ValueError("transport recovery phase is invalid")

    async def close_retries(self) -> None:
        """幂等释放当前输出会话的全部 retry lease。"""
        await self._complete_retry("transport")
        await self._complete_retry("provider")

    async def _start_retry(
        self,
        source: RetryActivitySource,
        *,
        presentation_epoch: int,
        round_no: int,
        attempt: int,
    ) -> None:
        """替换同来源的旧 retry lease 并登记新身份。"""
        identity = (presentation_epoch, round_no, attempt)
        if self._active_retries.get(source) == identity:
            return None
        await self._complete_retry(source)
        await self.retry_changed(
            source,
            "started",
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )
        self._active_retries[source] = identity

    async def _complete_retry(self, source: RetryActivitySource) -> None:
        """释放指定来源当前活动的 retry lease。"""
        identity = self._active_retries.pop(source, None)
        if identity is None:
            return None
        presentation_epoch, round_no, attempt = identity
        await self.retry_changed(
            source,
            "completed",
            presentation_epoch=presentation_epoch,
            round_no=round_no,
            attempt=attempt,
        )

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
        if self._terminal_status is not None:
            if self._terminal_status != status:
                raise ValueError("turn terminal status conflicts with existing state")
            return None
        self._terminal_status = status
        await self.activity.emit(TurnTerminal(
            **self._scope(),
            status=status,
        ))

    async def logical_settled(self) -> None:
        """登记 Turn 的逻辑交互已完成结算。"""
        await self.activity.emit(LogicalSettled(**self._scope()))

    @property
    def terminal_status(self) -> TurnTerminalStatus | None:
        """返回已投影的 Turn 终态。"""
        return self._terminal_status

    @property
    def event_seq(self) -> int:
        """返回当前输出会话已确认的持久事件水位。"""
        return self._event_seq

    def _scope(self) -> dict[str, str]:
        """返回当前输出会话的事件 scope。"""
        return {
            "surface_id": self.context.surface_id,
            "turn_id": self.context.turn_id,
        }


if __name__ == '__main__':
    pass
