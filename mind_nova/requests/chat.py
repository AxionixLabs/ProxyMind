# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import time
import httpx
import random
import typing
import asyncio
import contextlib
from mind_nova.service_auth import build_service_headers
from mind_nova.requests.payload import (
    build_chat_payload,
    request_llm_conf
)
from mind_nova.requests.streaming import streaming
from mind_nova.requests.turn_control import (
    TurnStatusRequestError,
    get_turn_status
)
from mind_nova.services import service_endpoints
from mind_nova.stream_events import (
    ChatStreamEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnLogicalSettledEvent,
    parse_stream_event
)
from mind_nova.tool_approval import ToolApprovalSnapshot
from mind_nova.requests.tools import (
    ToolApprovalSnapshotRequestError,
    reconcile_tool_approval_snapshot,
)
from mind_nova import const

ATTACH_BACKOFF_DELAYS_SEC: typing.Final[tuple[float, ...]] = (
    0.0,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
)

ATTACH_BACKOFF_JITTER_RATIO: typing.Final[float]  = 0.2
ATTACH_RETRY_MAX_ELAPSED_SEC: typing.Final[float] = 60.0

STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC: typing.Final[float] = 25.0
TRANSPORT_RETRY_MIN_VISIBLE_SEC: typing.Final[float]    = 0.8

TurnStreamEndReason: typing.TypeAlias = typing.Literal[
    "settled",
    "fatal",
    "cancelled",
    "protocol_error",
]

ReconnectStatusCallback: typing.TypeAlias = typing.Callable[[bool], None]

ApprovalSnapshotCallback: typing.TypeAlias = typing.Callable[
    [ToolApprovalSnapshot],
    typing.Awaitable[None] | None,
]


