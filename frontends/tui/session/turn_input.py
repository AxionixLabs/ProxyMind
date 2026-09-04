# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing
from dataclasses import replace

from agent.application.turns.context import TurnContext
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
)
from agent.protocol import (
    ModelStreamEndReason,
    SteerTurnInput,
)
from observability import (
    observe,
    observe_exception,
)
from protocol.schema.identifiers import new_request_id
from protocol.schema.stream_events import (
    StreamEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
)
from protocol.schema.turn_inputs import TurnInput
from .steer_ledger import PendingSteerLedger
from ..core.queued import TuiSubmission
from ..runtime.ports import TurnInputRuntimePort

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost
    from .state import TuiSessionState


class TuiTurnInputControl(object):
    """协调活动轮次的即时输入、下一轮输入和远端中断。"""

    RECONCILE_DEADLINE_SEC: typing.Final[float] = 2.0
    RECONCILE_RETRY_INTERVAL_SEC: typing.Final[float] = 0.2
    INTERRUPT_STATUS_RETRY_INTERVAL_SEC: typing.Final[float] = 0.2

    def __init__(
        self,
        controller: "TuiApplicationHost",
        runtime: TurnInputRuntimePort,
        state: "TuiSessionState",
        *,
        cid: str,
        sid: str,
        turn_id: str,
        protocol_client: ProtocolCommandClient,
    ) -> None:
        """绑定当前会话坐标并初始化输入对账状态。"""
        self._controller = controller
        self._runtime = runtime
        self._state = state
        self._target = (cid, sid, turn_id)

        if not isinstance(protocol_client, ProtocolCommandClient):
            raise TypeError("TUI turn input requires ProtocolCommandClient")

        self._protocol_client = protocol_client
        self._ready_turn_id: str = ""
        self._stream_end_reason: ModelStreamEndReason = "cancelled"
        self._ledger: PendingSteerLedger = PendingSteerLedger()
        self._steer_task: asyncio.Task[None] | None = None
        self._interrupt_task: asyncio.Task[None] | None = None
        self._interrupt_requested: bool = False
        self._interrupt_ready_callback: typing.Callable[[], None] | None = None
        self._abandoned: bool = False
        self._tasks: set[asyncio.Task[None]] = set()

    def activate(self, context: TurnContext) -> None:
        """更新等待服务端启动确认的远端轮次。"""
        self._target = (context.cid, context.sid, context.turn_id)
        self._ready_turn_id = ""
        self._stream_end_reason = "cancelled"

        resolution = self._ledger.advance()
        for client_message_id in resolution.resolved_ids:
            self._runtime.resolve_pending_steer(client_message_id)
        for submission in resolution.uncertain:
            self._runtime.retain_uncertain_steer(submission)

    def submit(self, submission: TuiSubmission, queue_only: bool) -> bool:
        """按按键意图接管执行期间提交的输入。"""
        captured = self._capture_payload(submission)
        if queue_only or self._interrupt_requested:
            self._runtime.defer_submission(captured)
            return True

        if captured.shell_mode:
            self._runtime.defer_submission(captured)
            return True

        self._ledger.add(captured)
        self._runtime.track_pending_steer(captured)
        self._start_steer_worker()
        return True

    def handle_event(
        self,
        event: StreamEvent
    ) -> TurnInput | None:
        """按服务端确认结果完成即时输入或安排下一轮输入。"""
        active_turn_id = self._target[2]
        if event.turn_id != active_turn_id:
            return None

        if event.type == "turn.start":
            self._ready_turn_id = active_turn_id
            if self._interrupt_requested:
                self._start_interrupt_worker()
                return None
            self._start_steer_worker()
            return None

        if isinstance(event, TurnInputAcceptedEvent):
            deferred = self._runtime.discard_rejected_steer(
                event.client_message_id
            )

            pending = (
                self._ledger.commit(event.client_message_id)
                or deferred
            )

            self._runtime.resolve_pending_steer(event.client_message_id)

            if pending is None:
                return None

            turn_input = self._input_from_submission(pending)

            self._runtime.append_turn_input(active_turn_id, pending)
            return turn_input

        if not isinstance(event, TurnLogicalSettledEvent):
            return None

        self._ledger.settle()
        self._ready_turn_id = ""

        next_input = event.next_input
        if next_input is None:
            return None

        pending = self._ledger.release(next_input.client_message_id)

        submission = (
            pending
            or self._runtime.discard_rejected_steer(
            next_input.client_message_id
        )
            or self._submission_from_input(next_input)
        )

        self._runtime.defer_rejected_steer(submission)

        return None

    def handle_stream_end(self, reason: ModelStreamEndReason) -> None:
        """记录当前远端事件传输的最终结束原因。"""
        if reason not in {
            "settled",
            "fatal",
            "cancelled",
            "protocol_error",
        }:
            raise ValueError(f"invalid turn stream end reason: {reason}")
        self._stream_end_reason = reason

    def request_interrupt(
        self,
        *,
        on_remote_control_ready: typing.Callable[[], None] | None = None,
    ) -> bool:
        """记录中断意图，并在远端控制就绪时同步通知本地流。"""
        self._interrupt_requested = True
        if on_remote_control_ready is not None:
            self._interrupt_ready_callback = on_remote_control_ready
        return self._start_interrupt_worker()

    def _start_interrupt_worker(self) -> bool:
        """在远端 Turn 就绪后启动由当前轮次持有的中断任务。"""
        cid, sid, turn_id = self._target
        if self._interrupt_task is not None:
            self._notify_interrupt_ready()
            return True
        if (
            not cid
            or not sid
            or not turn_id
            or self._ready_turn_id != turn_id
        ):
            return False

        self._interrupt_task = self._start(
            self._send_interrupt(cid, sid, turn_id),
        )
        self._notify_interrupt_ready()
        observe(
            "turn.interrupt.requested",
            cid=cid,
            sid=sid,
            turn_id=turn_id,
        )
        return True

    def _notify_interrupt_ready(self) -> None:
        """一次性通知调用方远端中断控制已经可以调度。"""
        callback = self._interrupt_ready_callback
        self._interrupt_ready_callback = None
        if callback is not None:
            callback()

    def abandon(self) -> None:
        """在用户强制退出时停止等待远端控制与输入对账。"""
        if self._abandoned:
            return None
        self._abandoned = True
        self._interrupt_ready_callback = None
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()

    def restore_draft(self, submission: TuiSubmission) -> None:
        """恢复从本地队列取回消息关联的结构化草稿。"""
        if not submission.payload_bound:
            return None
        self._controller.attach.replace_pending_attachments(
            submission.attachments
        )
        self._state.replace_pending_prompt_extras(submission.extras)

    async def close(self) -> None:
        """停止控制请求并对账未确认的活动轮次输入。"""
        steer_task = self._steer_task
        if steer_task is not None and not steer_task.done():
            steer_task.cancel()

        tasks = tuple(self._tasks)
        self._tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._steer_task = None
        self._interrupt_task = None
        self._interrupt_ready_callback = None

        committed_ids: tuple[str, ...] = ()
        retry_ids: tuple[str, ...] = ()

        sent_ids = self._ledger.sent_ids()

        if (
            not self._abandoned
            and not self._ledger.settled
            and sent_ids
            and self._stream_end_reason != "settled"
        ):
            committed_ids, retry_ids = await self._reconcile(sent_ids)

        resolution = self._ledger.close(
            committed_ids=committed_ids,
            retry_ids=retry_ids,
        )

        retry_client_message_ids = {
            submission.client_message_id
            for submission in resolution.retry
        }
        for client_message_id in resolution.resolved_ids:
            if client_message_id in retry_client_message_ids:
                continue
            self._runtime.resolve_pending_steer(client_message_id)
        for submission in resolution.retry:
            self._runtime.defer_rejected_steer(submission)
        for submission in resolution.uncertain:
            self._runtime.retain_uncertain_steer(submission)

    async def _reconcile(
        self,
        client_message_ids: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """在绝对截止时间内查询已发送输入的稳定归属。"""
        cid, sid, turn_id = self._target

        loop = asyncio.get_running_loop()

        deadline = loop.time() + self.RECONCILE_DEADLINE_SEC
        pending_ids = client_message_ids

        committed: list[str] = []
        retry: list[str] = []

        while pending_ids:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout_at(deadline):
                    response = await self._protocol_client.reconcile_turn_inputs(
                        cid=cid,
                        sid=sid,
                        turn_id=turn_id,
                        client_message_ids=pending_ids,
                    )
            except (ProtocolCommandError, TimeoutError) as error:
                observe_exception(
                    "turn.reconcile.failed",
                    error,
                    level="WARNING",
                )
                break

            committed.extend(response.committed_ids)
            retry.extend(response.retry_ids)

            pending_ids = response.pending_ids
            if not pending_ids:
                break

            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            await asyncio.sleep(min(
                self.RECONCILE_RETRY_INTERVAL_SEC,
                remaining,
            ))

        return tuple(committed), tuple(retry)

    def _capture_payload(self, submission: TuiSubmission) -> TuiSubmission:
        """固定提交时有效的附件与扩展输入。"""
        attachments: tuple[dict[str, typing.Any], ...] = ()
        if self._controller.attach.has_pending_attachments():
            attachments = tuple(
                self._controller.attach.consume_pending_attachments()
            )

        extras = self._state.consume_pending_prompt_extras()

        return replace(
            submission,
            attachments=attachments,
            extras=extras,
            payload_bound=True,
        )

    def _start(
        self,
        awaitable: typing.Coroutine[typing.Any, typing.Any, None]
    ) -> asyncio.Task[None]:
        """启动轮次控制请求并跟踪其生命周期。"""
        task = asyncio.create_task(awaitable, name="tui turn input control")
        self._tasks.add(task)
        return task

    def _start_steer_worker(self) -> None:
        """在服务端轮次就绪后按提交顺序发送即时输入。"""
        cid, sid, turn_id = self._target
        if (
            not cid
            or not sid
            or not turn_id
            or self._ready_turn_id != turn_id
            or self._ledger.next_local() is None
        ):
            return None
        if self._steer_task is not None and not self._steer_task.done():
            return None
        self._steer_task = self._start(
            self._send_pending_steers(cid, sid, turn_id)
        )

    async def _send_pending_steers(
        self,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> None:
        """依次发送当前采样阶段已经暂存的即时输入。"""
        while (
            self._ready_turn_id == turn_id
        ):
            submission = self._ledger.next_local()
            if submission is None:
                return None

            self._ledger.mark_sent(submission.client_message_id)

            confirmed = await self._send_steer(
                cid,
                sid,
                turn_id,
                submission,
            )

            if not confirmed:
                return None

    async def _send_steer(
        self,
        cid: str,
        sid: str,
        turn_id: str,
        submission: TuiSubmission
    ) -> bool:
        """提交即时输入，并返回服务端是否已经明确响应归属。"""
        turn_input = self._input_from_submission(submission)
        request_id = new_request_id("steer")

        response = None
        wire_input = SteerTurnInput(
            client_message_id=turn_input.client_message_id,
            text=turn_input.text,
            attachments=tuple(turn_input.attachments),
            extras=turn_input.extras,
        )
        for attempt in range(2):
            try:
                response = await self._protocol_client.steer_turn(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    turn_input=wire_input,
                    request_id=request_id,
                )
                break
            except ProtocolCommandError as error:
                if attempt == 0:
                    continue
                observe_exception("turn.steer.failed", error, level="WARNING")

        if response is None:
            return False

        if response.status in {
            "turn_not_active",
            "turn_not_steerable",
            "turn_mismatch",
        }:
            pending = self._ledger.release(submission.client_message_id)
            if pending is not None:
                self._runtime.defer_rejected_steer(pending)

        return True

    async def _send_interrupt(
        self,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> None:
        """提交远端中断并记录传输失败。"""
        started_at = time.perf_counter()
        request_id = new_request_id("interrupt")

        for attempt in range(2):
            try:
                response = await self._protocol_client.interrupt_turn(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    request_id=request_id,
                )
                observe(
                    "turn.interrupt.remote",
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    request_id=request_id,
                    status=response.status,
                    elapsed_ms=int(
                        (time.perf_counter() - started_at) * 1000
                    ),
                )
                break
            except ProtocolCommandError as error:
                if attempt == 0:
                    continue
                observe_exception(
                    "turn.interrupt.failed",
                    error,
                    level="WARNING",
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    request_id=request_id,
                    elapsed_ms=int(
                        (time.perf_counter() - started_at) * 1000
                    ),
                )

        await self._wait_for_interrupt_settlement(
            cid,
            sid,
            turn_id,
        )

    async def _wait_for_interrupt_settlement(
        self,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> None:
        """等待远端轮次进入权威终态后再释放下一轮入口。"""
        started_at = time.perf_counter()
        while True:
            try:
                snapshot = await self._protocol_client.get_turn_status(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                )
            except ProtocolCommandError as error:
                observe_exception(
                    "turn.interrupt.status_failed",
                    error,
                    level="WARNING",
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                )
                if error.status_code == 404 or not error.retryable:
                    return None
            else:
                if snapshot.terminal:
                    observe(
                        "turn.interrupt.settled",
                        cid=cid,
                        sid=sid,
                        turn_id=turn_id,
                        status=snapshot.status,
                        elapsed_ms=int(
                            (time.perf_counter() - started_at) * 1000
                        ),
                    )
                    return None

            await asyncio.sleep(self.INTERRUPT_STATUS_RETRY_INTERVAL_SEC)

    @staticmethod
    def _input_from_submission(submission: TuiSubmission) -> TurnInput:
        """把本地提交转换为远端轮次输入。"""
        return TurnInput(
            client_message_id=submission.client_message_id,
            text=submission.value,
            attachments=submission.attachments,
            extras=submission.extras,
        )

    @staticmethod
    def _submission_from_input(turn_input: TurnInput) -> TuiSubmission:
        """把服务端结算输入转换为本地下一轮提交。"""
        return TuiSubmission(
            value=turn_input.text,
            editable_text=turn_input.text,
            paste_store={},
            client_message_id=turn_input.client_message_id,
            attachments=turn_input.attachments,
            extras=turn_input.extras,
            payload_bound=True,
        )


if __name__ == '__main__':
    pass
