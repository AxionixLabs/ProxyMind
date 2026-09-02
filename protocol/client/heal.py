# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from protocol.client.payload import request_llm_conf
from protocol.transport import config
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.streaming import streaming

__all__ = ("stream_heal",)


async def stream_heal(
    pref_config: dict[str, typing.Any],
    page_id: str,
    station: str,
    locator: str,
    page_dump: str,
    screenshot_base64: str,
    wm_size: dict,
    timeout: float = 60.0,
    *_args: typing.Any,
    **kwargs: typing.Any,
) -> typing.AsyncGenerator[dict, None]:
    """流式获取远程元素自愈事件。"""
    payload = {
        "llm_conf": request_llm_conf(pref_config),
        "app_id": config.CLIENT_DESCRIPTION,
        "page_id": page_id,
        "platform": station,
        "locator": locator,
        "page_dump": page_dump,
        "screenshot": f"data:image/png;base64,{screenshot_base64}",
        "wm_size": wm_size,
        "context": kwargs,
    }

    async for event in streaming(
        service_endpoints.endpoint("/mind-heal"),
        build_service_headers(),
        payload,
        timeout,
    ):
        if event.get("type") == "ping":
            continue
        if event.get("type") == "heal.step" and not str(
            event.get("message") or ""
        ):
            continue
        yield event


if __name__ == '__main__':
    pass
