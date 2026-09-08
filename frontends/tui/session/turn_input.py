# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import enum
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
    TurnCompletedEvent,
    TurnInputAcceptedEvent,
)
from protocol.schema.turn_inputs import TurnInput
from ..core.queued import TuiSubmission
from ..runtime.ports import TurnInputRuntimePort

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost
    from .state import TuiSessionState


class TurnInputPhase(enum.Enum):
    """描述单个 TUI 控制器当前绑定的远端 Turn 生命周期。"""

    WAITING_START = "waiting_start"
    ACTIVE = "active"
    INTERRUPT_PENDING = "interrupt_pending"
    INTERRUPTING = "interrupting"
    SETTLED = "settled"
    INTERRUPTED_SETTLED = "interrupted_settled"
    DETACHED = "detached"
    CLOSED = "closed"


class _TurnInputLifecycle(object):
    """集中裁决远端 Turn 输入和中断任务可以运行的阶段。"""

    def __init__(self) -> None:
        self.phase = TurnInputPhase.WAITING_START

    @property
    def interrupt_requested(self) -> bool:
        """返回用户中断意图是否仍约束当前控制器。"""
        return self.phase in {
            TurnInputPhase.INTERRUPT_PENDING,
            TurnInputPhase.INTERRUPTING,
            TurnInputPhase.INTERRUPTED_SETTLED,
            TurnInputPhase.DETACHED,
        }

    @property
    def accepts_steer(self) -> bool:
        """返回新输入是否还能归属当前远端 Turn。"""
        return self.phase in {
            TurnInputPhase.WAITING_START,
            TurnInputPhase.ACTIVE,
        }

    @property
    def can_send_steer(self) -> bool:
        """返回后台任务是否可以发送下一条 steer。"""
        return self.phase is TurnInputPhase.ACTIVE

    @property
    def can_send_interrupt(self) -> bool:
        """返回当前 Turn 是否仍允许调度中断命令。"""
        return self.phase in {
            TurnInputPhase.INTERRUPT_PENDING,
            TurnInputPhase.INTERRUPTING,
        }

    @property
    def settled(self) -> bool:
        """返回当前绑定的远端 Turn 是否已收到权威终态。"""
        return self.phase in {
            TurnInputPhase.SETTLED,
            TurnInputPhase.INTERRUPTED_SETTLED,
        }

    @property
    def detached(self) -> bool:
        """返回客户端是否已放弃继续观察远端终态。"""
        return self.phase is TurnInputPhase.DETACHED

    def activate(self) -> None:
        """进入下一次远端 Turn 启动等待并保留中断意图。"""
        if self.phase is TurnInputPhase.CLOSED:
            raise RuntimeError("cannot activate a closed turn input lifecycle")
        if self.phase is TurnInputPhase.DETACHED:
            raise RuntimeError("cannot activate a detached turn input lifecycle")
        self.phase = (
            TurnInputPhase.INTERRUPT_PENDING
            if self.interrupt_requested
            else TurnInputPhase.WAITING_START
        )

    def mark_started(self) -> bool:
        """登记匹配的远端启动事件并返回是否接受该事件。"""
        if self.phase in {
            TurnInputPhase.SETTLED,
            TurnInputPhase.INTERRUPTED_SETTLED,
            TurnInputPhase.DETACHED,
            TurnInputPhase.CLOSED,
        }:
            return False
        if self.phase is TurnInputPhase.INTERRUPT_PENDING:
            self.phase = TurnInputPhase.INTERRUPTING
        elif self.phase is TurnInputPhase.WAITING_START:
            self.phase = TurnInputPhase.ACTIVE
        return True

    def request_interrupt(self) -> bool:
        """登记中断意图并返回是否可以立即发送远端命令。"""
        if self.phase in {
            TurnInputPhase.SETTLED,
            TurnInputPhase.INTERRUPTED_SETTLED,
            TurnInputPhase.DETACHED,
            TurnInputPhase.CLOSED,
        }:
            return False
        if self.phase is TurnInputPhase.WAITING_START:
            self.phase = TurnInputPhase.INTERRUPT_PENDING
        elif self.phase is TurnInputPhase.ACTIVE:
            self.phase = TurnInputPhase.INTERRUPTING
        return self.can_send_interrupt

    def settle(self) -> bool:
        """登记权威终态并返回是否首次完成该转换。"""
        if self.phase in {
            TurnInputPhase.SETTLED,
            TurnInputPhase.INTERRUPTED_SETTLED,
            TurnInputPhase.DETACHED,
            TurnInputPhase.CLOSED,
        }:
            return False
        self.phase = (
            TurnInputPhase.INTERRUPTED_SETTLED
            if self.interrupt_requested
            else TurnInputPhase.SETTLED
        )
        return True

    def detach(self) -> bool:
        """停止本地观察，但不改变远端 Turn 的权威状态。"""
        if self.phase in {TurnInputPhase.DETACHED, TurnInputPhase.CLOSED}:
            return False
        self.phase = TurnInputPhase.DETACHED
        return True

    def close(self) -> None:
        """关闭控制器并禁止后续生命周期转换。"""
        self.phase = TurnInputPhase.CLOSED


