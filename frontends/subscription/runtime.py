# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import platform
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent.application import TurnApplication
from agent.ports import SubscriptionHost
from infrastructure.config.runtime_paths import agent_runtime_db_path
from metadata import const
from observability import observe_exception
from protocol.transport.endpoints import service_endpoints
from .client import AgentClient
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
        mind: SubscriptionHost,
        *,
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
    ) -> None:
        """装配订阅连接、收件箱和执行器。"""
        self.mind = mind

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

        self.contexts: dict[str, AgentRuntimeContext] = {}

        self._inbox_changed: InboxChangedCallback | None = None

        self._receipt_disposition_resolver: ReceiptDispositionResolver | None = None

        self._ready: asyncio.Event = asyncio.Event()

        self.handler = InboxForwardHandler(
            self.inbox,
            self.remember_context,
            self._notify_inbox_changed,
            self._resolve_receipt_disposition,
        )
        self.connection = AgentConnection(
            mind,
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
            mind,
            self.connection,
            self.live_status,
            configuration_service_url=configuration_service_url,
        )
        self.task: asyncio.Task[None] | None = None

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
        for item in self.inbox.pending_items():
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
        self._notify_inbox_changed()
        return self.task

    async def stop(self) -> None:
        """停止后台订阅任务。"""
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
            return await self.inbox.accept(
                item,
                executor=self.executor,
                mind=self.mind,
                client=context.client,
                connection=context.connection,
                runtime=context.runtime,
                live_status=context.live_status,
                status_changed=notify_execution_change,
                turn_id=turn_id,
                status_outbox=self.status_outbox,
            )
        finally:
            self.contexts.pop(message_id, None)
            if self.inbox.find(message_id) is not None:
                self.inbox.remove(message_id)
            self._notify_inbox_changed()

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
