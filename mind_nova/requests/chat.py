# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger
from engine.channel import Channel
from mind_app.stream_ui import StreamUI
from mind_nova.requests.payload import (
    build_chat_payload,
    request_llm_conf
)
from mind_nova.requests.streaming import streaming
from mind_nova.services import service_endpoints
from mind_nova import const


async def stream_chat(
    mode: str,
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """流式获取 chat/fast/xtra 模式事件。"""
    headers = Channel.make_headers()
    payload = await build_chat_payload(
        mode,
        pref_config,
        message,
        tools,
        attachments,
        **kwargs
    )

    async for event in streaming(service_endpoints.endpoint("/mind-chat"), headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type == "ping":
            continue

        yield event


async def stream_heal(
    pref_config: dict[str, typing.Any],
    page_id: str,
    station: str,
    locator: str,
    page_dump: str,
    screenshot_base64: str,
    wm_size: dict,
    timeout: float = 60.0,
    slog: typing.Optional[StreamUI] = None,
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
        match event.get("type"):
            case "ping":
                continue

            case "heal.step":
                message = str(event.get("message") or "")
                if not message:
                    continue
                if slog:
                    await slog.feed(message, display=StreamUI.BLOCK)
                else:
                    logger.debug(message)
                continue

            case "heal.failed":
                error = str(event.get("error") or "unknown heal error")
                if slog:
                    await slog.feed(error, display=StreamUI.BLOCK)
                else:
                    logger.debug(error)

        yield event


if __name__ == '__main__':
    pass
