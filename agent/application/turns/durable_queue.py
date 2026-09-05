# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.turns.context import TurnContext
from agent.ports import (
    DurableQueueClient,
    DurableQueuePersistence,
    ProtocolCommandClient,
    ProtocolCommandError,
    TurnInputEventHandler,
)
from agent.protocol import (
    DurableQueueItem,
    DurableQueueMutationReceipt,
    DurableQueueReorderReceipt,
    DurableQueueStartReceipt,
    LocalDurableQueueSnapshot,
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol import ModelStreamEndReason
from protocol.schema.identifiers import (
    new_request_id,
    new_submission_id,
    stable_request_id,
)


@dataclass(frozen=True, slots=True)
class DurableQueueEntry:
    """组合服务端 Queue item 与可选的本地冻结执行快照。"""

    item: DurableQueueItem
    local: LocalDurableQueueSnapshot | None

    @property
    def executable(self) -> bool:
        """返回当前进程是否具备安全观察该项所需的冻结语义。"""
        return self.local is not None and self.local.status in {
            "queued",
            "starting",
        }


@dataclass(frozen=True, slots=True)
class DurableQueueSessionSnapshot:
    """描述一次已与本地执行账本对齐的服务端 Queue 快照。"""

    cid: str
    sid: str
    queue_version: int
    entries: tuple[DurableQueueEntry, ...]
    local_only: tuple[LocalDurableQueueSnapshot, ...]


@dataclass(frozen=True, slots=True)
class DurableQueueSubmissionResult:
    """描述 add receipt 与对应的本地恢复快照。"""

    receipt: DurableQueueMutationReceipt
    local: LocalDurableQueueSnapshot


@dataclass(frozen=True, slots=True)
class DurableQueueStartResult:
    """描述 Queue item 到既有 Turn 的确定绑定。"""

    local: LocalDurableQueueSnapshot
    receipt: DurableQueueStartReceipt | None


@dataclass(frozen=True, slots=True)
class DurableQueueTurnCallbacks:
    """保存 Queue Turn 观察期间由前端提供的生命周期回调。"""

    input_context: typing.Callable[[TurnContext], None] | None = None
    input_event: TurnInputEventHandler | None = None
    stream_end: typing.Callable[[ModelStreamEndReason], None] | None = None
    interrupted: typing.Callable[[], None] | None = None


class DurableQueueApplication:
    """协调显式 Durable Queue 命令与客户端冻结执行账本。

    服务端 snapshot/receipt 始终是队列真相。本用例只在网络命令前保存恢复所需的
    本地事实，并确保未知结果重试复用原 request id；它不自动消费普通 TUI 输入。
    """

    def __init__(
        self,
        client: DurableQueueClient,
        protocol_client: ProtocolCommandClient,
        persistence: DurableQueuePersistence,
    ) -> None:
        """绑定服务端 Queue/Turn 能力与本地执行账本。"""
        if not isinstance(client, DurableQueueClient):
            raise TypeError("durable queue client is required")
        if not isinstance(protocol_client, ProtocolCommandClient):
            raise TypeError("protocol command client is required")
        if not isinstance(persistence, DurableQueuePersistence):
            raise TypeError("durable queue persistence is required")
        self._client = client
        self._protocol_client = protocol_client
        self._persistence = persistence

    async def enqueue(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
        *,
        submission_id: str | None = None,
        client_message_id: str | None = None,
        request_id: str | None = None,
    ) -> DurableQueueSubmissionResult:
        """先持久化本地执行快照，再幂等提交服务端 queue.add。"""
        resolved_submission_id = submission_id or new_submission_id()
        resolved_client_message_id = (
            str(client_message_id or "").strip()
            or f"message_{resolved_submission_id}"
        )
        resolved_request_id = request_id or new_request_id("queue_add")
        local = await self._persistence.create(
            command,
            request,
            submission_id=resolved_submission_id,
            client_message_id=resolved_client_message_id,
            add_request_id=resolved_request_id,
        )
        try:
            receipt = await self._client.add_queue_submission(
                local.request,
                submission_id=local.submission_id,
                client_message_id=local.client_message_id,
                request_id=local.add_request_id,
            )
        except ProtocolCommandError as error:
            if _definitely_not_committed(error):
                await self._persistence.mark_deleted(
                    local.submission_id,
                    queue_version=0,
                )
            raise
        _validate_add_receipt(local, receipt)
        queued = await self._persistence.mark_queued(
            local.submission_id,
            add_request_id=local.add_request_id,
            queue_version=receipt.queue_version,
        )
        return DurableQueueSubmissionResult(receipt=receipt, local=queued)

    async def retry_add(
        self,
        submission_id: str,
    ) -> DurableQueueSubmissionResult:
        """使用原冻结请求和 request id 对账一次未知 add 结果。"""
        local = await self._require_local(submission_id)
        if local.status not in {"adding", "queued"}:
            raise ValueError("durable queue item is not awaiting add confirmation")
        receipt = await self._client.add_queue_submission(
            local.request,
            submission_id=local.submission_id,
            client_message_id=local.client_message_id,
            request_id=local.add_request_id,
        )
        _validate_add_receipt(local, receipt)
        queued = await self._persistence.mark_queued(
            local.submission_id,
            add_request_id=local.add_request_id,
            queue_version=receipt.queue_version,
        )
        return DurableQueueSubmissionResult(receipt=receipt, local=queued)

    async def snapshot(
        self,
        *,
        cid: str,
        sid: str,
    ) -> DurableQueueSessionSnapshot:
        """读取远端权威快照，并只提交能够确定的本地正向事实。"""
        remote = await self._client.list_queue_submissions(cid=cid, sid=sid)
        local_items = await self._persistence.list_session(cid=cid, sid=sid)
        local_by_id = {item.submission_id: item for item in local_items}
        entries: list[DurableQueueEntry] = []
        remote_ids: set[str] = set()
        for item in remote.items:
            remote_ids.add(item.submission_id)
            local = local_by_id.get(item.submission_id)
            if local is not None:
                _validate_remote_item(local, item)
                if local.status in {"adding", "queued"}:
                    local = await self._persistence.mark_queued(
                        local.submission_id,
                        add_request_id=local.add_request_id,
                        queue_version=remote.queue_version,
                    )
            entries.append(DurableQueueEntry(item=item, local=local))

        local_only = tuple(
            item
            for item in local_items
            if item.submission_id not in remote_ids
            and item.status not in {"deleted", "settled"}
        )
        return DurableQueueSessionSnapshot(
            cid=remote.cid,
            sid=remote.sid,
            queue_version=remote.queue_version,
            entries=tuple(entries),
            local_only=local_only,
        )

    async def reconcile(
        self,
        *,
        cid: str,
        sid: str,
    ) -> DurableQueueSessionSnapshot:
        """以一次 Queue 快照和有界 Turn 查询收敛冷恢复的不确定项。"""
        snapshot = await self.snapshot(cid=cid, sid=sid)
        for local in snapshot.local_only:
            if local.status == "adding":
                continue
            if local.status == "started":
                continue
            try:
                await self._protocol_client.get_turn_status(
                    cid=local.request.cid,
                    sid=local.request.sid,
                    turn_id=local.request.turn_id,
                )
            except ProtocolCommandError as error:
                if error.status_code != 404:
                    continue
                await self._persistence.mark_deleted(
                    local.submission_id,
                    queue_version=snapshot.queue_version,
                )
                continue

            start_request_id = local.start_request_id
            if start_request_id is None:
                start_request_id = stable_request_id(
                    "queue_recovered_start",
                    local.submission_id,
                    local.request.turn_id,
                )
                local = await self._persistence.begin_start(
                    local.submission_id,
                    request_id=start_request_id,
                )
            await self._persistence.mark_started(
                local.submission_id,
                request_id=start_request_id,
                turn_id=local.request.turn_id,
                queue_version=snapshot.queue_version,
            )
        return await self.snapshot(cid=cid, sid=sid)

    async def delete(
        self,
        *,
        cid: str,
        sid: str,
        submission_id: str,
        request_id: str | None = None,
    ) -> DurableQueueMutationReceipt:
        """删除远端 Queue item，并在 receipt 后更新本地投影。"""
        receipt = await self._client.delete_queue_submission(
            cid=cid,
            sid=sid,
            submission_id=submission_id,
            request_id=request_id or new_request_id("queue_delete"),
        )
        local = await self._persistence.find(submission_id)
        if local is not None:
            await self._persistence.mark_deleted(
                submission_id,
                queue_version=receipt.queue_version,
            )
        return receipt

    async def reorder(
        self,
        *,
        cid: str,
        sid: str,
        submission_ids: typing.Sequence[str],
        request_id: str | None = None,
    ) -> DurableQueueReorderReceipt:
        """把完整新顺序提交服务端，不建立本地顺序真相。"""
        return await self._client.reorder_queue_submissions(
            cid=cid,
            sid=sid,
            submission_ids=submission_ids,
            request_id=request_id or new_request_id("queue_reorder"),
        )

    async def start(
        self,
        *,
        cid: str,
        sid: str,
        submission_id: str,
        request_id: str | None = None,
    ) -> DurableQueueStartResult:
        """持久化 start 意图并原子启动队首，未知结果保持待对账。"""
        local = await self._require_local(submission_id)
        if local.request.cid != cid or local.request.sid != sid:
            raise ValueError("durable queue item does not belong to session")
        if local.status == "started":
            return DurableQueueStartResult(local=local, receipt=None)
        if local.status == "starting":
            resolved_request_id = local.start_request_id
            if resolved_request_id is None:
                raise RuntimeError("starting durable queue item has no request id")
        else:
            resolved_request_id = request_id or new_request_id("queue_start")
            local = await self._persistence.begin_start(
                submission_id,
                request_id=resolved_request_id,
            )
        try:
            receipt = await self._client.start_queue_submission(
                cid=cid,
                sid=sid,
                submission_id=submission_id,
                request_id=resolved_request_id,
            )
        except ProtocolCommandError as error:
            if not error.retryable:
                await self._persistence.reset_start(
                    submission_id,
                    request_id=resolved_request_id,
                )
            raise
        if receipt.turn_id != local.request.turn_id:
            raise RuntimeError("durable queue start returned another turn identity")
        started = await self._persistence.mark_started(
            submission_id,
            request_id=resolved_request_id,
            turn_id=receipt.turn_id,
            queue_version=receipt.queue_version,
        )
        return DurableQueueStartResult(local=started, receipt=receipt)

    async def settle(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot:
        """记录 Queue Turn 已观察到权威终态，禁止冷恢复重复 attach。"""
        return await self._persistence.mark_settled(submission_id)

    async def local_snapshot(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot | None:
        """读取单项本地执行事实，不把它提升为远端 Queue 真相。"""
        return await self._persistence.find(submission_id)

    async def _require_local(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot:
        """读取执行所需的本地冻结快照。"""
        local = await self._persistence.find(submission_id)
        if local is None:
            raise LookupError(
                "durable queue execution snapshot is unavailable on this client"
            )
        return local


def _validate_add_receipt(
    local: LocalDurableQueueSnapshot,
    receipt: DurableQueueMutationReceipt,
) -> None:
    """拒绝把另一项远端 Queue receipt 提交到本地账本。"""
    item = receipt.item
    _validate_remote_item(local, item)
    if receipt.request_id != local.add_request_id or item.status != "queued":
        raise RuntimeError("durable queue add receipt identity is invalid")


def _definitely_not_committed(error: ProtocolCommandError) -> bool:
    """仅把具有明确 4xx 响应的 add 判为未提交。"""
    status_code = error.status_code
    return bool(
        status_code is not None
        and 400 <= status_code < 500
        and not error.retryable
    )


def _validate_remote_item(
    local: LocalDurableQueueSnapshot,
    item: DurableQueueItem,
) -> None:
    """校验远端 Queue item 与本地冻结身份和输入投影一致。"""
    if (
        item.submission_id != local.submission_id
        or item.client_message_id != local.client_message_id
        or item.cid != local.request.cid
        or item.sid != local.request.sid
        or item.turn_id != local.request.turn_id
        or item.input.text != local.request.message
        or item.input.attachments != local.request.attachments
    ):
        raise RuntimeError("durable queue snapshot conflicts with local intent")


if __name__ == '__main__':
    pass
