# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from engine.observability import (
    observe,
    observe_exception
)
from ..runtime.agent.client import AgentClient
from ..runtime.agent.protocol import build_envelope
from .models import (
    AgentForwardRequest,
    AgentSessionRuntime
)

CancelReason = typing.Literal[
    "user_interrupted",
    "message_deleted",
    "client_shutdown",
]


@dataclass(slots=True)
class PendingTerminalStatus:
    """等待服务端 ACK 的终态信封。"""
    session_id: str
    envelope: dict[str, typing.Any]


class AgentStatusOutbox(object):
    """保存未确认终态，并在当前连接或重连后补发。"""

    def __init__(self) -> None:
        self.pending: dict[str, PendingTerminalStatus] = {}
        self._binding: tuple[AgentClient, typing.Any, AgentSessionRuntime] | None = None
        self._session_id: str | None = None
        self._send_lock = asyncio.Lock()

    def bind(
        self,
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
    ) -> None:
        """绑定当前连接并异步补发属于该会话的终态。"""
        if self._session_id is not None and self._session_id != runtime.session_id:
            stale_ids = [
                message_id
                for message_id, pending in self.pending.items()
                if pending.session_id != runtime.session_id
            ]
            for message_id in stale_ids:
                self.pending.pop(message_id, None)
            if stale_ids:
                observe(
                    "agent.status.dropped",
                    reason="session_reopened",
                    count=len(stale_ids),
                )
        self._session_id = runtime.session_id
        self._binding = (client, connection, runtime)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        loop.create_task(self.flush(), name="agent-status-outbox-flush")

    def unbind(self) -> None:
        """移除已断开的连接。"""
        self._binding = None

    def acknowledge(self, message_id: str) -> None:
        """收到服务端 ACK 后移除对应终态。"""
        if self.pending.pop(str(message_id or ""), None) is not None:
            observe("agent.status.acked", message_id=message_id)

    async def completed(
        self,
        request: AgentForwardRequest,
        *,
        session_id: str,
    ) -> None:
        await self._queue("mind.completed", request, session_id=session_id, payload={})

    async def failed(
        self,
        request: AgentForwardRequest,
        *,
        session_id: str,
        error: BaseException,
    ) -> None:
        await self._queue(
            "mind.failed",
            request,
            session_id=session_id,
            payload={
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            },
        )

    async def cancelled(
        self,
        request: AgentForwardRequest,
        *,
        session_id: str,
        reason: CancelReason,
    ) -> None:
        await self._queue(
            "mind.cancelled",
            request,
            session_id=session_id,
            payload={"reason": reason},
        )

    async def _queue(
        self,
        message_type: str,
        request: AgentForwardRequest,
        *,
        session_id: str,
        payload: dict[str, typing.Any],
    ) -> None:
        envelope = build_envelope(
            message_type,
            session_id,
            cid=request.cid,
            sid=request.sid,
            payload={"call_id": request.call_id, **payload},
        )
        message_id = str(envelope["message_id"])
        self.pending[message_id] = PendingTerminalStatus(
            session_id=session_id,
            envelope=envelope,
        )
        await self._send(message_id)

    async def _send(self, message_id: str) -> None:
        pending = self.pending.get(message_id)
        binding = self._binding
        if pending is None or binding is None:
            return None

        client, connection, runtime = binding
        if runtime.session_id != pending.session_id:
            return None

        try:
            async with self._send_lock:
                if message_id not in self.pending or self._binding != binding:
                    return None
                await client.send_json(connection, pending.envelope)
            observe(
                "agent.status.sent",
                message_id=message_id,
                message_type=pending.envelope.get("type"),
                call_id=(pending.envelope.get("payload") or {}).get("call_id"),
            )
        except Exception as error:
            observe_exception("agent.status.send_failed", error, level="WARNING")

    async def flush(self) -> None:
        """按入队顺序补发当前会话尚未确认的终态。"""
        for message_id in list(self.pending):
            await self._send(message_id)


if __name__ == '__main__':
    pass
