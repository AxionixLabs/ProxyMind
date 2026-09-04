# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import types
import typing

import httpx

from protocol.schema.json_value import JsonObject


class _AsyncJsonClient(typing.Protocol):
    """描述可靠 JSON 请求实际使用的异步客户端能力。"""

    async def __aenter__(self) -> "_AsyncJsonClient":
        """进入客户端资源上下文。"""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> bool | None:
        """退出客户端资源上下文。"""
        ...

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        """发送 GET JSON 请求。"""
        ...

    async def post(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        json: JsonObject,
    ) -> httpx.Response:
        """发送 POST JSON 请求。"""
        ...

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        json: JsonObject,
    ) -> httpx.Response:
        """发送指定方法的 JSON 请求。"""
        ...


class _AsyncJsonClientFactory(typing.Protocol):
    """描述按超时创建异步 JSON 客户端的工厂。"""

    def __call__(self, *, timeout: float) -> _AsyncJsonClient:
        """创建一次请求作用域内的客户端。"""
        ...

RETRY_DELAYS_SEC: typing.Final[tuple[float, ...]] = (
    0.0,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    5.0,
    5.0,
    5.0,
    5.0,
)

RETRYABLE_STATUS_CODES: typing.Final[frozenset[int]] = frozenset({
    408,
    425,
    429,
})


def is_retryable_status(status_code: int) -> bool:
    """判断响应状态是否表示瞬态服务故障。"""
    return status_code >= 500 or status_code in RETRYABLE_STATUS_CODES


async def post_json_reliably(
    url: str,
    *,
    headers: dict[str, str],
    payload: JsonObject,
    timeout: float,
    params: dict[str, str] | None = None,
    client_factory: _AsyncJsonClientFactory = httpx.AsyncClient,
    retry_delays: typing.Sequence[float] = RETRY_DELAYS_SEC
) -> httpx.Response:
    """以固定载荷重试瞬态失败并返回最终 HTTP 响应。"""
    delays = tuple(max(0.0, float(delay)) for delay in retry_delays)
    if not delays:
        raise ValueError("reliable request requires at least one attempt")

    last_transport_error: BaseException | None = None

    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with client_factory(timeout=timeout) as client:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    json=payload,
                )
        except asyncio.CancelledError:
            raise
        except (httpx.TransportError, OSError) as error:
            last_transport_error = error
            if attempt == len(delays):
                raise
            continue

        if not is_retryable_status(response.status_code) or attempt == len(delays):
            return response

    if last_transport_error is not None:
        raise last_transport_error
    raise RuntimeError("reliable request exhausted without a response")


async def send_json_reliably(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: JsonObject,
    timeout: float,
    params: dict[str, str] | None = None,
    client_factory: _AsyncJsonClientFactory = httpx.AsyncClient,
    retry_delays: typing.Sequence[float] = RETRY_DELAYS_SEC,
) -> httpx.Response:
    """以固定方法和载荷可靠提交一项 JSON mutation。"""
    normalized_method = str(method or "").strip().upper()
    if normalized_method not in {"PATCH", "DELETE"}:
        raise ValueError("reliable JSON mutation requires PATCH or DELETE")

    delays = tuple(max(0.0, float(delay)) for delay in retry_delays)
    if not delays:
        raise ValueError("reliable request requires at least one attempt")

    last_transport_error: BaseException | None = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with client_factory(timeout=timeout) as client:
                response = await client.request(
                    normalized_method,
                    url,
                    params=params,
                    headers=headers,
                    json=payload,
                )
        except asyncio.CancelledError:
            raise
        except (httpx.TransportError, OSError) as error:
            last_transport_error = error
            if attempt == len(delays):
                raise
            continue

        if not is_retryable_status(response.status_code) or attempt == len(delays):
            return response

    if last_transport_error is not None:
        raise last_transport_error
    raise RuntimeError("reliable request exhausted without a response")


async def get_json_reliably(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str],
    timeout: float,
    client_factory: _AsyncJsonClientFactory = httpx.AsyncClient,
    retry_delays: typing.Sequence[float] = RETRY_DELAYS_SEC,
) -> httpx.Response:
    """以固定查询参数重试瞬态失败并返回最终 HTTP 响应。"""
    delays = tuple(max(0.0, float(delay)) for delay in retry_delays)
    if not delays:
        raise ValueError("reliable request requires at least one attempt")

    last_transport_error: BaseException | None = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with client_factory(timeout=timeout) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
                )
        except asyncio.CancelledError:
            raise
        except (httpx.TransportError, OSError) as error:
            last_transport_error = error
            if attempt == len(delays):
                raise
            continue

        if not is_retryable_status(response.status_code) or attempt == len(delays):
            return response

    if last_transport_error is not None:
        raise last_transport_error
    raise RuntimeError("reliable request exhausted without a response")


if __name__ == '__main__':
    pass
