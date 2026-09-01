# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import functools
from collections.abc import (
    Callable,
    Mapping,
)

from agent.adapters.turns.root import RootTurnCommandExecutor
from agent.application.turns.commands import (
    SubmitTurnCommand,
    SubmitTurnResult,
    TurnApplication,
)
from agent.application.turns.run_result import RunResult
from agent.ports import SubscriptionHost
from infrastructure.errors import AppError
from observability import observe
from .client import AgentClient
from .models import (
    AgentInboxItem,
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime
)
from .status import AgentStatusOutbox


class RootTurnRunner(typing.Protocol):
    """定义组合根提供的根轮次执行能力。"""

    async def __call__(
        self,
        host: SubscriptionHost,
        pref_config: dict[str, typing.Any] | None = None,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """执行一次已冻结的根轮次。"""
        ...


EnvironmentSnapshotProvider = Callable[
    [SubscriptionHost],
    dict[str, typing.Any] | None,
]


def capture_active_turn_environment(
    _host: SubscriptionHost,
) -> dict[str, typing.Any] | None:
    """在缺少组合根环境能力时返回明确配置错误。"""
    raise RuntimeError("subscription environment snapshot provider is required")


async def run_root_turn(
    _host: SubscriptionHost,
    _pref_config: dict[str, typing.Any] | None = None,
    *,
    message: str,
    **_kwargs: typing.Any,
) -> RunResult:
    """在缺少组合根根轮次能力时返回明确配置错误。"""
    _ = message
    raise RuntimeError("subscription root turn runner is required")


class AgentForwardHandler(typing.Protocol):
    """处理一条服务端 forward 请求。"""

    async def handle(
        self,
        mind: SubscriptionHost,
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

    def __init__(
        self,
        turn_runner: RootTurnRunner | None = None,
        *,
        turn_application: TurnApplication[RunResult] | None = None,
        environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
    ) -> None:
        """绑定组合根提供的根轮次和环境能力。"""
        self._turn_runner = turn_runner or run_root_turn
        self._environment_snapshot_provider = (
            environment_snapshot_provider or capture_active_turn_environment
        )
        self._turn_application = turn_application

    async def close(self) -> None:
        """关闭由订阅执行器持有的长生命周期 Turn application。"""
        if self._turn_application is not None:
            await self._turn_application.close(cancel_running=True)

    async def execute(
        self,
        mind: SubscriptionHost,
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        live_status: AgentLiveStatus | None = None,
        *,
        turn_id: str | None = None,
        status_outbox: AgentStatusOutbox | None = None
    ) -> None:
        """执行一条服务端下发的本地任务。"""
        message, intent_summary = normalize_forward_request(request.payload)

        timeout_sec = resolve_forward_timeout_sec(request.payload)
        metadata_raw = request.payload.get("metadata")
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

        try:
            await client.send_mind_started(
                connection,
                session_id=runtime.session_id,
                cid=request.cid,
                sid=request.sid,
                call_id=request.call_id
            )

            environment_snapshot = self._environment_snapshot_provider(mind)
            command = self._build_command(
                request,
                message=message,
                metadata=metadata,
                timeout_sec=timeout_sec,
                turn_id=turn_id,
                environment_snapshot=environment_snapshot,
            )

            execute_root_turn = RootTurnCommandExecutor(
                functools.partial(self._turn_runner, mind),
            )

            execution: SubmitTurnResult[RunResult]
            if self._turn_application is None:
                raise RuntimeError("turn application is required")
            submitted = self._turn_application.submit(
                command,
                execute_root_turn,
            )

            if timeout_sec is not None:
                execution = await asyncio.wait_for(
                    submitted,
                    timeout=timeout_sec,
                )
            else:
                execution = await submitted

            result = execution.value
            projected_status = execution.projection.status

            if projected_status == "interrupted":
                await self._send_cancelled(
                    client,
                    connection,
                    runtime,
                    request,
                    status_outbox=status_outbox,
                    reason="user_interrupted",
                )
                return None

            if projected_status != "completed":
                raise AppError(result.error or f"run {projected_status}")

            if status_outbox is not None:
                await status_outbox.completed(request, session_id=runtime.session_id)
            else:
                await client.send_mind_completed(
                    connection,
                    session_id=runtime.session_id,
                    cid=request.cid,
                    sid=request.sid,
                    call_id=request.call_id
                )
        except asyncio.CancelledError:
            await asyncio.shield(self._send_cancelled(
                client,
                connection,
                runtime,
                request,
                status_outbox=status_outbox,
                reason="user_interrupted",
            ))
            raise
        except Exception as error:
            if status_outbox is not None:
                await status_outbox.failed(
                    request,
                    session_id=runtime.session_id,
                    error=error,
                )
            else:
                await client.send_mind_failed(
                    connection,
                    session_id=runtime.session_id,
                    cid=request.cid,
                    sid=request.sid,
                    call_id=request.call_id,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
            raise

        observe(
            "agent.forward.complete",
            call_id=request.call_id,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

    @staticmethod
    def _build_command(
        request: AgentForwardRequest,
        *,
        message: str,
        metadata: Mapping[str, typing.Any],
        timeout_sec: float | None,
        turn_id: str | None,
        environment_snapshot: Mapping[str, typing.Any] | None,
    ) -> SubmitTurnCommand:
        """把订阅请求冻结为可恢复的主动 Turn 命令。"""
        payload_extras = request.payload.get("extras")
        if payload_extras is not None and not isinstance(payload_extras, Mapping):
            raise ValueError("mind.forward payload.extras must be an object")

        raw_attachments = request.payload.get("attachments", ())
        if raw_attachments in (None, ""):
            raw_attachments = ()
        if not isinstance(raw_attachments, (tuple, list)):
            raise ValueError("mind.forward payload.attachments must be a sequence")
        attachments: list[Mapping[str, typing.Any]] = []
        for attachment in raw_attachments:
            if not isinstance(attachment, Mapping):
                raise ValueError(
                    "mind.forward payload.attachments entries must be objects"
                )
            attachments.append(dict(attachment))

        command_extras: dict[str, typing.Any] = {
            "cid": request.cid,
            "sid": request.sid,
            "call_id": request.call_id,
            "message_id": request.message_id,
            "metadata": dict(metadata),
        }
        if payload_extras:
            command_extras["request_extras"] = dict(payload_extras)
        if timeout_sec is not None:
            command_extras["timeout_sec"] = timeout_sec
        if turn_id:
            command_extras["turn_id"] = turn_id

        return SubmitTurnCommand.create(
            command_id=f"agent-forward:{request.message_id}",
            idempotency_key=f"agent-forward:{request.message_id}",
            run_id=f"agent-forward:{request.sid}:{request.call_id}",
            session_id=request.sid,
            message=message,
            attachments=attachments,
            environment_snapshot=environment_snapshot,
            extras=command_extras,
        )

    @staticmethod
    async def _send_cancelled(
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        request: AgentForwardRequest,
        *,
        status_outbox: AgentStatusOutbox | None,
        reason: typing.Literal[
            "user_interrupted",
            "message_deleted",
            "client_shutdown"
        ]
    ) -> None:
        if status_outbox is not None:
            await status_outbox.cancelled(
                request,
                session_id=runtime.session_id,
                reason=reason,
            )
            return None
        await client.send_mind_cancelled(
            connection,
            session_id=runtime.session_id,
            cid=request.cid,
            sid=request.sid,
            call_id=request.call_id,
            reason=reason,
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

    def find(self, message_id: str) -> AgentInboxItem | None:
        """按消息标识查找请求。"""
        for item in self.items:
            if item.request.message_id == message_id:
                return item
        return None

    def remove(self, message_id: str) -> AgentInboxItem:
        """从当前进程中移除一条收件箱消息。"""
        item = self.find(message_id)
        if item is None:
            raise KeyError(message_id)
        self.items.remove(item)
        return item

    def pending_items(self) -> list[AgentInboxItem]:
        """返回待处理请求列表。"""
        return [item for item in self.items if item.status == "pending"]

    def pending_count(self) -> int:
        """返回待处理请求数量。"""
        return len(self.pending_items())

    def next_pending(self) -> AgentInboxItem | None:
        """返回最早的待处理请求。"""
        pending = self.pending_items()
        return pending[0] if pending else None

    @staticmethod
    async def accept(
        item: AgentInboxItem,
        *,
        executor: AgentExecutor,
        mind: SubscriptionHost,
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        live_status: AgentLiveStatus,
        status_changed: typing.Callable[[], None] | None = None,
        turn_id: str | None = None,
        status_outbox: AgentStatusOutbox | None = None
    ) -> AgentInboxItem:
        """执行一条待处理请求并调整收件箱状态。"""
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {item.request.message_id}")

        item.status = "running"
        if status_changed is not None:
            status_changed()
        try:
            await executor.execute(
                mind,
                client,
                connection,
                runtime,
                item.request,
                live_status,
                turn_id=turn_id,
                status_outbox=status_outbox,
            )
        except asyncio.CancelledError:
            item.status = "pending"
            if status_changed is not None:
                status_changed()
            raise
        except Exception as exc:
            item.status = "failed"
            item.error = f"{type(exc).__name__}: {exc}"
            if status_changed is not None:
                status_changed()
            raise

        item.status = "completed"
        item.error = None

        if status_changed is not None:
            status_changed()
        return item

    async def accept_next(
        self,
        *,
        executor: AgentExecutor,
        mind: SubscriptionHost,
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

InboxChangedCallback = typing.Callable[[], None]

ReceiptDisposition = typing.Literal["queued", "auto_run"]

ReceiptDispositionResolver = typing.Callable[[], ReceiptDisposition]


class InboxForwardHandler(object):
    """收到服务端请求后放入本地收件箱。"""

    def __init__(
        self,
        inbox: AgentInbox,
        context_callback: InboxContextCallback | None = None,
        changed_callback: InboxChangedCallback | None = None,
        disposition_resolver: ReceiptDispositionResolver | None = None
    ) -> None:
        """保存服务端请求收件箱。"""
        self.inbox = inbox
        self.context_callback = context_callback
        self.changed_callback = changed_callback
        self.disposition_resolver = disposition_resolver

    def _receipt_disposition(self) -> ReceiptDisposition:
        """返回当前请求入箱后的处理意图。"""
        resolver = self.disposition_resolver
        if resolver is None:
            return "queued"
        return resolver()

    async def handle(
        self,
        mind: SubscriptionHost,
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
        disposition = self._receipt_disposition()

        seen = get_runtime_message_cache(runtime)
        if request.message_id in seen:
            await client.send_mind_received(
                connection,
                session_id=runtime.session_id,
                cid=request.cid,
                sid=request.sid,
                call_id=request.call_id,
                acked_message_id=request.message_id,
                disposition=disposition,
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

        try:
            await client.send_mind_received(
                connection,
                session_id=runtime.session_id,
                cid=request.cid,
                sid=request.sid,
                call_id=request.call_id,
                acked_message_id=request.message_id,
                disposition=disposition,
            )
        finally:
            if self.changed_callback is not None:
                self.changed_callback()
        observe(
            "agent.forward.received",
            call_id=request.call_id,
            message_id=request.message_id,
            disposition=disposition,
        )


if __name__ == '__main__':
    pass
