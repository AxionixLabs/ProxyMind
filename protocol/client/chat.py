# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import random
import time
import typing

import httpx

from protocol.client.payload import build_chat_payload
from protocol.client.tools import (
    ToolApprovalSnapshotRequestError,
    reconcile_tool_approval_snapshot,
)
from protocol.client.turn_control import (
    TurnStatusRequestError,
    TurnStatusSnapshot,
    get_turn_status,
)
from protocol.schema.stream_events import (
    ChatStreamEvent,
    StreamGapEvent,
    TurnCompletedEvent,
    parse_stream_event
)
from protocol.schema.tool_approval import ToolApprovalSnapshot
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.streaming import streaming

ATTACH_BACKOFF_DELAYS_SEC: typing.Final[tuple[float, ...]] = (
    0.0,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
)

ATTACH_BACKOFF_JITTER_RATIO: typing.Final[float] = 0.2
ATTACH_RETRY_MAX_ELAPSED_SEC: typing.Final[float] = 60.0
STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC: typing.Final[float] = 25.0
CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC: typing.Final[float] = 0.5

TurnStreamEndReason: typing.TypeAlias = typing.Literal[
    "settled",
    "fatal",
    "cancelled",
    "protocol_error",
]

TransportRecoveryPhase: typing.TypeAlias = typing.Literal[
    "reconnecting",
    "replaying",
    "caught_up",
    "closed",
]

RecoveryStatusCallback: typing.TypeAlias = typing.Callable[
    [TransportRecoveryPhase, int],
    typing.Awaitable[None],
]

ApprovalSnapshotCallback: typing.TypeAlias = typing.Callable[
    [ToolApprovalSnapshot],
    typing.Awaitable[None] | None,
]


class _TurnStreamEnded(Exception):
    """表示事件传输已经按预期完成收尾。"""


