# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.protocol import (
    LocalDurableQueueSnapshot,
    ModelStreamRequest,
    SubmitTurnCommand,
)


class DurableQueuePersistenceConflict(ValueError):
    """表示本地 Queue 执行快照与既有持久事实冲突。"""


@typing.runtime_checkable
class DurableQueuePersistence(typing.Protocol):
    """持久化 Queue 命令的本地执行快照和不确定请求身份。

    实现方不得把本地状态作为远端队列顺序或生命周期真相；这些记录只用于恢复
    冻结执行语义，以及在网络结果未知时复用同一个幂等请求。
    """

    async def create(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
        *,
        submission_id: str,
        client_message_id: str,
        add_request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """在远端 add 前持久化不可变命令身份和完整执行快照。"""
        ...

    async def find(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot | None:
        """按 submission identity 读取本地执行快照。"""
        ...

    async def list_session(
        self,
        *,
        cid: str,
        sid: str,
    ) -> tuple[LocalDurableQueueSnapshot, ...]:
        """读取 Session 下仍保留的本地 Queue 执行快照。"""
        ...

    async def mark_queued(
        self,
        submission_id: str,
        *,
        add_request_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """在远端 add receipt 或快照确认后标记已排队。"""
        ...

    async def begin_start(
        self,
        submission_id: str,
        *,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """在远端 start 前持久化本次尝试的幂等身份。"""
        ...

    async def mark_started(
        self,
        submission_id: str,
        *,
        request_id: str,
        turn_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """在权威 start receipt 或 Turn 状态确认后标记已启动。"""
        ...

    async def reset_start(
        self,
        submission_id: str,
        *,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """在服务端确定拒绝 start 后允许新的显式尝试。"""
        ...

    async def mark_deleted(
        self,
        submission_id: str,
        *,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """在权威快照或 delete receipt 确认后标记已删除。"""
        ...


if __name__ == '__main__':
    pass
