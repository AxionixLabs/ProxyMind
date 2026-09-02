# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from collections.abc import (
    Callable,
    Mapping,
)

from .conversation import RootConversationPort
from .process_lifecycle import ProcessLifecyclePort


class SubscriptionRequest(typing.Protocol):
    """定义前端收件箱请求向展示层暴露的稳定字段。"""

    message_id: str
    call_id: str
    cid: str
    sid: str
    payload: Mapping[str, typing.Any]


class SubscriptionInboxItem(typing.Protocol):
    """定义收件箱条目的只读展示字段。"""

    request: SubscriptionRequest
    status: str


class SubscriptionInbox(typing.Protocol):
    """定义 TUI 读取订阅收件箱所需的最小端口。"""

    def pending_count(self) -> int:
        """返回尚未处理的请求数量。"""
        ...

    def pending_items(self) -> list[SubscriptionInboxItem]:
        """返回尚未处理的请求快照。"""
        ...

    def next_pending(self) -> SubscriptionInboxItem | None:
        """返回最早的待处理请求。"""
        ...

    def find(self, message_id: str) -> SubscriptionInboxItem | None:
        """按消息标识查找收件箱条目。"""
        ...


class SubscriptionRuntime(typing.Protocol):
    """定义 Harness 管理订阅生命周期及 TUI 收件箱交互的端口。"""

    inbox: SubscriptionInbox

    def start_background(self) -> asyncio.Task[None]:
        """启动后台订阅连接。"""
        ...

    async def wait_until_ready(self, *, timeout_sec: float = 30.0) -> None:
        """等待订阅连接完成服务端握手。"""
        ...

    async def stop(self) -> None:
        """暂停连接但保留内存收件箱。"""
        ...

    async def shutdown(self) -> None:
        """关闭连接并释放订阅执行资源。"""
        ...

    def is_running(self) -> bool:
        """返回后台订阅任务是否运行。"""
        ...

    def is_ready(self) -> bool:
        """返回订阅握手是否完成。"""
        ...

    def bind_inbox_changed(self, callback: Callable[[], None] | None) -> None:
        """绑定收件箱变化通知。"""
        ...

    def bind_receipt_disposition(
        self,
        resolver: Callable[[], typing.Literal["queued", "auto_run"]] | None,
    ) -> None:
        """绑定收件回执处理意图解析器。"""
        ...

    async def discard(self, message_id: str) -> SubscriptionInboxItem:
        """取消并移除指定待处理请求。"""
        ...


class SubscriptionHost(typing.Protocol):
    """定义组合订阅运行时所需的宿主最小生命周期能力。"""

    conversation: RootConversationPort
    lifecycle: ProcessLifecyclePort


if __name__ == "__main__":
    pass
