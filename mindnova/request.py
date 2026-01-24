#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#

import json
import httpx
import typing
from loguru import logger
from engine.channel import Channel
from mindnova import const


async def capture(response: httpx.Response) -> None:
    """Capture"""
    if response.status_code >= 400:
        try:
            response.extensions["error_body"] = await response.aread()
        except Exception as e:
            _ = e
            response.extensions["error_body"] = b""


async def streaming(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """Streaming"""

    async with httpx.AsyncClient(timeout=timeout, event_hooks={"response": [capture]}) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                body = await resp.aread()
                yield {
                    "type"    : "error",
                    "code"    : resp.status_code,
                    "content" : body.decode(const.CHARSET, errors="replace")
                }
                return

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue

                yield event


async def stream_plan(
    model: str,
    apikey: str,
    message: str,
    openai_tools: list[dict],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """Stream Planner"""

    url = f"https://api.appserverx.com/mind-plan"
    headers = Channel.make_headers()
    payload = {
        "model"   : model,
        "apikey"  : apikey,
        "message" : message,
        "tools"   : openai_tools
    }

    async for event in streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking":
                logger.debug(event["content"])
                continue
            case "done":
                logger.debug("Plan done ...")
                continue
            case "plan":
                if not (steps := event.get("steps")) or not (loop_count := event.get("loop_count")):
                    logger.warning(event)
                    continue
                logger.debug(f"Loop Count -> {loop_count}")
                for step in steps: logger.debug(step["action"])

        yield event


async def stream_heal(
    model: str,
    apikey: str,
    page_id: str,
    platform: str,
    locator: str,
    page_dump: str,
    screenshot_base64: str,
    wm_size: dict,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """Stream Heal"""

    url = "https://api.appserverx.com/mind-heal"
    headers = Channel.make_headers()

    payload = {
        "model"      : model,
        "apikey"     : apikey,
        "app_id"     : const.APP_DESC,
        "page_id"    : page_id,
        "platform"   : platform,
        "locator"    : locator,
        "page_dump"  : page_dump,
        "screenshot" : f"data:image/png;base64,{screenshot_base64}",
        "wm_size"    : wm_size,
        "context"    : kwargs
    }

    async for event in streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking":
                logger.debug(event["content"])
                continue
            case "done":
                logger.debug("Heal done ...")
                continue
            case "heal":
                logger.debug(event["content"])

        yield event


async def stream_chat(
    model: str,
    apikey: str,
    message: str,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """Stream Chat"""

    url = f"https://api.appserverx.com/mind-chat"
    headers = Channel.make_headers()
    payload = {
        "model": model, "apikey": apikey, "message": message
    }

    async for event in streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking":
                logger.debug(event["content"])
                continue
            case "done":
                logger.debug("Chat done ...")
                continue

        yield event


if __name__ == '__main__':
    pass
