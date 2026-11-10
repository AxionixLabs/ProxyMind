# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import platform
import contextlib
import time
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent.application import TurnApplication
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
    SubscriptionHost,
)
from infrastructure.config.runtime_paths import agent_runtime_db_path
from metadata import const
from observability import observe_exception
from protocol.transport.endpoints import service_endpoints
from protocol.schema.json_value import JsonValue
from protocol.schema.identifiers import (
    normalize_request_id,
    normalize_turn_id,
)
from .interaction import ApprovalControl, interaction_payload
from .wire import build_envelope
from .client import AgentClient
from .external_access import ConnectionState, publish_external_access
from .forwarding import (
    AgentExecutor,
    AgentInbox,
    EnvironmentSnapshotProvider,
    InboxForwardHandler,
    ReceiptDisposition,
    ReceiptDispositionResolver,
    RootTurnRunner,
)
from .loop import (
    AgentConnection,
    AgentSupervisor
)
from .models import (
    AgentConfig,
    AgentForwardRequest,
    AgentInboxItem,
    AgentLiveStatus,
    AgentSessionRuntime
)
from .opening import build_device_id
from .status import AgentStatusOutbox

InboxChangedCallback = typing.Callable[[], None]

AGENT_READY_WAIT_TIMEOUT_SEC: typing.Final[float] = 30.0

TurnApplicationFactory: typing.TypeAlias = Callable[
    [str | Path],
    TurnApplication[typing.Any],
]


@dataclass(slots=True)
class AgentRuntimeContext:
    """服务端请求执行所需的连接上下文。"""
    client: AgentClient
    connection: typing.Any
    runtime: AgentSessionRuntime
    live_status: AgentLiveStatus


def build_default_agent_config() -> AgentConfig:
    """构造默认订阅配置。"""
    return AgentConfig(
        base_url=service_endpoints.domain(),
        device_id=build_device_id(),
        agent_id=const.APP_NAME,
        client_version=const.APP_VERSION,
        platform=(platform.system().strip().lower() or "unknown"),
        arch=(platform.machine().strip().lower() or "unknown")
    )


