# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import replace
from engine.observability import observe_exception
from mind_nova.requests.turn_control import (
    TurnControlRequestError,
    interrupt_turn,
    steer_turn
)
from mind_nova.stream_events import (
    StreamEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent
)
from mind_nova.turn_inputs import TurnInput
from ...runtime.execution import TurnContext
from ..core.queued import TuiSubmission
from ..core.runtime import TuiRuntime

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from .state import TuiSessionState


class TuiTurnInputControl(object):
    """协调活动轮次的即时输入、下一轮输入和远端中断。"""

    def __init__(
        self,
        controller: "Mind",
        runtime: TuiRuntime,
        state: "TuiSessionState",
        *,
        cid: str,
        sid: str,
        turn_id: str
    ) -> None:
        self._controller    = controller
        self._runtime       = runtime
        self._state         = state
        self._target        = (cid, sid, turn_id)
        self._ready_turn_id = ""

        self._pending: dict[str, TuiSubmission] = {}
        self._tasks: set[asyncio.Task[None]]    = set()

    def activate(self, context: TurnContext) -> None:
        """更新等待服务端启动确认的远端轮次。"""
        self._target = (context.cid, context.sid, context.turn_id)
        self._ready_turn_id = ""

    def submit(self, submission: TuiSubmission, queue_only: bool) -> bool:
        """按按键意图接管执行期间提交的输入。"""
        if queue_only:
            self._runtime.defer_submission(self._capture_payload(submission))
            return True

        cid, sid, turn_id = self._target

        if (
            not cid
            or not sid
            or not turn_id
            or self._ready_turn_id != turn_id
        ):
            return False

        captured = self._capture_payload(submission)
        if captured.shell_mode:
            self._runtime.defer_submission(captured)
            return True

        self._pending[captured.client_message_id] = captured
        self._start(self._send_steer(cid, sid, turn_id, captured))
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
            return None

        if isinstance(event, TurnInputAcceptedEvent):
            pending = self._pending.pop(event.client_message_id, None)
            if pending is None:
                return None

            turn_input = self._input_from_submission(pending)

            self._runtime.append_turn_input(active_turn_id, pending)
            return turn_input

        if not isinstance(event, TurnLogicalSettledEvent):
            return None

        next_input = event.next_input
        if next_input is None:
            return None

        pending = self._pending.pop(next_input.client_message_id, None)

        submission = pending or self._submission_from_input(next_input)

        self._runtime.defer_submission(submission, next_input=True)

        return None

    def interrupt(self, fallback: typing.Callable[[], bool]) -> bool:
        """请求远端中断当前轮次，并在请求失败时执行本地取消。"""
        cid, sid, turn_id = self._target
        if (
            not cid
            or not sid
            or not turn_id
            or self._ready_turn_id != turn_id
        ):
            return fallback()

        self._start(self._send_interrupt(cid, sid, turn_id, fallback))
        return True

    def restore_draft(self, submission: TuiSubmission) -> None:
        """恢复从本地队列取回消息关联的结构化草稿。"""
        if not submission.payload_bound:
            return None
        self._controller.attach.replace_pending_attachments(
            submission.attachments
        )
        self._state.replace_pending_prompt_extras(submission.extras)

    async def close(self) -> None:
        """等待已经发起的轮次控制请求完成。"""
        tasks = tuple(self._tasks)
        self._tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

    def _start(self, awaitable: typing.Coroutine[typing.Any, typing.Any, None]) -> None:
        """启动轮次控制请求并跟踪其生命周期。"""
        task = asyncio.create_task(awaitable, name="tui turn input control")
        self._tasks.add(task)

    async def _send_steer(
        self,
        cid: str,
        sid: str,
        turn_id: str,
        submission: TuiSubmission
    ) -> None:
        """提交即时输入，并在确定未被服务端保留时回退到下一轮。"""
        turn_input = self._input_from_submission(submission)

        response = None
        for attempt in range(2):
            try:
                response = await steer_turn(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                    turn_input=turn_input,
                )
                break
            except TurnControlRequestError as error:
                if attempt == 0:
                    continue
                observe_exception("turn.steer.failed", error, level="WARNING")

        if response is None or response.status in {
            "turn_not_active",
            "turn_not_steerable",
            "turn_mismatch",
        }:
            pending = self._pending.pop(submission.client_message_id, None)
            if pending is not None:
                self._runtime.defer_submission(pending)

    @staticmethod
    async def _send_interrupt(
        cid: str,
        sid: str,
        turn_id: str,
        fallback: typing.Callable[[], bool]
    ) -> None:
        """提交远端中断，并在无法匹配活动轮次时取消本地任务。"""
        response = None
        for attempt in range(2):
            try:
                response = await interrupt_turn(
                    cid=cid,
                    sid=sid,
                    turn_id=turn_id,
                )
                break
            except TurnControlRequestError as error:
                if attempt == 0:
                    continue
                observe_exception("turn.interrupt.failed", error, level="WARNING")

        if response is None or response.status not in {
            "accepted",
            "turn_not_steerable",
        }:
            fallback()

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