class TurnEventStream(object):
    """管理单个模型轮次的事件读取与传输终止。"""

    def __init__(
        self,
        pref_config: dict[str, typing.Any],
        message: str,
        tools: list[dict],
        attachments: typing.Optional[list[dict[str, typing.Any]]],
        timeout: float,
        kwargs: dict[str, typing.Any],
        on_recovery_status: RecoveryStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
        initial_event_seq: int = 0,
    ) -> None:
        """保存请求参数并初始化逻辑轮次观察状态。"""
        self._request = (pref_config, message, tools, attachments, kwargs)
        self._timeout = timeout
        self._closed: bool = False
        self._payload_stream: typing.AsyncGenerator[dict, None] | None = None
        self._chat_payload: dict[str, typing.Any] | None = None
        self._attach_target: dict[str, str] | None = None
        self._response_observed: bool = False
        self._close_after_yield: bool = False
        self._reconnect_failures: int = 0
        self._recovery_phase: TransportRecoveryPhase | None = None
        self._replay_target_seq: int | None = None
        self._recovery_catch_up_pending: bool = False
        self._terminal_snapshot_payload: dict[str, typing.Any] | None = None
        self._internal_gap_detected: bool = False
        self._control_settlement_probe_active: bool = False
        self._recovery_probe_requested: asyncio.Event = asyncio.Event()
        self._reconnect_started_at: float | None = None
        self._last_event_progress_at: float = time.monotonic()
        self._on_recovery_status = on_recovery_status
        self._on_approval_snapshot = on_approval_snapshot
        self.end_reason: TurnStreamEndReason | None = None

        if (
            isinstance(initial_event_seq, bool)
            or not isinstance(initial_event_seq, int)
            or initial_event_seq < 0
        ):
            raise ValueError("initial_event_seq must be a non-negative integer")
        self.last_event_seq = initial_event_seq

    def __aiter__(self) -> typing.AsyncIterator[ChatStreamEvent]:
        """返回当前逻辑轮次的异步事件迭代器。"""
        return self._iterate()

    @staticmethod
    def _status_probe_allows_resubmit(error: TurnStatusRequestError) -> bool:
        """判断状态查询失败后能否安全重提幂等对话请求。"""
        status_code = error.status_code
        if status_code is None or status_code == 404:
            return True
        return status_code >= 500 or status_code in {408, 425, 429}

    @staticmethod
    def _recoverable(error: httpx.HTTPError | OSError | None) -> bool:
        """判断传输错误是否允许重新接入当前轮次。"""
        if not isinstance(error, httpx.HTTPStatusError):
            return True
        status_code = error.response.status_code
        return status_code >= 500 or status_code in {408, 425, 429}

    @staticmethod
    def _terminal_payload(
        status: TurnStatusSnapshot,
        *,
        attach_target: dict[str, str],
    ) -> dict[str, typing.Any]:
        """把同构终态快照转换为统一事件 reducer 的输入。"""
        terminal = status.terminal
        if terminal is None:
            raise ValueError("turn status is missing terminal snapshot")
        return {
            "type": terminal.type,
            "proto": "mind.chat",
            "cid": attach_target["cid"],
            "sid": attach_target["sid"],
            "turn_id": terminal.turn_id,
            "event_seq": terminal.last_event_seq,
            "presentation_epoch": 1,
            "status": terminal.status,
            "error": terminal.error,
            "last_event_seq": terminal.last_event_seq,
            "completed_at": terminal.completed_at,
        }

    def _attach_delay(self) -> float:
        """计算当前连续失败次数对应的封顶抖动退避。"""
        index = min(
            self._reconnect_failures,
            len(ATTACH_BACKOFF_DELAYS_SEC) - 1,
        )
        base_delay = ATTACH_BACKOFF_DELAYS_SEC[index]
        if base_delay <= 0:
            return 0.0
        lower_bound = base_delay * (1.0 - ATTACH_BACKOFF_JITTER_RATIO)
        return random.uniform(lower_bound, base_delay)

    def _validate_turn_identity(self, event: ChatStreamEvent) -> None:
        """拒绝缺失坐标或不属于当前逻辑轮次的业务事件。"""
        attach_target = self._attach_target
        if attach_target is None:
            raise ValueError("turn stream is missing recovery coordinates")
        if (
            event.proto != "mind.chat"
            or event.cid != attach_target["cid"]
            or event.sid != attach_target["sid"]
            or event.turn_id != attach_target["turn_id"]
        ):
            raise ValueError("stream event does not belong to the current turn")
        if event.event_seq is None or event.event_seq < 1:
            raise ValueError("stream event requires a positive event_seq")

    def _validate_gap_identity(self, event: StreamGapEvent) -> None:
        """拒绝不属于当前逻辑轮次的非持久缺口信号。"""
        attach_target = self._attach_target
        if attach_target is None:
            raise ValueError("turn stream is missing recovery coordinates")
        if (
            event.cid != attach_target["cid"]
            or event.sid != attach_target["sid"]
            or event.turn_id != attach_target["turn_id"]
        ):
            raise ValueError("stream gap does not belong to the current turn")
        if event.requested_after_seq != self.last_event_seq:
            raise ValueError("stream gap does not match the confirmed event cursor")

    def _has_sequence_gap(self, event: ChatStreamEvent) -> bool:
        """判断已建立水位后的事件序号是否出现缺口。"""
        event_seq = event.event_seq
        return (
            event_seq is not None
            and self.last_event_seq > 0
            and event_seq > self.last_event_seq + 1
        )

    def _event_progress_timeout(self) -> float:
        """返回距离下一次权威事件活性检查的剩余时间。"""
        timeout = max(
            0.01,
            min(float(self._timeout), STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC),
        )
        if self._control_settlement_probe_active:
            timeout = min(
                timeout,
                CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC,
            )
        elapsed = time.monotonic() - self._last_event_progress_at
        return max(0.0, timeout - elapsed)

    def _reset_event_progress_deadline(self) -> None:
        """为新建立的传输连接开启一个完整的事件推进窗口。"""
        self._last_event_progress_at = time.monotonic()

    def _open_chat_stream(self) -> typing.AsyncGenerator[dict, None]:
        """使用缓存的原始请求创建对话事件传输。"""
        payload = self._chat_payload
        if payload is None:
            raise RuntimeError("chat payload is unavailable")

        return streaming(
            service_endpoints.endpoint("/mind-chat"),
            build_service_headers(),
            payload,
            self._timeout,
        )

    def _required_attach_target(self) -> dict[str, str]:
        """返回已经建立的恢复坐标。"""
        attach_target = self._attach_target
        if attach_target is None:
            raise RuntimeError("turn stream is missing recovery coordinates")
        return attach_target

    def request_recovery_probe(self) -> None:
        """请求当前事件观察立即通过 attach/status 核对权威 Turn。"""
        if not self._closed:
            self._control_settlement_probe_active = True
            self._recovery_probe_requested.set()

    @staticmethod
    async def _wait_before_attach(delay: float) -> None:
        """等待下一次重新接入且保持任务可取消。"""
        if delay <= 0:
            return
        await asyncio.sleep(delay)

    async def _notify_recovery(
        self,
        phase: TransportRecoveryPhase,
        *,
        event_seq: int,
    ) -> None:
        """在恢复阶段实际变化时通知上层。"""
        if phase == self._recovery_phase:
            return
        self._recovery_phase = phase
        if self._on_recovery_status is not None:
            await self._on_recovery_status(phase, event_seq)

    async def _begin_replay(self, target_event_seq: int) -> None:
        """冻结权威水位并进入无瞬时动画的 replay 阶段。"""
        self._replay_target_seq = max(self.last_event_seq, target_event_seq)
        self._recovery_catch_up_pending = False
        await self._notify_recovery(
            "replaying",
            event_seq=self.last_event_seq,
        )

    async def _complete_replay(self) -> None:
        """在最后一条历史事件交付后恢复实时展示。"""
        self._replay_target_seq = None
        self._recovery_catch_up_pending = False
        await self._notify_recovery(
            "caught_up",
            event_seq=self.last_event_seq,
        )

    async def _prepare_recovery_delivery(self, event_seq: int) -> None:
        """确定当前事件应在 replay 中处理还是恢复实时交付。"""
        target_event_seq = self._replay_target_seq
        if target_event_seq is None:
            return None
        if event_seq > target_event_seq:
            await self._complete_replay()
            return None
        if event_seq >= target_event_seq:
            self._recovery_catch_up_pending = True

    async def _mark_transport_healthy(self) -> None:
        """在收到有效载荷后重置连续重连预算。"""
        self._reconnect_failures = 0
        self._reconnect_started_at = None

    async def _mark_event_progress(self) -> None:
        """在权威事件水位推进后刷新活性截止时间。"""
        self._last_event_progress_at = time.monotonic()
        await self._mark_transport_healthy()

    async def _read_payload_or_recovery_probe(
        self,
        payload_stream: typing.AsyncGenerator[dict, None],
        *,
        timeout: float,
    ) -> dict | None:
        """读取下一项载荷，或被控制命令唤醒以立即恢复观察。"""
        payload_task = asyncio.create_task(anext(payload_stream))
        probe_task = asyncio.create_task(self._recovery_probe_requested.wait())
        tasks = (payload_task, probe_task)
        try:
            completed, _pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not completed:
                raise TimeoutError
            if payload_task in completed:
                return payload_task.result()
            self._recovery_probe_requested.clear()
            return None
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _close_payload_stream(self) -> None:
        """关闭当前底层事件传输。"""
        payload_stream = self._payload_stream

        self._payload_stream = None

        if payload_stream is None:
            return
        with contextlib.suppress(Exception):
            await payload_stream.aclose()

    async def _finish(self, reason: TurnStreamEndReason) -> None:
        """只执行一次底层传输关闭。"""
        if self._closed:
            return

        self._closed = True
        self.end_reason = reason

        self._reconnect_started_at = None
        try:
            if self._recovery_phase in {"reconnecting", "replaying"}:
                await self._notify_recovery(
                    "closed",
                    event_seq=self.last_event_seq,
                )
        finally:
            await self._close_payload_stream()

    async def _iterate(self) -> typing.AsyncIterator[ChatStreamEvent]:
        """持续读取事件并在消费终止时释放传输。"""
        try:
            while True:
                try:
                    event = await self._next_event()
                except _TurnStreamEnded:
                    return
                yield event
        except asyncio.CancelledError:
            await self._finish("cancelled")
            raise
        except (TypeError, ValueError):
            await self._finish("protocol_error")
            raise
        except Exception:
            await self._finish("fatal")
            raise
        finally:
            await self.aclose()

    async def _next_event(self) -> ChatStreamEvent:
        """读取并解析下一项可交付事件。"""
        if self._closed:
            raise _TurnStreamEnded
        if self._recovery_catch_up_pending:
            await self._complete_replay()
        if self._close_after_yield:
            await self._finish("settled")
            raise _TurnStreamEnded

        event: ChatStreamEvent | None = None

        while event is None:
            payload = await self._next_payload()

            try:
                parsed_event = parse_stream_event(payload)
                if parsed_event is None:
                    raise ValueError("stream event parser returned no event")
            except (TypeError, ValueError):
                await self._finish("protocol_error")
                raise

            if parsed_event.type == "ping":
                await self._mark_transport_healthy()
                continue
            if isinstance(parsed_event, StreamGapEvent):
                self._validate_gap_identity(parsed_event)
                if parsed_event.gap_kind == "internal":
                    first_observation = not self._internal_gap_detected
                    self._internal_gap_detected = True
                    if first_observation:
                        event = parsed_event
                    continue
                if parsed_event.next_seq is None:
                    await self._finish("protocol_error")
                    raise RuntimeError("retained event prefix is missing replay floor")
                self.last_event_seq = max(
                    self.last_event_seq,
                    parsed_event.next_seq,
                )
                if self._replay_target_seq is None:
                    status = await self._turn_status_for_recovery(
                        self._required_attach_target()
                    )
                    await self._begin_replay(
                        status.last_event_seq
                        if status is not None
                        else self.last_event_seq
                    )
                if self.last_event_seq >= (self._replay_target_seq or 0):
                    self._recovery_catch_up_pending = True
                await self._mark_event_progress()
                event = parsed_event
                continue
            self._validate_turn_identity(parsed_event)
            self._response_observed = True
            if (
                self._internal_gap_detected
                and not isinstance(parsed_event, TurnCompletedEvent)
            ):
                continue
            if (
                parsed_event.event_seq is not None
                and parsed_event.event_seq <= self.last_event_seq
            ):
                continue
            if (
                self._has_sequence_gap(parsed_event)
                and not (
                    self._internal_gap_detected
                    and isinstance(parsed_event, TurnCompletedEvent)
                )
            ):
                if await self._resume_stream():
                    continue
                await self._finish("protocol_error")
                raise RuntimeError("turn event sequence is not continuous")
            if parsed_event.event_seq is not None:
                await self._prepare_recovery_delivery(parsed_event.event_seq)
                self.last_event_seq = parsed_event.event_seq
            await self._mark_event_progress()
            event = parsed_event

        if isinstance(event, TurnCompletedEvent):
            self._close_after_yield = True

        return event

    async def _next_payload(self) -> dict:
        """读取下一项载荷或以内部结束信号完成当前流。"""
        while True:
            terminal_payload = self._terminal_snapshot_payload
            if terminal_payload is not None:
                self._terminal_snapshot_payload = None
                return terminal_payload
            if (
                self._control_settlement_probe_active
                and self._payload_stream is None
                and self._attach_target is not None
            ):
                await asyncio.sleep(CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC)
                if await self._resume_stream():
                    continue
                await self._finish("fatal")
                raise RuntimeError(
                    "turn settlement status probe could not resume observation"
                )
            payload_stream = await self._ensure_open()

            try:
                payload = await self._read_payload_or_recovery_probe(
                    payload_stream,
                    timeout=self._event_progress_timeout(),
                )
                if payload is None:
                    self._reconnect_failures = 0
                    self._reconnect_started_at = None
                    if await self._resume_stream():
                        continue
                    await self._finish("fatal")
                    raise RuntimeError(
                        "turn recovery probe could not resume observation"
                    )
                if not isinstance(payload, dict):
                    await self._finish("protocol_error")
                    raise TypeError("stream payload must be an object")
                return payload
            except StopAsyncIteration:
                if await self._resume_stream():
                    continue
                await self._finish("protocol_error")
                raise RuntimeError(
                    "turn stream is missing recovery coordinates"
                ) from None
            except asyncio.CancelledError:
                await self._finish("cancelled")
                raise
            except TimeoutError as error:
                if await self._resume_stream(error):
                    continue
                await self._finish("fatal")
                raise
            except (httpx.HTTPError, OSError) as error:
                if await self._resume_stream(error):
                    continue
                await self._finish("fatal")
                raise

        raise RuntimeError("event payload loop exited unexpectedly")

    async def _ensure_open(self) -> typing.AsyncGenerator[dict, None]:
        """按首次读取延迟创建并返回底层事件传输。"""
        if self._payload_stream is not None:
            return self._payload_stream

        pref_config, message, tools, attachments, kwargs = self._request

        payload = self._chat_payload
        if payload is None:
            payload = await build_chat_payload(
                pref_config,
                message,
                tools,
                attachments,
                **kwargs,
            )
            self._chat_payload = payload

        metadata = payload.get("metadata")

        if isinstance(metadata, dict):
            cid = str(metadata.get("cid") or "").strip()
            sid = str(metadata.get("sid") or "").strip()
            turn_id = str(payload.get("turn_id") or "").strip()

            if cid and sid and turn_id:
                self._attach_target = {
                    "cid": cid,
                    "sid": sid,
                    "turn_id": turn_id,
                }

        self._payload_stream = self._open_chat_stream()

        return self._payload_stream

    async def _resume_stream(
        self,
        error: httpx.HTTPError | OSError | None = None,
    ) -> bool:
        """在可恢复的传输中断后重新提交或接入当前轮次。"""
        attach_target = self._attach_target
        if attach_target is None or not self._recoverable(error):
            return False

        now = time.monotonic()
        control_settlement = self._control_settlement_probe_active
        if control_settlement:
            delay = 0.0
        else:
            if self._reconnect_started_at is None:
                self._reconnect_started_at = now
            elif now - self._reconnect_started_at >= ATTACH_RETRY_MAX_ELAPSED_SEC:
                self._reconnect_started_at = now
                self._reconnect_failures = 0

            delay = self._attach_delay()
            self._reconnect_failures += 1
        await self._notify_recovery(
            "reconnecting",
            event_seq=self.last_event_seq,
        )

        await self._close_payload_stream()
        await self._wait_before_attach(delay)

        if (
            self._reconnect_started_at is not None
            and time.monotonic() - self._reconnect_started_at
            >= ATTACH_RETRY_MAX_ELAPSED_SEC
        ):
            self._reconnect_started_at = time.monotonic()
            self._reconnect_failures = 0

        status = await self._turn_status_for_recovery(attach_target)
        if not self._response_observed and status is None:
            self._replay_target_seq = self.last_event_seq
            self._payload_stream = self._open_chat_stream()
            self._reset_event_progress_deadline()
            return True

        if (
            status is not None
            and status.terminal is not None
            and (
                status.last_event_seq == self.last_event_seq + 1
                or control_settlement
                or (
                    self._internal_gap_detected
                    and status.last_event_seq > self.last_event_seq
                )
            )
        ):
            await self._begin_replay(status.last_event_seq)
            self._terminal_snapshot_payload = self._terminal_payload(
                status,
                attach_target=attach_target,
            )
            self._reset_event_progress_deadline()
            return True

        if control_settlement:
            self._replay_target_seq = None
            self._recovery_catch_up_pending = False
            self._reset_event_progress_deadline()
            return True

        await self._begin_replay(
            status.last_event_seq
            if status is not None
            else self.last_event_seq
        )
        snapshot_event_seq = await self._restore_approval_snapshot(attach_target)
        if snapshot_event_seq is not None:
            self._replay_target_seq = max(
                self._replay_target_seq or 0,
                snapshot_event_seq,
            )
        if self.last_event_seq >= (self._replay_target_seq or 0):
            self._recovery_catch_up_pending = True

        payload: dict[str, typing.Any] = dict(attach_target)
        payload["after_seq"] = self.last_event_seq

        self._payload_stream = streaming(
            service_endpoints.endpoint("/mind-attach"),
            build_service_headers(),
            payload,
            self._timeout,
        )
        self._reset_event_progress_deadline()
        return True

    async def _restore_approval_snapshot(
        self,
        attach_target: dict[str, str]
    ) -> int | None:
        """在重新接入前读取并交付审批恢复快照。"""
        callback = self._on_approval_snapshot
        if callback is None:
            return None

        try:
            snapshot = await reconcile_tool_approval_snapshot(**attach_target)
        except ToolApprovalSnapshotRequestError:
            return None

        result = callback(snapshot)
        if result is not None:
            await result
        return snapshot.last_event_seq

    async def _turn_status_for_recovery(
        self,
        attach_target: dict[str, str],
    ) -> TurnStatusSnapshot | None:
        """查询恢复开始时的权威 Turn 水位；可安全重提时返回空。"""
        try:
            return await get_turn_status(**attach_target)
        except TurnStatusRequestError as error:
            if self._status_probe_allows_resubmit(error):
                return None
            raise

    async def aclose(self) -> None:
        """关闭底层事件传输并固定结束原因。"""
        reason: TurnStreamEndReason = (
            self.end_reason
            if self.end_reason is not None
            else "settled"
            if self._close_after_yield
            else "cancelled"
        )
        await self._finish(reason)


def stream_chat(
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    on_recovery_status: RecoveryStatusCallback | None = None,
    on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    *_,
    initial_event_seq: int = 0,
    **kwargs
) -> TurnEventStream:
    """流式获取对话事件。"""
    return TurnEventStream(
        pref_config,
        message,
        tools,
        attachments,
        timeout,
        kwargs,
        on_recovery_status,
        on_approval_snapshot,
        initial_event_seq,
    )


if __name__ == '__main__':
    pass
