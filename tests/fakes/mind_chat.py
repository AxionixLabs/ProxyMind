"""提供可编排协议故障的最小有状态 mind.chat 测试服务。"""

import asyncio
import enum
import typing
from collections.abc import (
    AsyncIterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass

from agent.ports import ProtocolCommandError
from agent.protocol import (
    ConversationForkReceipt,
    SteerTurnInput,
    TurnCompletedSnapshot,
    TurnControlReceipt,
    TurnReconcileReceipt,
    TurnStatusSnapshot,
)
from agent.protocol.commands import TurnRuntimeStatus
from agent.protocol.json_value import (
    JsonValue,
    ThawedJsonValue,
)


class CommandFault(enum.Enum):
    NORMAL = "normal"
    RESPONSE_LOST = "response_lost"
    TIMEOUT = "timeout"
    RETRYABLE_ERROR = "retryable_error"


class SteerBehavior(enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    COMMIT_RESPONSE_LOST = "commit_response_lost"
    PENDING = "pending"


class ToolResultBehavior(enum.Enum):
    ACCEPT = "accept"
    RESPONSE_LOST = "response_lost"
    COMMIT_RESPONSE_LOST = "commit_response_lost"


class FakeInputState(enum.Enum):
    COMMITTED = "committed"
    PENDING = "pending"
    RETRY = "retry"


class FakeEventKind(enum.Enum):
    TURN_STARTED = "turn.started"
    ASSISTANT_DELTA = "assistant.delta"
    TURN_COMPLETED = "turn.completed"
    STREAM_CLOSED = "stream.closed"


@dataclass(frozen=True, slots=True)
class FakeServerEvent:
    """描述 Fake Server 事件流中的稳定事件。"""

    kind: FakeEventKind
    turn_id: str
    event_seq: int
    text: str = ""


@dataclass(frozen=True, slots=True)
class FakeMindChatRequest:
    """记录一次 `/mind-chat` 请求及其起始游标。"""

    turn_id: str
    initial_event_seq: int


@dataclass(frozen=True, slots=True)
class FakeEventSpec:
    """描述故障计划中的一个确定事件。"""

    kind: FakeEventKind
    event_seq: int
    turn_id: str | None = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class FakeMindChatFaultPlan:
    """组合命令、工具结果与事件序列故障。"""

    interrupt: CommandFault = CommandFault.NORMAL
    steer: SteerBehavior = SteerBehavior.ACCEPT
    tool_result: ToolResultBehavior = ToolResultBehavior.ACCEPT
    events: tuple[FakeEventSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class FakeToolResultRequest:
    """记录一次工具结果提交的稳定身份。"""

    cid: str
    sid: str
    call_id: str
    name: str
    ok: bool
    result: Mapping[str, JsonValue]
    additional_context: tuple[str, ...]
    request_id: str


@dataclass(frozen=True, slots=True)
class FakeServerFact:
    """记录 Fake Server 边界发生的一个可诊断事实。"""

    operation: str
    turn_id: str
    request_id: str = ""
    subject_id: str = ""
    event_seq: int | None = None
    owner: str = ""
    status: str = ""


class FakeMindChatServer:
    """实现控制端口并精确注入提交后丢响应等传输窗口。

    本实现不创建后台 task、socket 或进程；事件队列由场景调用方显式写入和消费，因而没有
    独立 close 生命周期。
    """

    def __init__(
        self,
        *,
        fault_plan: FakeMindChatFaultPlan = FakeMindChatFaultPlan(),
    ) -> None:
        self.fault_plan = fault_plan
        self.cid = "cid-test"
        self.sid = "sid-test"
        self.turn_id = ""
        self.status: TurnRuntimeStatus = "completed"
        self.terminal: TurnCompletedSnapshot | None = None
        self.last_event_seq = 0
        self.client_cursor = 0
        self.mind_chat_requests: list[FakeMindChatRequest] = []
        self.interrupt_requests: list[str] = []
        self.steer_requests: list[tuple[str, str]] = []
        self.tool_result_requests: list[FakeToolResultRequest] = []
        self.facts: list[FakeServerFact] = []
        self.accepted_interrupt_count = 0
        self.status_read = asyncio.Event()
        self._interrupt_request_ids: set[str] = set()
        self._steer_request_ids: dict[str, str] = {}
        self._input_states: dict[str, FakeInputState] = {}
        self._tool_results: dict[str, FakeToolResultRequest] = {}
        self._events: asyncio.Queue[FakeServerEvent] = asyncio.Queue()

    @property
    def input_states(self) -> Mapping[str, FakeInputState]:
        """返回服务端当前持有的输入归属快照。"""
        return dict(self._input_states)

    async def post_mind_chat(
        self,
        *,
        turn_id: str,
        initial_event_seq: int,
        status: TurnRuntimeStatus = "running",
    ) -> None:
        """模拟 `/mind-chat`，并拒绝权威终态前的下一 Turn。"""
        if self.turn_id and self.terminal is None:
            raise ProtocolCommandError(
                "turn_conflict",
                "a turn is still active",
                details={"status_code": 409},
            )
        self.turn_id = turn_id
        self.status = status
        self.terminal = None
        self.mind_chat_requests.append(FakeMindChatRequest(
            turn_id,
            initial_event_seq,
        ))
        self._record(
            "mind_chat",
            event_seq=initial_event_seq,
            status=status,
        )
        await self.emit_event(FakeEventKind.TURN_STARTED)

    async def emit_event(
        self,
        kind: FakeEventKind,
        *,
        event_seq: int | None = None,
        turn_id: str | None = None,
        text: str = "",
    ) -> FakeServerEvent:
        """向 SSE 队列写入一个可重复、可跳号或可迟到的事件。"""
        resolved_seq = (
            self.last_event_seq + 1
            if event_seq is None
            else event_seq
        )
        if resolved_seq > self.last_event_seq:
            self.last_event_seq = resolved_seq
        event = FakeServerEvent(
            kind,
            turn_id or self.turn_id,
            resolved_seq,
            text,
        )
        await self._events.put(event)
        self._record(
            "event",
            turn_id=event.turn_id,
            event_seq=event.event_seq,
            status=event.kind.value,
        )
        return event

    async def emit_fault_events(self) -> None:
        """按故障计划写入可重复、跳号和迟到 Turn 事件。"""
        for event in self.fault_plan.events:
            await self.emit_event(
                event.kind,
                event_seq=event.event_seq,
                turn_id=event.turn_id,
                text=event.text,
            )

    async def stream_events(self) -> AsyncIterator[FakeServerEvent]:
        """按服务端入队顺序生成 SSE 事件。"""
        while True:
            event = await self._events.get()
            if event.kind is FakeEventKind.STREAM_CLOSED:
                return
            yield event

    def settle(
        self,
        status: TurnRuntimeStatus,
        *,
        last_event_seq: int,
    ) -> None:
        """把活动 Turn 原子推进为权威终态快照。"""
        if status not in {"completed", "failed", "interrupted", "cancelled"}:
            raise ValueError("fake terminal status is invalid")
        self.status = status
        self.last_event_seq = max(self.last_event_seq, last_event_seq)
        if status == "completed":
            completed_status = "completed"
        elif status == "failed":
            completed_status = "failed"
        elif status == "interrupted":
            completed_status = "interrupted"
        else:
            completed_status = "cancelled"
        self.terminal = TurnCompletedSnapshot(
            turn_id=self.turn_id,
            status=completed_status,
            error=None,
            last_event_seq=self.last_event_seq,
            completed_at=1.0,
        )
        self._record(
            "settle",
            event_seq=self.last_event_seq,
            status=status,
        )

    async def interrupt_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        request_id: str | None = None,
    ) -> TurnControlReceipt:
        """模拟幂等 `/turn/interrupt` 及提交后的传输失败。"""
        self._validate_turn(cid, sid, turn_id)
        resolved_request_id = request_id or "interrupt-request"
        self.interrupt_requests.append(resolved_request_id)
        self._record("interrupt", request_id=resolved_request_id)
        if resolved_request_id in self._interrupt_request_ids:
            return TurnControlReceipt(
                "duplicate",
                resolved_request_id,
                turn_id,
            )
        self._interrupt_request_ids.add(resolved_request_id)
        self.accepted_interrupt_count += 1
        if self.fault_plan.interrupt is not CommandFault.NORMAL:
            raise ProtocolCommandError(
                self.fault_plan.interrupt.value,
                f"injected interrupt {self.fault_plan.interrupt.value}",
                retryable=True,
            )
        return TurnControlReceipt("accepted", resolved_request_id, turn_id)

    async def steer_turn(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        turn_input: SteerTurnInput,
        request_id: str | None = None,
    ) -> TurnControlReceipt:
        """模拟幂等 `/turn/steer` 和服务端输入归属。"""
        self._validate_turn(cid, sid, turn_id)
        resolved_request_id = request_id or "steer-request"
        self.steer_requests.append((
            resolved_request_id,
            turn_input.client_message_id,
        ))
        known_message_id = self._steer_request_ids.get(resolved_request_id)
        if known_message_id is not None:
            if known_message_id != turn_input.client_message_id:
                raise ProtocolCommandError(
                    "idempotency_conflict",
                    "request id was reused for another input",
                )
            known_state = self._input_states.get(turn_input.client_message_id)
            self._record(
                "steer",
                request_id=resolved_request_id,
                subject_id=turn_input.client_message_id,
                owner=known_state.value if known_state is not None else "",
                status="duplicate",
            )
            return TurnControlReceipt(
                "duplicate",
                resolved_request_id,
                turn_id,
                turn_input.client_message_id,
            )
        self._steer_request_ids[resolved_request_id] = (
            turn_input.client_message_id
        )

        if self.fault_plan.steer is SteerBehavior.REJECT:
            self._input_states[turn_input.client_message_id] = FakeInputState.RETRY
            self._record(
                "steer",
                request_id=resolved_request_id,
                subject_id=turn_input.client_message_id,
                owner=FakeInputState.RETRY.value,
                status="turn_not_steerable",
            )
            return TurnControlReceipt(
                "turn_not_steerable",
                resolved_request_id,
                turn_id,
                turn_input.client_message_id,
            )
        if self.fault_plan.steer is SteerBehavior.PENDING:
            self._input_states[turn_input.client_message_id] = FakeInputState.PENDING
        else:
            self._input_states[turn_input.client_message_id] = (
                FakeInputState.COMMITTED
            )
        input_state = self._input_states[turn_input.client_message_id]
        self._record(
            "steer",
            request_id=resolved_request_id,
            subject_id=turn_input.client_message_id,
            owner=input_state.value,
            status=self.fault_plan.steer.value,
        )
        if self.fault_plan.steer is SteerBehavior.COMMIT_RESPONSE_LOST:
            raise ProtocolCommandError(
                "response_lost",
                "steer was committed before its response was lost",
                retryable=True,
            )
        return TurnControlReceipt(
            "accepted",
            resolved_request_id,
            turn_id,
            turn_input.client_message_id,
        )

    async def reconcile_turn_inputs(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        client_message_ids: Sequence[str],
    ) -> TurnReconcileReceipt:
        """返回每条未确认 steer 的权威服务端归属。"""
        self._validate_turn(cid, sid, turn_id)
        committed: list[str] = []
        pending: list[str] = []
        retry: list[str] = []
        unknown: list[str] = []
        for client_message_id in client_message_ids:
            state = self._input_states.get(client_message_id)
            if state is FakeInputState.COMMITTED:
                committed.append(client_message_id)
            elif state is FakeInputState.PENDING:
                pending.append(client_message_id)
            elif state is FakeInputState.RETRY:
                retry.append(client_message_id)
            else:
                unknown.append(client_message_id)
        return TurnReconcileReceipt(
            turn_id=turn_id,
            turn_exists=True,
            terminal=self.terminal,
            committed_ids=tuple(committed),
            pending_ids=tuple(pending),
            retry_ids=tuple(retry),
            unknown_ids=tuple(unknown),
        )

    async def get_turn_status(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> TurnStatusSnapshot:
        """返回当前持久化 Turn 状态并暴露 status probe 同步点。"""
        self._validate_turn(cid, sid, turn_id)
        self.status_read.set()
        snapshot = TurnStatusSnapshot(
            cid=cid,
            sid=sid,
            turn_id=turn_id,
            run_id=f"run-{turn_id}",
            status=self.status,
            terminal=self.terminal,
            attempt=1,
            version=1,
            last_event_seq=self.last_event_seq,
            created_at=0.0,
            updated_at=0.0,
        )
        if snapshot.terminal is not None:
            self.client_cursor = max(
                self.client_cursor,
                snapshot.terminal.last_event_seq,
            )
        self._record(
            "status",
            event_seq=snapshot.last_event_seq,
            status=snapshot.status,
        )
        return snapshot

    async def fork_session(
        self,
        *,
        cid: str,
        sid: str,
        request_id: str,
        prompt_source: typing.Literal["none", "server", "client"],
        before_turn_id: str | None = None,
    ) -> ConversationForkReceipt:
        """拒绝超出本 Fake Server 场景边界的分支请求。"""
        raise AssertionError("fork_session is outside FakeMindChatServer scope")

    async def post_tool_result(
        self,
        cid: str,
        sid: str,
        call_id: str,
        name: str,
        ok: bool,
        result: Mapping[str, JsonValue],
        additional_context: Sequence[str] = (),
        request_id: str | None = None,
    ) -> None:
        """模拟不可覆盖的工具结果提交及提交前后丢回执窗口。"""
        self._validate_session(cid, sid)
        resolved_request_id = request_id or f"tool-result-{call_id}"
        request = FakeToolResultRequest(
            cid=cid,
            sid=sid,
            call_id=call_id,
            name=name,
            ok=ok,
            result=dict(result),
            additional_context=tuple(additional_context),
            request_id=resolved_request_id,
        )
        self.tool_result_requests.append(request)
        known = self._tool_results.get(call_id)
        if known is not None:
            if known != request:
                raise ProtocolCommandError(
                    "tool_result_conflict",
                    "tool result cannot overwrite an existing result",
                )
            self._record(
                "tool_result",
                request_id=resolved_request_id,
                subject_id=call_id,
                status="duplicate",
            )
            return
        if self.fault_plan.tool_result is not ToolResultBehavior.RESPONSE_LOST:
            self._tool_results[call_id] = request
        self._record(
            "tool_result",
            request_id=resolved_request_id,
            subject_id=call_id,
            status=self.fault_plan.tool_result.value,
        )
        if self.fault_plan.tool_result is not ToolResultBehavior.ACCEPT:
            raise ProtocolCommandError(
                "response_lost",
                "tool result response was lost",
                retryable=True,
            )

    async def get_tool_result_status(
        self,
        *,
        cid: str,
        sid: str,
        call_id: str,
    ) -> Mapping[str, ThawedJsonValue]:
        """返回正式字段形状的工具结果权威状态。"""
        self._validate_session(cid, sid)
        request = self._tool_results.get(call_id)
        received = request is not None
        self._record(
            "tool_result_status",
            subject_id=call_id,
            status="result_received" if received else "missing",
        )
        return {
            "cid": cid,
            "sid": sid,
            "call_id": call_id,
            "turn_id": self.turn_id,
            "name": request.name if request is not None else "",
            "tool_status": "result_received" if received else "missing",
            "completion_mode": "interactive",
            "turn_status": self.status,
            "result_received": received,
            "request_id": request.request_id if request is not None else None,
            "completed_at": 1.0 if received else None,
            "execution_deadline_at": None,
            "failure_reason": None,
            "effect_id": None,
            "effect_status": None,
            "reconciliation_required": False,
        }

    async def renew_tool_result(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        call_id: str,
        name: str,
        extension_seconds: int = 60,
        request_id: str | None = None,
    ) -> Mapping[str, ThawedJsonValue]:
        """拒绝超出本 Fake Server 场景边界的工具续期请求。"""
        raise AssertionError("renew_tool_result is outside FakeMindChatServer scope")

    async def post_tool_approval(
        self,
        cid: str,
        sid: str,
        call_id: str,
        approval_id: str,
        decision: str,
        *,
        turn_id: str,
        kind: str = "command",
        approval: Mapping[str, JsonValue] | None = None,
        request_id: str | None = None,
        execpolicy_amendment_id: str | None = None,
        reason: str | None = None,
        additional_context: Sequence[str] = (),
    ) -> None:
        """拒绝超出本 Fake Server 场景边界的审批提交请求。"""
        raise AssertionError("post_tool_approval is outside FakeMindChatServer scope")

    async def post_effect_reconciliation(
        self,
        *,
        effect_id: str,
        request_id: str,
        resolution: typing.Literal["committed", "failed", "retry"],
        result_payload: Mapping[str, JsonValue] | None = None,
        error: str = "",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """拒绝超出本 Fake Server 场景边界的效果对账请求。"""
        raise AssertionError(
            "post_effect_reconciliation is outside FakeMindChatServer scope"
        )

    def _validate_turn(self, cid: str, sid: str, turn_id: str) -> None:
        self._validate_session(cid, sid)
        if turn_id != self.turn_id:
            raise ProtocolCommandError(
                "turn_mismatch",
                "fake command coordinates do not match the active turn",
            )

    def _validate_session(self, cid: str, sid: str) -> None:
        """校验 Fake Server 固定的会话身份。"""
        if (cid, sid) != (self.cid, self.sid):
            raise ProtocolCommandError(
                "session_mismatch",
                "fake command coordinates do not match the active session",
            )

    def trace_text(self) -> str:
        """返回带序号和关键身份字段的边界事实。"""
        return "\n".join(
            (
                f"{index} {fact.operation} turn={fact.turn_id or '-'} "
                f"request={fact.request_id or '-'} "
                f"subject={fact.subject_id or '-'} "
                f"event_seq={fact.event_seq if fact.event_seq is not None else '-'} "
                f"owner={fact.owner or '-'} status={fact.status or '-'}"
            )
            for index, fact in enumerate(self.facts, start=1)
        )

    def _record(
        self,
        operation: str,
        *,
        turn_id: str | None = None,
        request_id: str = "",
        subject_id: str = "",
        event_seq: int | None = None,
        owner: str = "",
        status: str = "",
    ) -> None:
        self.facts.append(FakeServerFact(
            operation=operation,
            turn_id=self.turn_id if turn_id is None else turn_id,
            request_id=request_id,
            subject_id=subject_id,
            event_seq=event_seq,
            owner=owner,
            status=status,
        ))