class _TurnStreamState(enum.Enum):
    RUNNING      = "running"
    OUTCOME_SEEN = "outcome_seen"
    CLOSED       = "closed"


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
        on_reconnect_status: ReconnectStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> None:
        """保存请求参数并初始化逻辑轮次观察状态。"""
        self._request = (pref_config, message, tools, attachments, kwargs)
        self._timeout = timeout
        self._state   = _TurnStreamState.RUNNING

        self._payload_stream: typing.AsyncGenerator[dict, None] | None = None

        self._chat_payload: dict[str, typing.Any] | None = None

        self._attach_target: dict[str, str] | None = None

        self._response_observed: bool = False

        self._close_after_yield: bool = False

        self._reconnect_failures: int            = 0
        self._reconnecting: bool                 = False
        self._reconnect_started_at: float | None = None
        self._reconnect_visible_at: float | None = None

        self._reconnect_clear_handle: asyncio.TimerHandle | None = None

        self._on_reconnect_status = on_reconnect_status
        self._on_approval_snapshot = on_approval_snapshot

        self.end_reason: TurnStreamEndReason | None = None

        self.last_event_seq: int = 0

    def __aiter__(self) -> typing.AsyncIterator[ChatStreamEvent]:
        """返回当前逻辑轮次的异步事件迭代器。"""
        return self._iterate()

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
        if self._state is _TurnStreamState.CLOSED:
            raise _TurnStreamEnded
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
                self._mark_transport_healthy()
                continue
            self._validate_turn_identity(parsed_event)
            self._response_observed = True
            if (
                parsed_event.event_seq is not None
                and parsed_event.event_seq <= self.last_event_seq
            ):
                continue
            if self._has_sequence_gap(parsed_event):
                if await self._resume_stream():
                    continue
                await self._finish("protocol_error")
                raise RuntimeError("turn event sequence is not continuous")
            if parsed_event.event_seq is not None:
                self.last_event_seq = parsed_event.event_seq
            self._mark_transport_healthy()
            event = parsed_event

        if isinstance(event, TurnLogicalSettledEvent):
            self._close_after_yield = True
        elif (
            isinstance(event, (TurnDoneEvent, TurnFailedEvent))
            and self._state is _TurnStreamState.RUNNING
        ):
            self._state = _TurnStreamState.OUTCOME_SEEN

        return event

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

    async def _next_payload(self) -> dict:
        """读取下一项载荷或以内部结束信号完成当前流。"""
        while True:
            payload_stream = await self._ensure_open()

            try:
                async with asyncio.timeout(self._payload_silence_timeout()):
                    payload: typing.Any = await anext(payload_stream)
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
            cid     = str(metadata.get("cid") or "").strip()
            sid     = str(metadata.get("sid") or "").strip()
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
        if self._reconnect_started_at is None:
            self._reconnect_started_at = now
        elif now - self._reconnect_started_at >= ATTACH_RETRY_MAX_ELAPSED_SEC:
            return False

        delay = self._attach_delay()
        self._reconnect_failures += 1
        self._set_reconnecting(True)

        await self._close_payload_stream()
        await self._wait_before_attach(delay)

        if (
            self._reconnect_started_at is not None
            and time.monotonic() - self._reconnect_started_at
            >= ATTACH_RETRY_MAX_ELAPSED_SEC
        ):
            return False

        if not self._response_observed:
            turn_exists = await self._turn_exists_for_recovery(attach_target)
            if not turn_exists:
                self._payload_stream = self._open_chat_stream()
                return True

        await self._restore_approval_snapshot(attach_target)

        payload: dict[str, typing.Any] = dict(attach_target)
        payload["after_seq"] = self.last_event_seq

        self._payload_stream = streaming(
            service_endpoints.endpoint("/mind-attach"),
            build_service_headers(),
            payload,
            self._timeout,
        )
        return True

    async def _restore_approval_snapshot(
        self,
        attach_target: dict[str, str]
    ) -> None:
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

    def _has_sequence_gap(self, event: ChatStreamEvent) -> bool:
        """判断已建立水位后的事件序号是否出现缺口。"""
        event_seq = event.event_seq
        return (
            event_seq is not None
            and self.last_event_seq > 0
            and event_seq > self.last_event_seq + 1
        )

    def _set_reconnecting(self, reconnecting: bool) -> None:
        """在连接状态实际变化时通知上层。"""
        value = bool(reconnecting)

        if value:
            self._cancel_reconnect_clear()
            if self._reconnecting:
                self._reconnect_visible_at = time.monotonic()
                return
            self._reconnecting = True
            self._reconnect_visible_at = time.monotonic()
            if self._on_reconnect_status is not None:
                self._on_reconnect_status(True)
            return

        if not self._reconnecting or self._reconnect_clear_handle is not None:
            return

        visible_at = self._reconnect_visible_at
        if visible_at is None:
            self._clear_reconnecting()
            return

        elapsed = max(0.0, time.monotonic() - visible_at)

        remaining = max(0.0, TRANSPORT_RETRY_MIN_VISIBLE_SEC - elapsed)
        if remaining <= 0:
            self._clear_reconnecting()
            return

        self._reconnect_clear_handle = asyncio.get_running_loop().call_later(
            remaining,
            self._clear_reconnecting_after,
            visible_at,
        )

    def _clear_reconnecting_after(self, visible_at: float) -> None:
        """仅清除创建当前定时器的那一轮重连状态。"""
        if self._reconnect_visible_at != visible_at:
            return
        self._clear_reconnecting()

    def _cancel_reconnect_clear(self) -> None:
        """取消尚未执行的重连状态复位。"""
        handle = self._reconnect_clear_handle
        self._reconnect_clear_handle = None
        if handle is not None and not handle.cancelled():
            handle.cancel()

    def _clear_reconnecting(self) -> None:
        """立即清除传输重连状态。"""
        self._cancel_reconnect_clear()
        if not self._reconnecting:
            self._reconnect_visible_at = None
            return

        self._reconnecting = False
        self._reconnect_visible_at = None
        if self._on_reconnect_status is not None:
            self._on_reconnect_status(False)

    def _mark_transport_healthy(self) -> None:
        """在收到有效载荷后重置连续重连预算。"""
        self._reconnect_failures = 0
        self._reconnect_started_at = None
        self._set_reconnecting(False)

    def _payload_silence_timeout(self) -> float:
        """返回不超过传输超时的 SSE 静默检测窗口。"""
        return max(
            0.01,
            min(float(self._timeout), STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC),
        )

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

    async def _turn_exists_for_recovery(
        self,
        attach_target: dict[str, str],
    ) -> bool:
        """查询首事件前断线的轮次是否已由服务端持久化。"""
        try:
            await get_turn_status(**attach_target)
        except TurnStatusRequestError as error:
            if self._status_probe_allows_resubmit(error):
                return False
            raise
        return True

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

    @staticmethod
    async def _wait_before_attach(delay: float) -> None:
        """等待下一次重新接入且保持任务可取消。"""
        if delay <= 0:
            return
        await asyncio.sleep(delay)

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
        if self._state is _TurnStreamState.CLOSED:
            return

        self._state     = _TurnStreamState.CLOSED
        self.end_reason = reason

        self._reconnect_started_at = None
        self._clear_reconnecting()

        await self._close_payload_stream()


def stream_chat(
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    on_reconnect_status: ReconnectStatusCallback | None = None,
    on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    *_,
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
        on_reconnect_status,
        on_approval_snapshot,
    )


async def stream_heal(
    pref_config: dict[str, typing.Any],
    page_id: str,
    station: str,
    locator: str,
    page_dump: str,
    screenshot_base64: str,
    wm_size: dict,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """流式获取修复链路事件。"""
    headers = build_service_headers()

    payload = {
        "llm_conf"   : request_llm_conf(pref_config),
        "app_id"     : const.APP_DESC,
        "page_id"    : page_id,
        "platform"   : station,
        "locator"    : locator,
        "page_dump"  : page_dump,
        "screenshot" : f"data:image/png;base64,{screenshot_base64}",
        "wm_size"    : wm_size,
        "context"    : kwargs
    }

    async for event in streaming(service_endpoints.endpoint("/mind-heal"), headers, payload, timeout):
        if event.get("type") == "ping":
            continue

        if event.get("type") == "heal.step" and not str(event.get("message") or ""):
            continue

        yield event


if __name__ == '__main__':
    pass
