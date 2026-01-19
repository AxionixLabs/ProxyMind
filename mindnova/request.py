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
    timeout: float = 60.0,
    on_event: typing.Optional[typing.Callable] = None
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

                if on_event:
                    try:
                        on_event(event)
                    except Exception as e:
                        logger.debug(f"on_event failed: {type(e).__name__}: {e}")


async def stream_planner(
    model: str,
    apikey: str,
    message: str,
    openai_tools: list[dict],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """Stream Planner"""

    def on_event(event_dict: dict) -> None:
        match event_dict.get("type"):
            case "thinking":
                logger.info(f"🟣 {event_dict['content']}")
            case "plan":
                if steps := event_dict.get("steps"):
                    for step in steps: logger.info(f"🔵 {step['action']}")
                else:
                    logger.warning(f"🟠 {event_dict}")
            case "done":
                logger.info(f"🟢 Plan done ...")
            case "error":
                logger.error(f"🔴 Error {event_dict['content']}")

    url = f"https://api.appserverx.com/planner"
    headers = Channel.make_headers()
    payload = {
        "model"   : model,
        "apikey"  : apikey,
        "message" : message,
        "tools"   : openai_tools
    }

    async for event in __streaming(url, headers, payload, timeout, on_event):
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
    """Stream Self Heal"""

    def on_event(event_dict: dict) -> None:
        match event_dict.get("type"):
            case "thinking":
                logger.info(f"🟣 {event_dict['content']}")
            case "heal":
                logger.info(f"🔵 {event_dict['content']}")
            case "done":
                logger.info(f"🟢 Heal done ...")
            case "error":
                logger.error(f"🔴 Error {event_dict['content']}")

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

    async for event in __streaming(url, headers, payload, timeout, on_event):
        yield event


async def stream_chat(
    model: str,
    apikey: str,
    message: str,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """Stream Chat"""

    def on_event(event_dict: dict) -> None:
        match event_dict.get("type"):
            case "thinking":
                logger.info(f"🟣 {event_dict['content']}")

    url = f"https://api.appserverx.com/chat"
    headers = Channel.make_headers()
    payload = {
        "model": model, "apikey": apikey, "message": message
    }

    async for event in __streaming(url, headers, payload, timeout, on_event):
        yield event


if __name__ == '__main__':
    pass
