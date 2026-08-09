# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import platform
from dataclasses import dataclass
from ..runtime.agent.client import AgentClient
from .forwarding import (
    AgentExecutor,
    AgentInbox,
    InboxForwardHandler
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

        self.handler = InboxForwardHandler(
            self.inbox,
            self.remember_context
        )
        self.connection = AgentConnection(
            mind,
            self.client,
            self.config,
            self.live_status,
            self.handler
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

    def is_running(self) -> bool:
        """判断后台订阅任务是否运行中。"""
        return self.task is not None and not self.task.done()

    def start_background(self) -> asyncio.Task[None]:
        """启动后台订阅任务。"""
        if self.is_running():
            return self.task

        self.task = asyncio.create_task(
            self.supervisor.run(),
            name="agent-runtime"
        )
        self.task.add_done_callback(self._task_done)
        return self.task

    @staticmethod
    def _task_done(task: asyncio.Task[None]) -> None:
        """取回后台监听任务结果，避免异常泄漏到事件循环。"""
        if not task.cancelled():
            task.exception()

    async def stop(self) -> None:
        """停止后台订阅任务。"""
        task = self.task
        self.task = None
        if task is None or task.done():
            return None

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return None

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

        context = self.contexts.get(item.request.message_id)
        if context is None:
            raise RuntimeError("agent inbox item context missing")

        return await self.inbox.accept(
            item,
            executor=self.executor,
            mind=self.mind,
            client=context.client,
            connection=context.connection,
            runtime=context.runtime,
            live_status=context.live_status
        )

    def decline(self, message_id: str, *, reason: str = "") -> AgentInboxItem:
        """拒绝一条待处理请求。"""
        item = self.inbox.decline(message_id, reason=reason)
        self.contexts.pop(message_id, None)
        return item


if __name__ == '__main__':
    pass
