# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from engine.observability import observe_exception
from mind_nova.requests.turn_control import (
    TurnControlRequestError,
    steer_turn
)
from mind_nova.stream_events import (
    StreamEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent
)
from mind_nova.turn_inputs import TurnInput
from mind_app.runtime.execution import TurnContext
from mind_app.runtime.subagents.mailbox import AgentMailboxEvent

AgentMessageDeliveryStatus = typing.Literal["active_turn", "mailbox"]

ACTIVE_TURN_READY_TIMEOUT_SEC = 1.0

STEERING_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class AgentMessageDispatch:
    """描述邮箱消息的事件及实际投递通道。"""
    event: AgentMailboxEvent
    delivery: AgentMessageDeliveryStatus


class AgentMessageDeliveryPort(typing.Protocol):
    """定义向活动远程轮次投递输入所需能力。"""

    async def deliver(
        self,
        context: TurnContext,
        turn_input: TurnInput,
    ) -> bool:
        """投递输入并返回远程轮次是否已接收。"""
        ...


class SteeringMessageDelivery:
    """通过远程轮次引导接口投递活动轮次输入。"""

    async def deliver(
        self,
        context: TurnContext,
        turn_input: TurnInput,
    ) -> bool:
        """尝试投递输入，不可用时返回邮箱回退信号。"""
        response = None
        for attempt in range(STEERING_ATTEMPTS):
            try:
                response = await steer_turn(
                    cid=context.cid,
                    sid=context.sid,
                    turn_id=context.turn_id,
                    turn_input=turn_input,
                )
                break
            except TurnControlRequestError as error:
                if attempt + 1 < STEERING_ATTEMPTS:
                    continue
                observe_exception(
                    "subagent.message.steer_failed",
                    error,
                    level="WARNING",
                    agent_id=context.agent.agent_id,
                    turn_id=context.turn_id,
                )

        return response is not None and response.status in {
            "accepted",
            "duplicate",
        }


class AgentActiveTurn:
    """跟踪一个子执行主体的活动远程轮次。"""

    def __init__(
        self,
        context: TurnContext,
        delivery: AgentMessageDeliveryPort,
        *,
        ready_timeout_sec: float = ACTIVE_TURN_READY_TIMEOUT_SEC,
    ) -> None:
        if ready_timeout_sec <= 0:
            raise ValueError("active turn ready timeout must be positive")
        self._context = context
        self._delivery = delivery
        self._ready_timeout_sec = ready_timeout_sec
        self._ready = asyncio.Event()
        self._unavailable = asyncio.Event()
        self._pending: dict[str, TurnInput] = {}
        self._delivery_lock = asyncio.Lock()

    @property
    def context(self) -> TurnContext:
        """返回当前活动轮次上下文。"""
        return self._context

    def handle_event(self, event: StreamEvent) -> TurnInput | None:
        """根据流事件更新轮次状态并返回已确认输入。"""
        if event.turn_id != self._context.turn_id:
            return None

        if event.type == "turn.start":
            if not self._unavailable.is_set():
                self._ready.set()
            return None

        if isinstance(event, TurnInputAcceptedEvent):
            return self._pending.pop(event.client_message_id, None)

        if isinstance(event, TurnLogicalSettledEvent):
            self._ready.clear()
            self._unavailable.set()

        return None

    async def deliver(self, event: AgentMailboxEvent) -> bool:
        """在轮次就绪后投递邮箱事件。"""
        if event.kind != "message":
            raise ValueError("active turn delivery requires a message event")
        if event.recipient_agent_id != self._context.agent.agent_id:
            raise ValueError("mailbox message recipient does not match active turn")

        async with self._delivery_lock:
            if not await self._wait_until_ready():
                return False

            turn_input = _turn_input_from_event(event)
            self._pending[event.event_id] = turn_input
            accepted = await self._delivery.deliver(self._context, turn_input)
            if not accepted:
                self._pending.pop(event.event_id, None)
            return accepted

    def close(self) -> None:
        """关闭活动轮次并释放未确认输入。"""
        self._ready.clear()
        self._unavailable.set()
        self._pending.clear()

    async def _wait_until_ready(self) -> bool:
        """有界等待远程轮次就绪或结束。"""
        if self._unavailable.is_set():
            return False
        if self._ready.is_set():
            return True

        ready_wait = asyncio.create_task(self._ready.wait())
        unavailable_wait = asyncio.create_task(self._unavailable.wait())
        waits = (ready_wait, unavailable_wait)
        try:
            await asyncio.wait(
                waits,
                timeout=self._ready_timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for wait in waits:
                if not wait.done():
                    wait.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

        return self._ready.is_set() and not self._unavailable.is_set()


def _turn_input_from_event(event: AgentMailboxEvent) -> TurnInput:
    """把邮箱消息转换为可追踪的轮次输入。"""
    return TurnInput(
        client_message_id=event.event_id,
        text=event.message,
        extras={
            "agent_mailbox": {
                "event_id": event.event_id,
                "source_agent_id": event.source_agent_id,
                "source_task_path": event.source_task_path,
                "recipient_agent_id": event.recipient_agent_id,
                "recipient_task_path": event.recipient_task_path,
            },
        },
    )


if __name__ == "__main__":
    pass