class TuiTurnInputControl(object):
    """协调活动轮次的即时输入、下一轮输入和远端中断。"""

    RECONCILE_DEADLINE_SEC: typing.Final[float] = 2.0
    RECONCILE_RETRY_INTERVAL_SEC: typing.Final[float] = 0.2
    INTERRUPT_PERSISTENCE_DEADLINE_SEC: typing.Final[float] = 5.0
    INTERRUPT_PERSISTENCE_RETRY_INTERVAL_SEC: typing.Final[float] = 0.1

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
        allow_steer: bool = True,
    ) -> None:
        """绑定当前会话坐标并初始化输入对账状态。"""
        self._controller = controller
        self._runtime = runtime
        self._state = state
        self._target = (cid, sid, turn_id)

        if not isinstance(protocol_client, ProtocolCommandClient):
            raise TypeError("TUI turn input requires ProtocolCommandClient")

        self._protocol_client = protocol_client
        self._allow_steer = bool(allow_steer)
        self._lifecycle = _TurnInputLifecycle()
        self._stream_end_reason: ModelStreamEndReason = "cancelled"
        self._steer_ids: list[str] = []
        self._steer_task: asyncio.Task[None] | None = None
        self._interrupt_task: asyncio.Task[None] | None = None
        self._interrupt_target: tuple[str, str, str] | None = None
        self._interrupt_request_id: str | None = None
        self._interrupt_acknowledged: bool = False
        self._interrupt_failed: bool = False
        self._interrupt_waiting_for_start: bool = False
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def phase(self) -> TurnInputPhase:
        """返回当前远端 Turn 输入控制阶段。"""
        return self._lifecycle.phase

    def activate(self, context: TurnContext) -> None:
        """更新等待服务端启动确认的远端轮次。"""
        previous_settled = self._lifecycle.settled
        if context.turn_id != self._target[2]:
            self._retire_target_workers()
        resolution = self._runtime.advance_pending_steers(
            tuple(self._steer_ids),
            settled=previous_settled,
        )
        resolved_ids = set(resolution.resolved_ids)
        self._steer_ids = [
            client_message_id
            for client_message_id in self._steer_ids
            if client_message_id not in resolved_ids
        ]
        self._lifecycle.activate()
        self._target = (context.cid, context.sid, context.turn_id)
        self._stream_end_reason = "cancelled"

        if self._lifecycle.interrupt_requested:
            self._start_interrupt_worker()

        for client_message_id in resolution.resolved_ids:
            self._runtime.resolve_pending_steer(client_message_id)
        for submission in resolution.uncertain:
            self._runtime.retain_uncertain_steer(submission)

    def _retire_target_workers(self) -> None:
        """停止只对上一远端 Turn 有效的控制请求。"""
        for task in (self._steer_task, self._interrupt_task):
            if task is not None and not task.done():
                task.cancel()
        self._steer_task = None
        self._interrupt_task = None
        self._interrupt_target = None
        self._interrupt_request_id = None
        self._interrupt_acknowledged = False
        self._interrupt_failed = False
        self._interrupt_waiting_for_start = False

    def submit(self, submission: TuiSubmission, queue_only: bool) -> bool:
        """按按键意图接管执行期间提交的输入。"""
        captured = self._capture_payload(submission)
        if (
            not self._allow_steer
            or queue_only
            or not self._lifecycle.accepts_steer
        ):
            self._runtime.defer_submission(captured)
            return True

        if captured.shell_mode:
            self._runtime.defer_submission(captured)
            return True

        self._runtime.track_pending_steer(captured)
        if captured.client_message_id not in self._steer_ids:
            self._steer_ids.append(captured.client_message_id)
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

        if event.type == "turn.started":
            if not self._lifecycle.mark_started():
                return None
            if self._lifecycle.interrupt_requested:
                self._interrupt_waiting_for_start = False
                self._start_interrupt_worker()
                return None
            if self._allow_steer:
                self._start_steer_worker()
            return None

        if isinstance(event, TurnInputAcceptedEvent):
            deferred = self._runtime.discard_rejected_steer(
                event.client_message_id
            )

            pending = (
                self._runtime.resolve_pending_steer(event.client_message_id)
                or deferred
            )

            if pending is None:
                return None

            turn_input = self._input_from_submission(pending)

            self._runtime.append_turn_input(active_turn_id, pending)
            return turn_input

        if not isinstance(event, TurnCompletedEvent):
            return None

        self._lifecycle.settle()
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

    def request_interrupt(self) -> bool:
        """记录中断意图，并立即尝试中断已知坐标的远端轮次。"""
        if not self._lifecycle.request_interrupt():
            return False
        steer_task = self._steer_task
        if steer_task is not None and not steer_task.done():
            steer_task.cancel()
        return self._start_interrupt_worker()

    def _start_interrupt_worker(self) -> bool:
        """启动当前轮次唯一的幂等远端中断任务。"""
        cid, sid, turn_id = self._target
        if (
            not cid
            or not sid
            or not turn_id
            or not self._lifecycle.can_send_interrupt
        ):
            return False
        if self._interrupt_target != self._target:
            self._interrupt_target = self._target
            self._interrupt_request_id = new_request_id("interrupt")
            self._interrupt_acknowledged = False
            self._interrupt_failed = False
            self._interrupt_waiting_for_start = False

        if self._interrupt_acknowledged or self._interrupt_failed:
            return True
        if (
            self._interrupt_waiting_for_start
            and self.phase is TurnInputPhase.INTERRUPT_PENDING
        ):
            return True
        task = self._interrupt_task
        if task is not None and not task.done():
            return True

        request_id = self._interrupt_request_id
        if request_id is None:
            raise RuntimeError("interrupt request identity is required")

        self._interrupt_task = self._start(
            self._send_interrupt(cid, sid, turn_id, request_id),
        )
        observe(
            "turn.interrupt.requested",
            cid=cid,
            sid=sid,
            turn_id=turn_id,
        )
        return True

    def abandon(self) -> None:
        """在用户强制退出时停止等待远端控制与输入对账。"""
        if not self._lifecycle.detach():
            return None
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()

    def restore_draft(self, submission: TuiSubmission) -> None:
        """把取回消息的结构化载荷合并到当前草稿。"""
        if not submission.payload_bound:
            return None
        current_attachments = tuple(
            self._controller.attach.pending_attachments_snapshot()
        )
        self._controller.attach.replace_pending_attachments(
            submission.attachments + current_attachments
        )
        current_extras = self._state.consume_pending_prompt_extras()
        restored_extras = dict(submission.extras)
        restored_extras.update(current_extras)
        self._state.replace_pending_prompt_extras(restored_extras)

    async def close(self) -> None:
        """停止控制请求并对账未确认的活动轮次输入。"""
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._steer_task = None
        self._interrupt_task = None
        self._interrupt_target = None
        self._interrupt_request_id = None
        self._interrupt_acknowledged = False
        self._interrupt_failed = False
        self._interrupt_waiting_for_start = False

        committed_ids: tuple[str, ...] = ()
        retry_ids: tuple[str, ...] = ()

        sent_ids = self._runtime.pending_steer_sent_ids(
            tuple(self._steer_ids),
        )

        if (
            not self._lifecycle.detached
            and not self._lifecycle.settled
            and sent_ids
            and self._stream_end_reason != "settled"
        ):
            committed_ids, retry_ids = await self._reconcile(sent_ids)

        resolution = self._runtime.close_pending_steers(
            tuple(self._steer_ids),
            settled=self._lifecycle.settled,
            committed_ids=committed_ids,
            retry_ids=retry_ids,
        )
        self._steer_ids.clear()

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
        self._lifecycle.close()

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
            or not self._lifecycle.can_send_steer
            or self._runtime.next_local_steer(tuple(self._steer_ids)) is None
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
        while self._lifecycle.can_send_steer and self._target[2] == turn_id:
            submission = self._runtime.next_local_steer(
                tuple(self._steer_ids),
            )
            if submission is None:
                return None

            self._runtime.mark_pending_steer_sent(
                submission.client_message_id,
            )

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
                if attempt == 0 and error.retryable:
                    continue
                observe_exception("turn.steer.failed", error, level="WARNING")
                break

        if response is None:
            return False

        if response.status in {
            "turn_not_active",
            "turn_not_steerable",
            "turn_mismatch",
        }:
            pending = self._runtime.resolve_pending_steer(
                submission.client_message_id,
            )
            if pending is not None:
                self._runtime.defer_rejected_steer(pending)

        return True

    async def _send_interrupt(
        self,
        cid: str,
        sid: str,
        turn_id: str,
        request_id: str,
    ) -> None:
        """以固定请求身份提交中断，并吸收 Turn 持久化创建竞态。"""
        started_at = time.perf_counter()
        loop = asyncio.get_running_loop()
        persistence_deadline = (
            loop.time() + self.INTERRUPT_PERSISTENCE_DEADLINE_SEC
        )
        retryable_failure_used: bool = False
        post_start_retry_used: bool = False

        while (
            self._lifecycle.can_send_interrupt
            and self._target == (cid, sid, turn_id)
        ):
            try:
                response = await self._protocol_client.interrupt_turn(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    request_id=request_id,
                )
                if self._interrupt_request_id == request_id:
                    self._interrupt_acknowledged = True
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
                return None
            except ProtocolCommandError as error:
                if (
                    not self._lifecycle.can_send_interrupt
                    or self._target != (cid, sid, turn_id)
                ):
                    return None

                if self._is_turn_persistence_race(error):
                    remaining = persistence_deadline - loop.time()
                    if remaining > 0:
                        await asyncio.sleep(min(
                            self.INTERRUPT_PERSISTENCE_RETRY_INTERVAL_SEC,
                            remaining,
                        ))
                        continue
                    if (
                        self.phase is TurnInputPhase.INTERRUPTING
                        and not post_start_retry_used
                    ):
                        post_start_retry_used = True
                        continue
                    if self.phase is TurnInputPhase.INTERRUPTING:
                        if self._interrupt_request_id == request_id:
                            self._interrupt_failed = True
                        self._observe_interrupt_failure(
                            error,
                            cid=cid,
                            sid=sid,
                            turn_id=turn_id,
                            request_id=request_id,
                            started_at=started_at,
                        )
                        return None
                    if self._interrupt_request_id == request_id:
                        self._interrupt_waiting_for_start = True
                    return None

                if error.retryable and not retryable_failure_used:
                    retryable_failure_used = True
                    continue
                if self._interrupt_request_id == request_id:
                    self._interrupt_failed = True
                self._observe_interrupt_failure(
                    error,
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    request_id=request_id,
                    started_at=started_at,
                )
                return None

    @staticmethod
    def _observe_interrupt_failure(
        error: ProtocolCommandError,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        request_id: str,
        started_at: float,
    ) -> None:
        """记录已经越过安全重试边界的远端中断失败。"""
        observe_exception(
            "turn.interrupt.failed",
            error,
            level="WARNING",
            cid=cid,
            sid=sid,
            turn_id=turn_id,
            request_id=request_id,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

    @staticmethod
    def _is_turn_persistence_race(error: ProtocolCommandError) -> bool:
        """判断中断命令是否早于对应 queued Turn 的持久化。"""
        return bool(
            error.code == "turn_not_found"
            and error.status_code == 404
        )

    @staticmethod
    def _input_from_submission(submission: TuiSubmission) -> TurnInput:
        """把本地提交转换为远端轮次输入。"""
        return TurnInput(
            client_message_id=submission.client_message_id,
            text=submission.value,
            attachments=submission.attachments,
            extras=submission.extras,
        )


if __name__ == '__main__':
    pass
