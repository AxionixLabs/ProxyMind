# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from protocol.schema.stream_events import (
    StreamEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent
)
from protocol.schema.turn_inputs import TurnInput
from agent.application.turns.context import TurnContext
from agent.ports.agent_messages import (
    AgentMessageDeliveryPort,
    AgentMessageReceipt,
)
from agent.stores.agents.mailbox import AgentMailboxEvent

__all__ = (
    "AgentActiveTurn",
    "AgentDeliveryRegistry",
)

ACTIVE_TURN_READY_TIMEOUT_SEC = 1.0


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

    async def deliver(
        self,
        event: AgentMailboxEvent,
    ) -> AgentMessageReceipt | None:
        """在轮次就绪后投递邮箱事件。"""
        if event.kind != "message":
            raise ValueError("active turn delivery requires a message event")
        if event.recipient_agent_id != self._context.agent.agent_id:
            raise ValueError("mailbox message recipient does not match active turn")

        async with self._delivery_lock:
            if not await self._wait_until_ready():
                return None

            turn_input = _turn_input_from_event(event)
            self._pending[event.event_id] = turn_input

            receipt = await self._delivery.deliver(self._context, turn_input)
            if receipt is not None and (
                receipt.turn_id != self._context.turn_id
                or receipt.client_message_id != event.event_id
            ):
                self._pending.pop(event.event_id, None)
                raise ValueError("agent message receipt does not match delivery")
            if receipt is None or receipt.status == "duplicate":
                self._pending.pop(event.event_id, None)
            return receipt

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


class AgentDeliveryRegistry:
    """管理根会话下活动 Agent Turn 的并发投递状态。"""

    def __init__(self) -> None:
        """创建空的活动投递登记表。"""
        self._lock = asyncio.Lock()
        self._active: dict[tuple[str, str], AgentActiveTurn] = {}

    async def register(self, active: AgentActiveTurn) -> None:
        """登记活动轮次并关闭同一 Agent 的旧轮次。"""
        context = active.context
        key = (context.agent.root_session_id, context.agent.agent_id)
        async with self._lock:
            previous = self._active.get(key)
            self._active[key] = active
        if previous is not None and previous is not active:
            previous.close()

    async def unregister(self, active: AgentActiveTurn) -> None:
        """仅在登记仍指向当前轮次时移除活动状态。"""
        context = active.context
        key = (context.agent.root_session_id, context.agent.agent_id)
        async with self._lock:
            if self._active.get(key) is active:
                self._active.pop(key, None)

    async def get(
        self,
        agent_id: str,
        *,
        root_session_id: str,
    ) -> AgentActiveTurn | None:
        """返回指定根会话与 Agent 的活动轮次。"""
        key = (str(root_session_id or "").strip(), agent_id)
        async with self._lock:
            return self._active.get(key)

    async def close(self, root_session_id: str | None = None) -> None:
        """关闭全部或指定根会话的活动投递状态。"""
        async with self._lock:
            if root_session_id is None:
                active = tuple(self._active.values())
                self._active.clear()
            else:
                normalized = str(root_session_id or "").strip()
                keys = tuple(key for key in self._active if key[0] == normalized)
                active = tuple(self._active.pop(key) for key in keys)
        for delivery in active:
            delivery.close()


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


if __name__ == '__main__':
    pass
