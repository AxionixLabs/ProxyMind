#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#

import json
import httpx
import base64
import typing
from loguru import logger
from engine.channel import Channel
from mindnova import const


async def __streaming(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """Streaming"""

    async with httpx.AsyncClient(timeout=timeout) as client:
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

    url = f"https://api.appserverx.com/planner"
    headers = Channel.make_headers()
    payload = {
        "model"   : model,
        "apikey"  : apikey,
        "message" : message,
        "tools"   : openai_tools
    }

    async for event in __streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking" : logger.info(event["content"])
            case "done"     : logger.info("Plan done ...")
            case "plan"     :
                if (steps := event.get("steps")) and (loop_count := event.get("loop_count")):
                    logger.info(f"Loop Count -> {loop_count}")
                    for step in steps: logger.info(step["action"])
                else:
                    logger.warning(event)

        yield event


async def stream_heal(
    model: str,
    apikey: str,
    page_id: str,
    platform: str,
    by: typing.Literal["text", "id", "desc", "xpath"],
    value: str,
    page_dump: str,
    screenshot: str,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """Stream Heal"""

    url = "https://api.appserverx.com/self-heal"
    headers = Channel.make_headers()

    with open(screenshot, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model"       : model,
        "apikey"      : apikey,
        "app_id"      : const.APP_DESC,
        "page_id"     : page_id,
        "platform"    : platform,
        "old_locator" : {"by": by, "value": value},
        "page_dump"   : page_dump,
        "screenshot"  : f"data:image/png;base64,{image_b64}",
        "context"     : kwargs
    }

    async for event in __streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking" : logger.info(event["content"])
            case "done"     : logger.info("Heal done ...")
            case "heal"     : logger.info(event["content"])

        yield event


async def stream_chat(
    model: str,
    apikey: str,
    message: str,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """Stream Chat"""

    url = f"https://api.appserverx.com/chat"
    headers = Channel.make_headers()
    payload = {
        "model": model, "apikey": apikey, "message": message
    }

    async for event in __streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking" : logger.info(event["content"])

        yield event


if __name__ == '__main__':
    pass
