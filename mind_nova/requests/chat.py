# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import httpx
import typing
import asyncio
from engine.channel import Channel
from mind_nova.requests.payload import (
    build_chat_payload,
    request_llm_conf
)
from mind_nova.requests.streaming import streaming
from mind_nova.services import service_endpoints
from mind_nova.stream_events import (
    ChatStreamEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnLogicalSettledEvent,
    parse_stream_event
)
from mind_nova import const

TURN_SETTLEMENT_TIMEOUT_SEC: typing.Final[float] = 2.0

TurnStreamEndReason: typing.TypeAlias = typing.Literal[
    "settled",
    "settlement_timeout",
    "disconnected",
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
        kwargs: dict[str, typing.Any]
    ) -> None:
        self._request = (pref_config, message, tools, attachments, kwargs)
        self._timeout = timeout
        self._state   = _TurnStreamState.RUNNING

        self._payload_stream: typing.AsyncGenerator[dict, None] | None = None

        self._settlement_deadline: float | None = None

        self._settlement_seen: bool   = False
        self._close_after_yield: bool = False

        self.end_reason: TurnStreamEndReason | None = None

    def __aiter__(self) -> typing.AsyncIterator[ChatStreamEvent]:
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
            except (TypeError, ValueError) as error:
                outcome_seen = self._state is _TurnStreamState.OUTCOME_SEEN
                await self._finish("disconnected")
                if outcome_seen:
                    raise _TurnStreamEnded from None
                raise error

            if parsed_event.type == "ping":
                continue
            event = parsed_event

        if isinstance(event, TurnLogicalSettledEvent):
            self._settlement_seen = True
            if self._state is _TurnStreamState.OUTCOME_SEEN:
                self._close_after_yield = True
        elif (
            isinstance(event, (TurnDoneEvent, TurnFailedEvent))
            and self._state is _TurnStreamState.RUNNING
        ):
            self._state = _TurnStreamState.OUTCOME_SEEN
            self._settlement_deadline = (
                asyncio.get_running_loop().time()
                + TURN_SETTLEMENT_TIMEOUT_SEC
            )
            if self._settlement_seen:
                self._close_after_yield = True

        return event

    async def aclose(self) -> None:
        """关闭底层事件传输并固定结束原因。"""
        reason: TurnStreamEndReason = (
            self.end_reason
            if self.end_reason is not None
            else "disconnected"
        )
        await self._finish(reason)

    async def _next_payload(self) -> dict:
        """读取下一项载荷或以内部结束信号完成当前流。"""
        payload_stream = await self._ensure_open()

        try:
            if self._settlement_deadline is None:
                return await anext(payload_stream)
            async with asyncio.timeout_at(self._settlement_deadline):
                return await anext(payload_stream)
        except StopAsyncIteration:
            await self._finish("disconnected")
            raise _TurnStreamEnded from None
        except TimeoutError:
            await self._finish("settlement_timeout")
            raise _TurnStreamEnded from None
        except asyncio.CancelledError:
            await self._finish("disconnected")
            raise
        except (httpx.HTTPError, OSError):
            outcome_seen = self._state is _TurnStreamState.OUTCOME_SEEN
            await self._finish("disconnected")
            if outcome_seen:
                raise _TurnStreamEnded from None
            raise

    async def _ensure_open(self) -> typing.AsyncGenerator[dict, None]:
        """按首次读取延迟创建并返回底层事件传输。"""
        if self._payload_stream is not None:
            return self._payload_stream

        pref_config, message, tools, attachments, kwargs = self._request

        payload = await build_chat_payload(
            pref_config,
            message,
            tools,
            attachments,
            **kwargs,
        )

        self._payload_stream = streaming(
            service_endpoints.endpoint("/mind-chat"),
            Channel.make_headers(),
            payload,
            self._timeout,
        )

        return self._payload_stream

    async def _finish(self, reason: TurnStreamEndReason) -> None:
        """只执行一次底层传输关闭。"""
        if self._state is _TurnStreamState.CLOSED:
            return None

        self._state          = _TurnStreamState.CLOSED
        self.end_reason      = reason
        payload_stream       = self._payload_stream
        self._payload_stream = None

        if payload_stream is None:
            return None

        await payload_stream.aclose()


def stream_chat(
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
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
    headers = Channel.make_headers()
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