class AgentRuntime(object):
    """面向交互入口的订阅运行时门面。"""

    def __init__(
        self,
        host: SubscriptionHost,
        *,
        protocol_client: ProtocolCommandClient,
        config: AgentConfig | None = None,
        client: AgentClient | None = None,
        inbox: AgentInbox | None = None,
        executor: AgentExecutor | None = None,
        live_status: AgentLiveStatus | None = None,
        supervisor: AgentSupervisor | None = None,
        configuration_service_url: Callable[[], str] | None = None,
        turn_runner: RootTurnRunner | None = None,
        environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
        turn_application_factory: TurnApplicationFactory | None = None,
        approvals: ApprovalControl | None = None,
    ) -> None:
        """装配订阅连接、收件箱和执行器。"""
        self.host = host

        self.config = config or build_default_agent_config()
        self.client = client or AgentClient(base_url=self.config.base_url)
        self.inbox = inbox or AgentInbox()
        self._executor_owned = executor is None

        self.executor = executor or self._build_default_executor(
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
            turn_application_factory=turn_application_factory,
        )
        self.live_status = live_status or AgentLiveStatus()
        self.status_outbox = AgentStatusOutbox()
        self.approvals = approvals
        self._protocol_client = protocol_client
        self._active_runs: dict[str, asyncio.Task[AgentInboxItem]] = {}
        self._interrupt_tasks: dict[str, asyncio.Task[bool]] = {}
        self._interaction_started: dict[str, float] = {}

        self.contexts: dict[str, AgentRuntimeContext] = {}

        self._inbox_changed: InboxChangedCallback | None = None

        self._receipt_disposition_resolver: ReceiptDispositionResolver | None = None

        self._ready: asyncio.Event = asyncio.Event()

        self.handler = InboxForwardHandler(
            self.inbox,
            self.remember_context,
            self._notify_inbox_changed,
            self._resolve_receipt_disposition,
            control_callback=self._handle_control,
        )
        self.connection = AgentConnection(
            host,
            self.client,
            self.config,
            self.live_status,
            self.handler,
            on_ready=self._mark_ready,
            on_connected=self._rebind_pending_contexts,
            on_disconnected=self._mark_disconnected,
            on_ack=self.status_outbox.acknowledge,
        )
        self.supervisor = supervisor or AgentSupervisor(
            host,
            self.connection,
            self.live_status,
        )
        self.task: asyncio.Task[None] | None = None
        self._configuration_service_url = configuration_service_url
        self._access_task: asyncio.Task[None] | None = None

    @staticmethod
    def _build_default_executor(
        *,
        turn_runner: RootTurnRunner | None,
        environment_snapshot_provider: EnvironmentSnapshotProvider | None,
        turn_application_factory: TurnApplicationFactory | None,
    ) -> AgentExecutor:
        """为生产订阅运行时组合持久 Turn application。"""
        if turn_application_factory is not None:
            application = turn_application_factory(agent_runtime_db_path())
            if not isinstance(application, TurnApplication):
                raise TypeError("turn application factory returned an invalid application")
            return AgentExecutor(
                turn_runner,
                turn_application=application,
                environment_snapshot_provider=environment_snapshot_provider,
            )
        return AgentExecutor(
            turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
        )

    def _resolve_receipt_disposition(self) -> ReceiptDisposition:
        """读取当前处理意图，未绑定交互策略时只入箱。"""
        resolver = self._receipt_disposition_resolver
        if resolver is None:
            return "queued"
        return resolver()

    def _notify_inbox_changed(self) -> None:
        """通知当前展示层重新读取收件箱快照。"""
        callback = self._inbox_changed
        if callback is None:
            return None
        try:
            callback()
        except Exception as error:
            observe_exception(
                "agent.inbox.notify_failed",
                error,
                level="WARNING",
            )

    def _mark_ready(self) -> None:
        """记录当前后台订阅任务已经完成服务端握手。"""
        if self.is_running():
            self._ready.set()
            self._notify_inbox_changed()

    def _mark_disconnected(self) -> None:
        """清除当前连接的握手状态并同步监听器展示。"""
        self.status_outbox.unbind()
        if self._ready.is_set():
            self._ready.clear()
            self._notify_inbox_changed()

    def _rebind_pending_contexts(
        self,
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        live_status: AgentLiveStatus,
    ) -> None:
        """把保留的待处理消息重新绑定到当前订阅连接。"""
        self.status_outbox.bind(client, connection, runtime)
        for item in self.inbox.items:
            self.remember_context(
                item,
                client,
                connection,
                runtime,
                live_status,
            )

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """取回后台监听任务结果，避免异常泄漏到事件循环。"""
        if not task.cancelled():
            task.exception()
        if self.task is task:
            self._ready.clear()
            self._notify_inbox_changed()

    def remember_context(
        self,
        item: AgentInboxItem,
        client: AgentClient,
        connection: typing.Any,
        runtime: AgentSessionRuntime,
        live_status: AgentLiveStatus
    ) -> None:
        """记录待处理请求的连接上下文。"""
        self.contexts[item.request.message_id] = AgentRuntimeContext(
            client=client,
            connection=connection,
            runtime=runtime,
            live_status=live_status
        )

    def bind_inbox_changed(
        self,
        callback: InboxChangedCallback | None
    ) -> None:
        """绑定收件箱快照变化通知，并立即同步当前状态。"""
        self._inbox_changed = callback
        self._notify_inbox_changed()

    def bind_receipt_disposition(
        self,
        resolver: ReceiptDispositionResolver | None
    ) -> None:
        """绑定收件回执所使用的处理意图解析器。"""
        self._receipt_disposition_resolver = resolver

    def is_running(self) -> bool:
        """判断后台订阅任务是否运行中。"""
        return self.task is not None and not self.task.done()

    @property
    def web_url(self) -> str | None:
        """读取当前订阅会话提供的浏览器入口。"""
        runtime = self.supervisor.runtime
        return runtime.web_url if runtime is not None else None

    def is_ready(self) -> bool:
        """判断后台订阅任务是否已经收到服务端 ready。"""
        return self.is_running() and self._ready.is_set()

    def status_label(self) -> str:
        """返回交互输入头可展示的订阅状态。"""
        if any(item.status == "running" for item in self.inbox.items):
            return "agent · running"

        pending_count = self.inbox.pending_count()
        if pending_count:
            return f"agent · {pending_count} pending"

        if self.is_ready():
            return "agent · online"

        if self.is_running():
            return "agent · reconnecting"

        return "agent · off"

    def start_background(self) -> asyncio.Task[None]:
        """启动后台订阅任务。"""
        if self.is_running():
            return self.task

        self._ready.clear()
        self.task = asyncio.create_task(
            self.supervisor.run(),
            name="agent-runtime"
        )
        self.task.add_done_callback(self._task_done)
        if self._configuration_service_url is not None and (self._access_task is None or self._access_task.done()):
            self._access_task = asyncio.create_task(self._observe_access(), name="agent-access-publisher")
        self._notify_inbox_changed()
        return self.task

    async def _observe_access(self) -> None:
        """持续发布连接状态及凭据，重试配置服务尚未就绪的写入。"""
        while True:
            state: ConnectionState
            if self.is_ready():
                state = "online"
            elif not self.is_running():
                state = "error"
            elif self.supervisor.runtime is None:
                state = "connecting"
            else:
                state = "reconnecting"
            await publish_external_access(self.supervisor.runtime,
                configuration_service_url=self._configuration_service_url, state=state)
            await asyncio.sleep(3)

    async def stop(self) -> None:
        """停止后台订阅任务。"""
        publisher = self._access_task
        self._access_task = None
        if publisher is not None:
            publisher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await publisher
            await publish_external_access(self.supervisor.runtime,
                configuration_service_url=self._configuration_service_url, state="stopped")
        task = self.task
        self.task = None
        self._ready.clear()
        if task is None or task.done():
            self._notify_inbox_changed()
            return None

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._notify_inbox_changed()

    async def run_next(self) -> AgentInboxItem | None:
        """执行最早的待处理请求。"""
        item = self.inbox.next_pending()
        if item is None:
            return None

        return await self.run_message(item.request.message_id)

    async def run_message(
        self,
        message_id: str,
        *,
        turn_id: str | None = None
    ) -> AgentInboxItem:
        """执行指定待处理请求，并在终态后释放进程内消息。"""
        item = self.inbox.find(message_id)
        if item is None:
            raise KeyError(message_id)
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {message_id}")

        def notify_execution_change() -> None:
            """同步可见终态，取消时由最终删除一次性刷新。"""
            if item.status != "pending":
                self._notify_inbox_changed()

        try:
            context = self.contexts.get(message_id)
            if context is None:
                raise RuntimeError("agent inbox item context missing")
            self._interaction_started[message_id] = time.time()
            operation = asyncio.create_task(self.inbox.accept(
                item,
                executor=self.executor,
                host=self.host,
                client=context.client,
                connection=context.connection,
                runtime=context.runtime,
                live_status=context.live_status,
                status_changed=notify_execution_change,
                turn_id=turn_id,
                status_outbox=self.status_outbox,
            ))
            self._active_runs[message_id] = operation
            observer = asyncio.create_task(self._observe_interaction(message_id))
            try:
                return await operation
            finally:
                interrupt = self._interrupt_tasks.pop(message_id, None)
                try:
                    if interrupt is not None:
                        interrupt.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await interrupt
                finally:
                    observer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await observer
                    await self._publish_interaction(message_id, clear=True)
        finally:
            self._active_runs.pop(message_id, None)
            self._interaction_started.pop(message_id, None)
            self.contexts.pop(message_id, None)
            if self.inbox.find(message_id) is not None:
                self.inbox.remove(message_id)
            self._notify_inbox_changed()

    async def _publish_interaction(self, message_id: str, *, clear: bool = False) -> None:
        """通过当前订阅连接发送可恢复的审批快照。"""
        context = self.contexts.get(message_id)
        item = self.inbox.find(message_id)
        if context is None or item is None:
            return
        payload = interaction_payload(item.request.call_id, self.approvals,
                                      since=self._interaction_started.get(message_id, time.time()), clear=clear)
        try:
            await context.client.send_json(context.connection, build_envelope(
                "mind.interaction", context.runtime.session_id, cid=item.request.cid,
                sid=item.request.sid, payload=payload,
            ))
        except Exception as error:
            observe_exception("agent.interaction.delivery_failed", error, level="DEBUG")

    async def _observe_interaction(self, message_id: str) -> None:
        """在任务期间持续同步审批状态以覆盖断线重连。"""
        while True:
            await self._publish_interaction(message_id)
            await asyncio.sleep(1)

    async def _handle_control(self, message: dict[str, JsonValue]) -> None:
        """只控制身份匹配且仍由当前进程拥有的订阅任务。"""
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        expires = payload.get("expires_at")
        if not isinstance(expires, (int, float)) or expires < time.time():
            return
        item = next((entry for entry in self.inbox.items if entry.request.call_id == payload.get("call_id")), None)
        if item is None or item.request.cid != message.get("cid") or item.request.sid != message.get("sid"):
            return
        message_id = item.request.message_id
        context = self.contexts.get(message_id)
        if context is None or context.runtime.session_id != message.get("session_id"):
            return
        if payload.get("action") == "cancel":
            task = self._active_runs.get(message_id)
            if task is not None and not task.done():
                request_id = payload.get("request_id")
                turn_id = item.request.payload.get("turn_id")
                if not isinstance(request_id, str) or not isinstance(turn_id, str):
                    return
                try:
                    request_id = normalize_request_id(request_id)
                    turn_id = normalize_turn_id(turn_id)
                except ValueError:
                    return
                interrupt = self._interrupt_tasks.get(message_id)
                if interrupt is not None and (not interrupt.done() or interrupt.result()):
                    return
                self._interrupt_tasks[message_id] = asyncio.create_task(
                    self._interrupt_remote_turn(
                        self._protocol_client, item.request, task,
                        turn_id=turn_id, request_id=request_id,
                    ),
                    name=f"agent-interrupt:{message_id}",
                )
            elif item.status == "pending":
                await self.discard(message_id)
        elif payload.get("action") == "approval" and message_id in self._active_runs and self.approvals is not None:
            current = self.approvals.snapshot.current
            decision = payload.get("decision")
            approval = interaction_payload(item.request.call_id, self.approvals).get("approval")
            if (current is not None and current.key.request_id == payload.get("approval_id")
                    and isinstance(decision, str) and isinstance(approval, dict)
                    and isinstance(approval.get("decisions"), list) and decision in approval["decisions"]):
                try:
                    await self.approvals.resolve(current.key.request_id, decision, source="user")
                except ValueError:
                    return
                await self._publish_interaction(message_id)

    @staticmethod
    async def _interrupt_remote_turn(
        protocol_client: ProtocolCommandClient,
        request: AgentForwardRequest,
        operation: asyncio.Task[AgentInboxItem],
        *,
        turn_id: str,
        request_id: str,
    ) -> bool:
        """提交幂等中断并吸收创建竞态；原执行任务继续观察权威终态。"""
        retry_delay = 0.25
        while not operation.done():
            try:
                await protocol_client.interrupt_turn(
                    cid=request.cid,
                    sid=request.sid,
                    turn_id=turn_id,
                    request_id=request_id,
                )
                return True
            except ProtocolCommandError as error:
                if operation.done():
                    return True
                creation_pending = error.code == "turn_not_found" and error.status_code == 404
                if not creation_pending and not error.retryable:
                    observe_exception(
                        "agent.interrupt.failed", error,
                        call_id=request.call_id, turn_id=turn_id, request_id=request_id,
                    )
                    return False
                await asyncio.sleep(retry_delay)
                retry_delay = min(2.0, retry_delay * 2)
        return True

    async def wait_until_ready(
        self,
        *,
        timeout_sec: float = AGENT_READY_WAIT_TIMEOUT_SEC
    ) -> None:
        """等待当前后台订阅任务收到 ready，或传播其提前退出原因。"""
        if self.is_ready():
            return None

        task = self.task
        if task is None:
            raise RuntimeError("listener is not running")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout_sec))

        while True:
            if self.is_ready():
                return None
            if task.done():
                await asyncio.shield(task)
                raise RuntimeError("listener stopped before server ready")

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"listener was not ready within {timeout_sec:g}s"
                )

            ready_wait = asyncio.create_task(
                self._ready.wait(),
                name="agent-runtime-ready",
            )
            try:
                await asyncio.wait(
                    (ready_wait, task),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=remaining,
                )
            finally:
                if not ready_wait.done():
                    ready_wait.cancel()
                    await asyncio.gather(ready_wait, return_exceptions=True)

    async def discard(self, message_id: str) -> AgentInboxItem:
        """取消服务端任务并从当前进程中删除待处理请求。"""
        item = self.inbox.find(message_id)
        if item is None:
            raise KeyError(message_id)
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {message_id}")
        context = self.contexts.get(message_id)
        if context is None:
            raise RuntimeError("agent inbox item context missing")
        await self.status_outbox.cancelled(
            item.request,
            session_id=context.runtime.session_id,
            reason="message_deleted",
        )
        self.contexts.pop(message_id, None)
        removed = self.inbox.remove(message_id)
        self._notify_inbox_changed()
        return removed

    async def shutdown(self) -> None:
        """取消尚未处理的远端任务并停止订阅运行时。"""
        try:
            for item in list(self.inbox.pending_items()):
                context = self.contexts.get(item.request.message_id)
                if context is None:
                    continue
                await self.status_outbox.cancelled(
                    item.request,
                    session_id=context.runtime.session_id,
                    reason="client_shutdown",
                )
        finally:
            try:
                await self.stop()
            finally:
                if self._executor_owned:
                    await self.executor.close()


if __name__ == '__main__':
    pass
