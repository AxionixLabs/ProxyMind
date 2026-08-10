# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import platform
from dataclasses import dataclass
from engine.observability import observe_exception
from ..runtime.agent.client import AgentClient
from .forwarding import (
    AgentExecutor,
    AgentInbox,
    InboxForwardHandler,
    ReceiptDisposition,
    ReceiptDispositionResolver
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
from mind_nova import const
from mind_nova.services import service_endpoints

if typing.TYPE_CHECKING:
    from ..controller import Mind


InboxChangedCallback = typing.Callable[[], None]

AGENT_READY_WAIT_TIMEOUT_SEC: typing.Final[float] = 30.0


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
        mind: "Mind",
        *,
        config: AgentConfig | None = None,
        client: AgentClient | None = None,
        inbox: AgentInbox | None = None,
        executor: AgentExecutor | None = None,
        live_status: AgentLiveStatus | None = None,
        supervisor: AgentSupervisor | None = None
    ) -> None:
        """装配订阅连接、收件箱和执行器。"""
        self.mind = mind

        self.config      = config or build_default_agent_config()
        self.client      = client or AgentClient(base_url=self.config.base_url)
        self.inbox       = inbox or AgentInbox()
        self.executor    = executor or AgentExecutor()
        self.live_status = live_status or AgentLiveStatus()

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
        )
        self.supervisor = supervisor or AgentSupervisor(
            mind,
            self.connection,
            self.live_status
        )
        self.task: asyncio.Task[None] | None = None

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

    def is_running(self) -> bool:
        """判断后台订阅任务是否运行中。"""
        return self.task is not None and not self.task.done()

    def is_ready(self) -> bool:
        """判断后台订阅任务是否已经收到服务端 ready。"""
        return self.is_running() and self._ready.is_set()

    def _mark_ready(self) -> None:
        """记录当前后台订阅任务已经完成服务端握手。"""
        if self.is_running():
            self._ready.set()
            self._notify_inbox_changed()

    def _mark_disconnected(self) -> None:
        """清除当前连接的握手状态并同步监听器展示。"""
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
        for item in self.inbox.pending_items():
            self.remember_context(
                item,
                client,
                connection,
                runtime,
                live_status,
            )

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

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """取回后台监听任务结果，避免异常泄漏到事件循环。"""
        if not task.cancelled():
            task.exception()
        if self.task is task:
            self._ready.clear()
            self._notify_inbox_changed()

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

    def status_label(self) -> str:
        """返回交互输入头可展示的订阅状态。"""
        if any(item.status == "running" for item in self.inbox.items):
            return "agent · running"

        pending_count = self.inbox.pending_count()
        if pending_count:
            return f"agent · {pending_count} pending"

        if self.is_running():
            return "agent · online"

        return "agent · off"

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
            )
        finally:
            self.contexts.pop(message_id, None)
            if self.inbox.find(message_id) is not None:
                self.inbox.remove(message_id)
            self._notify_inbox_changed()

    def discard(self, message_id: str) -> AgentInboxItem:
        """仅从当前进程中删除一条待处理请求。"""
        item = self.inbox.find(message_id)
        if item is None:
            raise KeyError(message_id)
        if item.status != "pending":
            raise ValueError(f"agent inbox item is not pending: {message_id}")
        self.contexts.pop(message_id, None)
        removed = self.inbox.remove(message_id)
        self._notify_inbox_changed()
        return removed

    def decline(self, message_id: str, *, reason: str = "") -> AgentInboxItem:
        """拒绝一条待处理请求。"""
        item = self.inbox.decline(message_id, reason=reason)
        self.contexts.pop(message_id, None)
        self._notify_inbox_changed()
        return item


if __name__ == '__main__':
    pass
