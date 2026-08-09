# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from engine.errors import AppError
from engine.observability import observe
from ..runtime.agent.client import AgentClient
from .models import (
    AgentInboxItem,
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime
)

if typing.TYPE_CHECKING:
    from ..controller import Mind


class AgentForwardHandler(typing.Protocol):
    """处理一条服务端 forward 请求。"""

    async def handle(
        self,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus
    ) -> None:
        """处理一条服务端 forward 请求。"""
        ...


def resolve_intent_summary(payload: dict[str, typing.Any]) -> str | None:
    """提取服务端下发的任务意图摘要。"""
    intent_raw = payload.get("intent")
    if intent_raw in (None, ""):
        return None
    if not isinstance(intent_raw, dict):
        raise ValueError("mind.forward payload.intent must be an object")

    summary_raw = intent_raw.get("summary")
    if summary_raw in (None, ""):
        return None
    if not isinstance(summary_raw, str):
        raise ValueError("mind.forward payload.intent.summary must be a string")

    summary = summary_raw.strip()
    return summary or None


def normalize_forward_request(
    payload: dict[str, typing.Any]
) -> tuple[str, str | None]:
    """解析 `mind.forward` 载荷中的执行参数。"""
    message = payload.get("message")
    if not isinstance(message, str):
        raise ValueError("mind.forward payload.message must be a string")
    if not message.strip():
        raise ValueError("mind.forward payload.message must be non-empty")

    metadata_raw = payload.get("metadata")
    if metadata_raw is not None and not isinstance(metadata_raw, dict):
        raise ValueError("mind.forward payload.metadata must be an object")

    return message, resolve_intent_summary(payload)


def resolve_forward_timeout_sec(payload: dict[str, typing.Any]) -> float | None:
    """解析 `mind.forward` 的超时设置。"""
    if (raw := payload.get("timeout_sec")) in (None, ""):
        return None

    try:
        timeout_sec = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("mind.forward payload.timeout_sec must be numeric") from exc

    if timeout_sec <= 0:
        raise ValueError("mind.forward payload.timeout_sec must be greater than 0")

    return timeout_sec


def get_runtime_message_cache(runtime: AgentSessionRuntime) -> set[str]:
    """返回订阅运行态中的转发消息去重集合。"""
    if runtime.forwarded_message_ids is None:
        runtime.forwarded_message_ids = set()
    return runtime.forwarded_message_ids


class AgentExecutor(object):
    """执行服务端下发的本地任务并回写结果。"""

    async def execute(
        self,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus | None = None
    ) -> None:
        """执行一条服务端下发的本地任务。"""
        message, intent_summary = normalize_forward_request(request.payload)

        timeout_sec      = resolve_forward_timeout_sec(request.payload)
        metadata_raw     = request.payload.get("metadata")
        forward_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

        metadata = dict(forward_metadata)
        metadata["cid"] = request.cid
        metadata["sid"] = request.sid

        if intent_summary is not None:
            metadata["intent_summary"] = intent_summary

        started_at = time.perf_counter()

        observe(
            "agent.forward.start",
            call_id=request.call_id,
            message_id=request.message_id,
            message_chars=len(message),
            timeout_sec=timeout_sec or 0,
            metadata_fields=len(forward_metadata),
        )
        if live_status is not None:
            live_status.update(
                "Server Task Received", request.call_id
            )

        await client.send_mind_started(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id
        )

        runner = mind.calling(message=message, metadata=metadata)

        if timeout_sec is not None:
            result = await asyncio.wait_for(runner, timeout=timeout_sec)
        else:
            result = await runner

        if not result.ok:
            raise AppError(result.error or f"run {result.status}")

        await client.send_mind_completed(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id
        )

        observe(
            "agent.forward.complete",
            call_id=request.call_id,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

class AgentInbox(object):
    """保存等待用户处理的服务端请求。"""

    def __init__(self) -> None:
        """初始化内存收件箱。"""
        self.items: list[AgentInboxItem] = []

    def add(self, request: AgentForwardRequest) -> AgentInboxItem:
        """加入一条待处理请求。"""
        item = AgentInboxItem(request=request)
        self.items.append(item)
        return item

    def pending_items(self) -> list[AgentInboxItem]:
        """返回待处理请求列表。"""
        return [item for item in self.items if item.status == "pending"]

    def pending_count(self) -> int:
        """返回待处理请求数量。"""
        return len(self.pending_items())

    def find(self, message_id: str) -> AgentInboxItem | None:
        """按消息标识查找请求。"""
        for item in self.items:
            if item.request.message_id == message_id:
                return item
        return None

    def next_pending(self) -> AgentInboxItem | None:
        """返回最早的待处理请求。"""
        pending = self.pending_items()
        return pending[0] if pending else None

    def decline(self, message_id: str, *, reason: str = "") -> AgentInboxItem:
        """标记一条请求为已拒绝。"""
        item = self.find(message_id)
        if item is None:
            raise KeyError(message_id)
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {message_id}")
        item.status = "declined"
        item.error = reason or None
        return item

    @staticmethod
    async def accept(
        item: AgentInboxItem,
        *,
        executor: AgentExecutor,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        live_status: AgentLiveStatus
    ) -> AgentInboxItem:
        """执行一条待处理请求并调整收件箱状态。"""
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {item.request.message_id}")

        item.status = "running"
        try:
            await executor.execute(
                mind,
                client,
                connection,
                runtime,
                item.request,
                live_status
            )
        except Exception as exc:
            item.status = "failed"
            item.error = f"{type(exc).__name__}: {exc}"
            raise

        item.status = "completed"
        item.error = None
        return item

    async def accept_next(
        self,
        *,
        executor: AgentExecutor,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        live_status: AgentLiveStatus
    ) -> AgentInboxItem | None:
        """执行最早的待处理请求。"""
        item = self.next_pending()
        if item is None:
            return None
        return await self.accept(
            item,
            executor=executor,
            mind=mind,
            client=client,
            connection=connection,
            runtime=runtime,
            live_status=live_status
        )


InboxContextCallback = typing.Callable[
    [
        AgentInboxItem,
        AgentClient,
        typing.Any,
        AgentSessionRuntime,
        AgentLiveStatus,
    ],
    None
]


class InboxForwardHandler(object):
    """收到服务端请求后放入本地收件箱。"""

    def __init__(
        self,
        inbox: AgentInbox,
        context_callback: InboxContextCallback | None = None
    ) -> None:
        """保存服务端请求收件箱。"""
        self.inbox = inbox
        self.context_callback = context_callback

    async def handle(
        self,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus
    ) -> None:
        """确认收到请求并放入本地收件箱。"""
        _ = mind
        live_status.update(
            "Task Received", f"Queued {request.call_id}"
        )
        seen = get_runtime_message_cache(runtime)
        if request.message_id in seen:
            await client.send_mind_received(
                connection,
                session_id=runtime.session_id,
                cid=request.cid,
                sid=request.sid,
                call_id=request.call_id,
                acked_message_id=request.message_id
            )
            observe(
                "agent.forward.skipped",
                message_id=request.message_id,
                reason="replay",
            )
            return None

        seen.add(request.message_id)
        item = self.inbox.add(request)
        if self.context_callback is not None:
            self.context_callback(item, client, connection, runtime, live_status)

        await client.send_mind_received(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id,
            acked_message_id=request.message_id
        )
        observe(
            "agent.forward.received",
            call_id=request.call_id,
            message_id=request.message_id,
        )


if __name__ == '__main__':
    pass
