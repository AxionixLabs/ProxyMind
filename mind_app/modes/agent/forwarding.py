# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
import asyncio
from loguru import logger
from engine.errors import MindError
from mind_nova.modes import (
    RUN_MODE_SET,
    RunMode
)
from ...runtime.agent.client import AgentClient
from .models import (
    AgentInboxItem,
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime
)
from .ui import start_status_animation

if typing.TYPE_CHECKING:
    from ...controller import Mind


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


def normalize_forward_profiles(payload: dict[str, typing.Any]) -> list[str] | None:
    """把服务端 `mind.forward.payload.profile` 归一化为输入入口列表。"""
    profile_raw = payload.get("profile")
    if profile_raw in (None, ""):
        return None
    if not isinstance(profile_raw, list):
        raise ValueError("mind.forward payload.profile must be a list when provided")

    normalized: list[str] = []
    for index, item in enumerate(profile_raw):
        if not isinstance(item, str):
            raise ValueError(f"mind.forward payload.profile[{index}] must be a string")

        entry = item.strip()
        if not entry:
            raise ValueError(f"mind.forward payload.profile[{index}] must be a non-empty string")
        normalized.append(entry)

    return normalized or None


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


def normalize_forward_target(
    payload: dict[str, typing.Any]
) -> tuple[
    RunMode, str | None, list[typing.Any] | None, str | None
]:
    """解析 `mind.forward` 载荷，映射到本地可执行的模式与参数。"""
    mode_raw = payload.get("mode")
    if not isinstance(mode_raw, str):
        raise ValueError("mind.forward payload.mode must be a string")
    mode = mode_raw.strip().lower()
    if mode not in RUN_MODE_SET:
        raise ValueError("mind.forward payload.mode must be chat, fast, or xtra")

    mode = typing.cast(RunMode, mode)

    message_raw = payload.get("message")
    if message_raw in (None, ""):
        message = None
    elif isinstance(message_raw, str):
        message = message_raw
    else:
        raise ValueError("mind.forward payload.message must be a string when provided")

    metadata_raw = payload.get("metadata")
    if metadata_raw is not None and not isinstance(metadata_raw, dict):
        raise ValueError("mind.forward payload.metadata must be an object")

    profile_entries = normalize_forward_profiles(payload)
    intent_summary  = resolve_intent_summary(payload)

    if str(message or "").strip():
        return mode, message, None, intent_summary

    if profile_entries:
        return mode, None, profile_entries, intent_summary

    raise ValueError("mind.forward requires non-empty message or payload.profile")


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
        mode, message, profile, intent_summary = normalize_forward_target(request.payload)

        timeout_sec      = resolve_forward_timeout_sec(request.payload)
        metadata_raw     = request.payload.get("metadata")
        forward_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

        metadata = dict(forward_metadata)
        metadata["cid"] = request.cid
        metadata["sid"] = request.sid
        if intent_summary is not None:
            metadata["intent_summary"] = intent_summary

        logger.debug(
            f"[Agent] forward start call_id={request.call_id} mode={mode} "
            f"message={json.dumps(message, ensure_ascii=False)} "
            f"profile={json.dumps(profile, ensure_ascii=False)} "
            f"timeout_sec={timeout_sec or 0} "
            f"metadata={json.dumps(forward_metadata, ensure_ascii=False)}"
        )
        if live_status is not None:
            live_status.update(
                "Server Task Received", f"{mode} · {request.call_id}"
            )

        await client.send_mind_started(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id
        )

        if profile is not None:
            runner = mind.mind_pack(profile, mode, metadata=metadata)
        else:
            if message is None:
                raise MindError("mind.forward resolved empty message")
            runner = mind.calling(message=message, mode=mode, metadata=metadata)

        if timeout_sec is not None:
            await asyncio.wait_for(runner, timeout=timeout_sec)
        else:
            await runner

        await client.send_mind_completed(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id
        )

        logger.debug(
            f"[Agent] forward done call_id={request.call_id} mode={mode}"
        )

    def spawn(
        self,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus | None = None
    ) -> None:
        """以后台任务方式执行服务端请求，避免阻塞 WS 心跳处理。"""
        tasks = runtime.pending_tasks if runtime.pending_tasks is not None else set()

        runtime.pending_tasks = tasks

        async def runner() -> None:
            try:
                await self.execute(
                    mind,
                    client,
                    connection,
                    runtime,
                    request,
                    live_status
                )
            except asyncio.CancelledError:
                logger.debug(
                    f"[Agent] forward cancelled call_id={request.call_id}"
                )
                if live_status is not None:
                    live_status.update(
                        "Exiting Subscription", "Canceled in-flight local task"
                    )
                raise
            except Exception as exc:
                logger.debug(
                    f"[Agent] forward failed call_id={request.call_id}: {type(exc).__name__}: {exc}"
                )
                await client.send_mind_failed(
                    connection,
                    session_id=runtime.session_id,
                    cid=request.cid,
                    sid=request.sid,
                    call_id=request.call_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc)
                )
                if live_status is not None:
                    live_status.update(
                        "Task Execution Failed", f"{request.call_id} · {type(exc).__name__}"
                    )
            finally:
                if live_status is not None and not mind.task_event.is_set():
                    live_status.update(
                        "Waiting for Server Tasks", "Long link established and listening"
                    )
                    await start_status_animation(mind, live_status)

        task = asyncio.create_task(runner(), name=f"agent-forward-{request.call_id or 'unknown'}")
        tasks.add(task)
        task.add_done_callback(tasks.discard)


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


class AutoForwardHandler(object):
    """收到服务端请求后立即执行本地任务。"""

    def __init__(self, executor: AgentExecutor | None = None) -> None:
        """保存服务端任务执行器。"""
        self.executor = executor or AgentExecutor()

    async def handle(
        self,
        mind: "Mind",
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus
    ) -> None:
        """确认收到请求并启动本地任务。"""
        live_status.update(
            "Task Accepted", f"Validated {request.call_id}, stopping live status"
        )
        await mind.await_cleanup(mind.stop_anim())

        await client.send_mind_received(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id,
            acked_message_id=request.message_id
        )
        logger.debug(
            f"[Agent] mind.received sent call_id={request.call_id} message_id={request.message_id}"
        )

        seen = get_runtime_message_cache(runtime)
        if request.message_id in seen:
            logger.debug(
                f"[Agent] mind.forward replay skipped message_id={request.message_id}"
            )
            return None

        seen.add(request.message_id)
        self.executor.spawn(
            mind,
            client,
            connection,
            runtime,
            request,
            live_status
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
        await client.send_mind_received(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id,
            acked_message_id=request.message_id
        )
        logger.debug(
            f"[Agent] mind.received sent call_id={request.call_id} message_id={request.message_id}"
        )

        seen = get_runtime_message_cache(runtime)
        if request.message_id in seen:
            logger.debug(
                f"[Agent] mind.forward replay skipped message_id={request.message_id}"
            )
            return None

        seen.add(request.message_id)
        item = self.inbox.add(request)
        if self.context_callback is not None:
            self.context_callback(item, client, connection, runtime, live_status)


if __name__ == '__main__':
    pass
