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


class FakeMindChatServer:
    """实现控制端口并精确注入提交后丢响应等传输窗口。"""

    def __init__(
        self,
        *,
        interrupt_fault: CommandFault = CommandFault.NORMAL,
        steer_behavior: SteerBehavior = SteerBehavior.ACCEPT,
    ) -> None:
        self.interrupt_fault = interrupt_fault
        self.steer_behavior = steer_behavior
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
        self.accepted_interrupt_count = 0
        self.status_read = asyncio.Event()
        self._interrupt_request_ids: set[str] = set()
        self._steer_request_ids: dict[str, str] = {}
        self._input_states: dict[str, FakeInputState] = {}
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
        return event

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
        if resolved_request_id in self._interrupt_request_ids:
            return TurnControlReceipt(
                "duplicate",
                resolved_request_id,
                turn_id,
            )
        self._interrupt_request_ids.add(resolved_request_id)
        self.accepted_interrupt_count += 1
        if self.interrupt_fault is not CommandFault.NORMAL:
            raise ProtocolCommandError(
                self.interrupt_fault.value,
                f"injected interrupt {self.interrupt_fault.value}",
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
            return TurnControlReceipt(
                "duplicate",
                resolved_request_id,
                turn_id,
                turn_input.client_message_id,
            )
        self._steer_request_ids[resolved_request_id] = (
            turn_input.client_message_id
        )

        if self.steer_behavior is SteerBehavior.REJECT:
            self._input_states[turn_input.client_message_id] = FakeInputState.RETRY
            return TurnControlReceipt(
                "turn_not_steerable",
                resolved_request_id,
                turn_id,
                turn_input.client_message_id,
            )
        if self.steer_behavior is SteerBehavior.PENDING:
            self._input_states[turn_input.client_message_id] = FakeInputState.PENDING
        else:
            self._input_states[turn_input.client_message_id] = (
                FakeInputState.COMMITTED
            )
        if self.steer_behavior is SteerBehavior.COMMIT_RESPONSE_LOST:
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
        """拒绝超出本 Fake Server 场景边界的工具结果请求。"""
        raise AssertionError("post_tool_result is outside FakeMindChatServer scope")

    async def get_tool_result_status(
        self,
        *,
        cid: str,
        sid: str,
        call_id: str,
    ) -> Mapping[str, ThawedJsonValue]:
        """拒绝超出本 Fake Server 场景边界的工具状态请求。"""
        raise AssertionError(
            "get_tool_result_status is outside FakeMindChatServer scope"
        )

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
        if (cid, sid, turn_id) != (self.cid, self.sid, self.turn_id):
            raise ProtocolCommandError(
                "turn_mismatch",
                "fake command coordinates do not match the active turn",
            )
