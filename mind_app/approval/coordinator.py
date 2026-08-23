# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import uuid
import typing
import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass
from engine.observability import observe_exception
from mind_app.approval.models import (
    ApprovalDecisionSource,
    ApprovalDecisionValue,
    ApprovalOutcome,
    ApprovalQueueSnapshot,
    ApprovalRequest,
    ApprovalRequestKey,
    ApprovalRequestKind,
    ApprovalResolutionReason
)
from mind_app.approval.policy import (
    approval_decisions,
    approval_expired,
    approval_expires_at_ms
)
from mind_app.interaction.contracts import ApprovalPresenterPort

DEFAULT_APPROVAL_QUEUE_LIMIT = 64


@dataclass(slots=True)
class _QueuedApproval(object):
    """保存协调器内部的一项未决审批。"""
    request: ApprovalRequest
    future: asyncio.Future[ApprovalOutcome]
    waiters: int = 1
    expiry_task: asyncio.Task[None] | None = None
    presentation_task: asyncio.Task[ApprovalDecisionValue] | None = None


class ApprovalCoordinator:
    """协调前端无关的审批队列、超时、取消和关闭生命周期。"""

    def __init__(
        self,
        interaction: ApprovalPresenterPort,
        *,
        queue_limit: int = DEFAULT_APPROVAL_QUEUE_LIMIT,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("approval queue_limit must be positive")

        self._interaction = interaction
        self._queue_limit = queue_limit
        self._lock = asyncio.Lock()
        self._pending: deque[_QueuedApproval] = deque()
        self._current: _QueuedApproval | None = None
        self._by_request_id: dict[str, _QueuedApproval] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False
        self._session_active = False
        self._coordinator_id = uuid.uuid4().hex
        self._revision = 0
        self._local_request_sequence = 0

    @property
    def snapshot(self) -> ApprovalQueueSnapshot:
        """返回当前审批队列的不可变快照。"""
        return self._snapshot()

    async def request(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """提交审批请求并返回对应决策。"""
        outcome = await self.request_outcome(approval)
        return outcome.decision

    async def request_outcome(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalOutcome:
        """提交审批请求并返回带来源和收束原因的终态。"""
        request = self._normalize_request(approval)

        async with self._lock:
            immediate = self._immediate_outcome(request)
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
                self._schedule_expiry(entry)
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
            if decision not in entry.request.decisions and decision not in {
                "cancel",
                "expired",
            }:
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

    def _normalize_request(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalRequest:
        """把松散审批载荷转换为稳定应用层请求。"""
        payload = copy.deepcopy(dict(approval))
        approval_id = self._text(payload.get("id"))
        call_id = self._text(
            payload.get("call_id") or payload.get("callId")
        )
        tool = self._text(payload.get("tool")) or "shell_command"
        request_id = self._text(
            payload.get("request_id") or payload.get("requestId")
        )
        if not request_id:
            request_id = ":".join(
                value for value in (approval_id, call_id) if value
            )
        if not request_id:
            self._local_request_sequence += 1
            request_id = f"local-approval-{self._local_request_sequence}"

        return ApprovalRequest(
            key=ApprovalRequestKey(
                request_id=request_id,
                approval_id=approval_id,
                call_id=call_id,
                tool=tool,
                kind=self._request_kind(tool),
            ),
            approval=payload,
            decisions=tuple(approval_decisions(payload)),
            expires_at_ms=approval_expires_at_ms(payload),
        )

    def _immediate_outcome(
        self,
        request: ApprovalRequest,
    ) -> ApprovalOutcome | None:
        """返回无需进入交互队列即可确定的结果。"""
        if self._closed:
            return self._outcome(
                "decline",
                source="policy",
                reason="closed",
            )
        if approval_expired(request.approval):
            return self._outcome(
                "expired",
                source="policy",
                reason="expired",
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
                        if decision not in current.request.decisions and decision not in {
                            "cancel",
                            "expired",
                        }:
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
                            if decision == "expired":
                                decision_source: ApprovalDecisionSource = "policy"
                                resolution_reason: ApprovalResolutionReason = (
                                    "expired"
                                )
                            else:
                                decision_source = (
                                    self._interaction.approval_source
                                )
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
        self._cancel_expiry(entry)
        if cancel_presentation:
            self._cancel_presentation(entry)
        self._cancel_empty_session_start()

    def _fail_entry(
        self,
        entry: _QueuedApproval,
        error: BaseException,
    ) -> None:
        """让展示异常只终止对应请求，并继续处理剩余队列。"""
        if entry.future.done():
            return None
        self._remove_entry(entry)
        entry.future.set_exception(error)
        self._cancel_expiry(entry)
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

    def _schedule_expiry(self, entry: _QueuedApproval) -> None:
        """为带 deadline 的请求安排主动过期任务。"""
        expires_at_ms = entry.request.expires_at_ms
        if expires_at_ms is None:
            return None
        delay = max(0.0, expires_at_ms / 1000.0 - time.time())
        entry.expiry_task = asyncio.create_task(
            self._expire(entry, delay=delay),
            name="approval expiry",
        )

    async def _expire(
        self,
        entry: _QueuedApproval,
        *,
        delay: float,
    ) -> None:
        """在 deadline 到达时主动释放当前或排队请求。"""
        await asyncio.sleep(delay)
        async with self._lock:
            if entry.future.done():
                return None
            self._settle_entry(
                entry,
                self._outcome(
                    "expired",
                    source="policy",
                    reason="expired",
                ),
            )
            self._changed()

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

    def _changed(self) -> None:
        """增加队列版本并把最新快照通知交互前端。"""
        self._revision += 1
        try:
            self._interaction.approval_snapshot_changed(self._snapshot())
        except Exception as error:
            observe_exception(
                "approval.snapshot_notify_failed",
                error,
                level="WARNING",
                coordinator_id=self._coordinator_id,
                revision=self._revision,
            )

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

    @staticmethod
    def _copy_request(request: ApprovalRequest) -> ApprovalRequest:
        """复制快照中的请求载荷，避免观察者修改内部队列。"""
        return ApprovalRequest(
            key=request.key,
            approval=copy.deepcopy(request.approval),
            decisions=request.decisions,
            expires_at_ms=request.expires_at_ms,
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
    def _cancel_expiry(entry: _QueuedApproval) -> None:
        """取消不再需要的请求过期任务。"""
        task = entry.expiry_task
        entry.expiry_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    @staticmethod
    def _cancel_presentation(entry: _QueuedApproval) -> None:
        """取消不再对应未决请求的前端展示任务。"""
        task = entry.presentation_task
        entry.presentation_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    @staticmethod
    def _request_kind(tool: str) -> ApprovalRequestKind:
        """按工具名归一化审批展示类别。"""
        normalized = tool.strip().lower()
        if normalized in {"shell_command", "exec_command", "write_stdin"}:
            return "exec"
        if normalized in {"apply_patch", "patch"}:
            return "apply_patch"
        if "permission" in normalized:
            return "permissions"
        if normalized.startswith("mcp"):
            return "mcp"
        return "tool"

    @staticmethod
    def _text(value: typing.Any) -> str:
        """把可选协议字段规范化为去除首尾空白的文本。"""
        return str(value or "").strip()


if __name__ == '__main__':
    pass
