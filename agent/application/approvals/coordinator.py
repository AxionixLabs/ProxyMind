# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import uuid
import typing
import asyncio
import contextlib
from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass
from agent.application.approvals.models import (
    ApprovalDecisionSource,
    ApprovalDecisionValue,
    ApprovalOutcome,
    ApprovalQueueSnapshot,
    ApprovalRequest,
    ApprovalRequestKey,
    ApprovalResolutionReason,
)
from agent.application.approvals.factory import build_approval_request
from agent.application.approvals.presenter import ApprovalPresenterPort

ApprovalSnapshotErrorHandler: typing.TypeAlias = typing.Callable[
    [BaseException, str, int],
    None,
]

DEFAULT_APPROVAL_QUEUE_LIMIT = 64


@dataclass(slots=True)
class _QueuedApproval(object):
    """保存协调器内部的一项未决审批。"""
    request: ApprovalRequest
    future: asyncio.Future[ApprovalOutcome]
    waiters: int = 1
    presentation_task: asyncio.Task[ApprovalDecisionValue] | None = None


class ApprovalCoordinator:
    """协调前端无关的审批队列、取消和关闭生命周期。"""

    def __init__(
        self,
        interaction: ApprovalPresenterPort,
        *,
        queue_limit: int = DEFAULT_APPROVAL_QUEUE_LIMIT,
        snapshot_error_handler: ApprovalSnapshotErrorHandler | None = None,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("approval queue_limit must be positive")

        self._interaction = interaction
        self._queue_limit = queue_limit
        self._snapshot_error_handler = snapshot_error_handler
        self._lock = asyncio.Lock()
        self._pending: deque[_QueuedApproval] = deque()
        self._current: _QueuedApproval | None = None
        self._by_request_id: dict[str, _QueuedApproval] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False
        self._session_active = False
        self._coordinator_id = uuid.uuid4().hex
        self._revision = 0

    @property
    def snapshot(self) -> ApprovalQueueSnapshot:
        """返回当前审批队列的不可变快照。"""
        return self._snapshot()

    @staticmethod
    def _copy_request(request: ApprovalRequest) -> ApprovalRequest:
        """复制快照中的请求载荷，避免观察者修改内部队列。"""
        return ApprovalRequest(
            key=request.key,
            payload=copy.deepcopy(request.payload),
            presentation=copy.deepcopy(request.presentation),
            decisions=request.decisions,
        )

    @staticmethod
    def _outcome(
        decision: ApprovalDecisionValue,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
    ) -> ApprovalOutcome:
        """构造协调器统一使用的审批终态。"""
        return ApprovalOutcome.create(
            decision,
            source=source,
            reason=reason,
        )

    @staticmethod
    def _cancel_presentation(entry: _QueuedApproval) -> None:
        """取消不再对应未决请求的前端展示任务。"""
        task = entry.presentation_task
        entry.presentation_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    @staticmethod
    def _text(value: typing.Any) -> str:
        """把可选协议字段规范化为去除首尾空白的文本。"""
        return str(value or "").strip()

    def _normalize_request(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> ApprovalRequest:
        """把审批载荷统一转换为稳定应用层请求。"""
        if isinstance(approval, ApprovalRequest):
            return approval
        return build_approval_request(approval)

    def _immediate_outcome(
        self,
    ) -> ApprovalOutcome | None:
        """返回无需进入交互队列即可确定的结果。"""
        if self._closed:
            return self._outcome(
                "decline",
                source="policy",
                reason="closed",
            )
        return None

    def _ensure_worker(self) -> None:
        """确保唯一审批 worker 正在运行。"""
        if self._worker_task is not None:
            return None
        self._worker_task = asyncio.create_task(
            self._run(),
            name="approval coordinator",
        )

    def _settle_batch_cancel(self, current: _QueuedApproval) -> None:
        """使用 cancel 收束当前请求和整个等待队列。"""
        entries = tuple(
            queued for queued in self._by_request_id.values()
        )
        for queued in entries:
            self._settle_entry(
                queued,
                self._outcome(
                    "cancel",
                    source=self._interaction.approval_source,
                    reason="batch_cancelled",
                ),
                cancel_presentation=queued is not current,
            )

    def _settle_entry(
        self,
        entry: _QueuedApproval,
        outcome: ApprovalOutcome,
        *,
        cancel_presentation: bool = True,
    ) -> None:
        """幂等完成指定请求并从 current/pending 索引移除。"""
        if entry.future.done():
            return None

        self._remove_entry(entry)
        entry.future.set_result(outcome)
        if cancel_presentation:
            self._cancel_presentation(entry)
        self._cancel_empty_session_start()

    def _changed(self) -> None:
        """增加队列版本并把最新快照通知交互前端。"""
        self._revision += 1
        try:
            self._interaction.approval_snapshot_changed(self._snapshot())
        except Exception as error:
            handler = self._snapshot_error_handler
            if handler is not None:
                handler(error, self._coordinator_id, self._revision)

    def _snapshot(self) -> ApprovalQueueSnapshot:
        """在当前状态上构建不可变审批快照。"""
        return ApprovalQueueSnapshot(
            current=(
                self._copy_request(self._current.request)
                if self._current is not None
                else None
            ),
            pending=tuple(
                entry.request.key for entry in self._pending
                if not entry.future.done()
            ),
            revision=self._revision,
            coordinator_id=self._coordinator_id,
            closed=self._closed,
        )

    def _fail_entry(
        self,
        entry: _QueuedApproval,
        error: BaseException,
    ) -> None:
        """让展示异常只终止对应请求，并继续处理剩余队列。"""
        if entry.future.done():
            return None
        self._remove_entry(entry)
        if entry.waiters:
            entry.future.set_exception(error)
        else:
            entry.future.set_result(
                self._outcome(
                    "decline",
                    source="policy",
                    reason="presentation_failed",
                )
            )
        self._cancel_empty_session_start()

    def _remove_entry(self, entry: _QueuedApproval) -> None:
        """从 current、pending 和身份索引中移除指定请求。"""
        if self._current is entry:
            self._current = None
        else:
            self._pending = deque(
                queued for queued in self._pending if queued is not entry
            )
        self._by_request_id.pop(entry.request.key.request_id, None)

    def _take_pending(self) -> _QueuedApproval | None:
        """按 FIFO 取得下一条仍未完成的审批。"""
        while self._pending:
            entry = self._pending.popleft()
            if not entry.future.done():
                return entry
        return None

    def _cancel_empty_session_start(self) -> None:
        """在启动阶段已无请求时取消 worker，避免留下空审批会话。"""
        worker = self._worker_task
        if (
            self._by_request_id
            or self._session_active
            or worker is None
            or worker is asyncio.current_task()
        ):
            return None
        worker.cancel()

    def _find(
        self,
        request: ApprovalRequestKey | str,
    ) -> _QueuedApproval | None:
        """按完整 key 或任一稳定协议 ID 查找未决请求。"""
        if isinstance(request, ApprovalRequestKey):
            entry = self._by_request_id.get(request.request_id)
            if entry is not None and entry.request.key == request:
                return entry
            return None
        identity = self._text(request)
        if not identity:
            return None
        matches = tuple(
            entry
            for entry in self._by_request_id.values()
            if identity in {
                entry.request.key.request_id,
                entry.request.key.approval_id,
                entry.request.key.call_id,
            }
        )
        return matches[0] if len(matches) == 1 else None

    async def _run(self) -> None:
        """串行驱动当前审批展示并保持一个连续前端批次。"""
        try:
            if not self._closed:
                await self._begin_session()

            while True:
                async with self._lock:
                    if self._current is None:
                        self._current = self._take_pending()
                        if self._current is not None:
                            self._changed()

                    current = self._current
                    if current is None:
                        try:
                            await self._end_session()
                        finally:
                            self._worker_task = None
                        return None

                    presentation = asyncio.create_task(
                        self._interaction.present_approval(
                            self._copy_request(current.request)
                        ),
                        name="approval presentation",
                    )
                    current.presentation_task = presentation

                try:
                    decision = await presentation
                except asyncio.CancelledError:
                    async with self._lock:
                        if not current.future.done():
                            cancellation_reason: ApprovalResolutionReason
                            if self._closed:
                                cancellation_reason = "closed"
                            else:
                                cancellation_reason = "caller_cancelled"
                            self._settle_entry(
                                current,
                                self._outcome(
                                    "decline",
                                    source="policy",
                                    reason=cancellation_reason,
                                ),
                            )
                            self._changed()
                except Exception as error:
                    async with self._lock:
                        if not current.future.done():
                            self._fail_entry(current, error)
                            self._changed()
                else:
                    async with self._lock:
                        if current.future.done():
                            continue
                        if decision not in current.request.decisions and decision != "cancel":
                            self._fail_entry(
                                current,
                                ValueError(
                                    f"unsupported approval decision: {decision}"
                                ),
                            )
                            self._changed()
                            continue
                        if decision == "cancel":
                            self._settle_batch_cancel(current)
                        else:
                            decision_source = self._interaction.approval_source
                            if decision_source == "user":
                                resolution_reason = "user"
                            elif decision_source == "policy":
                                resolution_reason = "policy"
                            else:
                                resolution_reason = "external"
                            self._settle_entry(
                                current,
                                self._outcome(
                                    decision,
                                    source=decision_source,
                                    reason=resolution_reason,
                                ),
                            )
                        self._changed()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._lock:
                entries = tuple(
                    queued
                    for queued in self._by_request_id.values()
                    if not queued.future.done()
                )
                if entries:
                    self._fail_entry(entries[0], error)
                for queued in entries[1:]:
                    self._settle_entry(
                        queued,
                        self._outcome(
                            "decline",
                            source="policy",
                            reason="presentation_failed",
                        ),
                    )
                if entries:
                    self._changed()
        finally:
            async with self._lock:
                if self._worker_task is asyncio.current_task():
                    try:
                        await self._end_session()
                    finally:
                        self._worker_task = None

    async def _release_waiter(
        self,
        entry: _QueuedApproval,
        *,
        cancelled: bool,
    ) -> None:
        """释放调用方等待，并在无人等待时移除其未决请求。"""
        async with self._lock:
            entry.waiters = max(0, entry.waiters - 1)
            if not cancelled or entry.waiters or entry.future.done():
                return None
            self._settle_entry(
                entry,
                self._outcome(
                    "decline",
                    source="policy",
                    reason="caller_cancelled",
                ),
            )
            self._changed()

    async def _begin_session(self) -> None:
        """通知前端开始一个连续审批批次。"""
        if self._session_active:
            return None
        await self._interaction.begin_approval_session()
        self._session_active = True

    async def _end_session(self) -> None:
        """通知前端结束连续审批批次。"""
        if not self._session_active:
            return None
        self._session_active = False
        await self._interaction.end_approval_session()

    async def request(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> ApprovalDecisionValue:
        """提交审批请求并返回对应决策。"""
        outcome = await self.request_outcome(approval)
        return outcome.decision

    async def request_outcome(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> ApprovalOutcome:
        """提交审批请求并返回带来源和收束原因的终态。"""
        request = self._normalize_request(approval)

        async with self._lock:
            immediate = self._immediate_outcome()
            if immediate is not None:
                return immediate

            existing = self._by_request_id.get(request.key.request_id)
            if existing is not None:
                if existing.request != request:
                    outcome = self._outcome(
                        "decline",
                        source="policy",
                        reason="identity_conflict",
                    )
                    return outcome
                existing.waiters += 1
                entry = existing
            elif len(self._by_request_id) >= self._queue_limit:
                outcome = self._outcome(
                    "decline",
                    source="policy",
                    reason="overloaded",
                )
                return outcome
            else:
                entry = _QueuedApproval(
                    request=request,
                    future=asyncio.get_running_loop().create_future(),
                )
                self._by_request_id[request.key.request_id] = entry
                self._pending.append(entry)
                self._changed()
                self._ensure_worker()

        try:
            outcome = await asyncio.shield(entry.future)
        except asyncio.CancelledError:
            await self._release_waiter(entry, cancelled=True)
            raise
        else:
            await self._release_waiter(entry, cancelled=False)
            return outcome

    async def restore_pending(
        self,
        approval: Mapping[str, typing.Any] | ApprovalRequest,
    ) -> bool:
        """恢复一项尚未解决的审批，并交由现有队列展示。"""
        request = self._normalize_request(approval)

        async with self._lock:
            if self._closed:
                return False

            existing = self._by_request_id.get(request.key.request_id)
            if existing is not None:
                if existing.request != request:
                    raise ValueError(
                        "restored approval identity conflicts with pending request"
                    )
                return False

            if len(self._by_request_id) >= self._queue_limit:
                return False

            entry = _QueuedApproval(
                request=request,
                future=asyncio.get_running_loop().create_future(),
                waiters=0,
            )
            self._by_request_id[request.key.request_id] = entry
            self._pending.append(entry)
            self._changed()
            self._ensure_worker()
            return True

    async def resolve(
        self,
        request: ApprovalRequestKey | str,
        decision: ApprovalDecisionValue,
        *,
        source: ApprovalDecisionSource = "policy",
    ) -> bool:
        """按稳定身份幂等解决当前或排队中的审批请求。"""
        async with self._lock:
            entry = self._find(request)
            if entry is None or entry.future.done():
                return False
            if decision not in entry.request.decisions and decision != "cancel":
                raise ValueError(
                    f"unsupported approval decision: {decision}"
                )
            self._settle_entry(
                entry,
                self._outcome(
                    decision,
                    source=source,
                    reason="external",
                ),
            )
            self._changed()
            return True

    async def close(self) -> None:
        """拒绝全部未决审批并永久关闭协调器。"""
        async with self._lock:
            if self._closed:
                worker = self._worker_task
                cancel_worker = False
            else:
                self._closed = True
                entries = tuple(
                    queued for queued in self._by_request_id.values()
                )
                cancel_worker = bool(entries) and not self._session_active
                for queued in entries:
                    self._settle_entry(
                        queued,
                        self._outcome(
                            "decline",
                            source="policy",
                            reason="closed",
                        ),
                    )
                self._changed()
                worker = self._worker_task

        if worker is not None and worker is not asyncio.current_task():
            if cancel_worker:
                worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker


if __name__ == '__main__':
    pass
