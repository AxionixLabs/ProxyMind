# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

import httpx

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
    payload: dict[str, typing.Any],
    timeout: float,
    params: dict[str, str] | None = None,
    client_factory: typing.Callable[..., typing.Any] = httpx.AsyncClient,
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


async def get_json_reliably(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str],
    timeout: float,
    client_factory: typing.Callable[..., typing.Any] = httpx.AsyncClient,
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
