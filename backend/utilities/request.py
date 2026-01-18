#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import httpx
import base64
import typing
from loguru import logger
from backend.utilities import const


async def stream_self_heal(
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
                logger.info(f"🔵 {event_dict.get('message')}")
            case "done":
                logger.info(f"🟢 Heal done ...")
            case "error":
                logger.error(f"🔴 Error {event_dict.get('message')}")

    url = "https://api.appserverx.com/self-heal"
    headers = {
        "Accept": "text/event-stream", "Content-Type": "application/json"
    }

    with open(screenshot, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "app_id"      : const.APP_DESC,
        "page_id"     : page_id,
        "platform"    : platform,
        "old_locator" : {"by": by, "value": value},
        "page_dump"   : page_dump,
        "screenshot"  : f"data:image/png;base64,{image_b64}",
        "context"     : kwargs
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                body = await resp.aread()
                yield {
                    "type" : "error",
                    "code" : resp.status_code,
                    "tips" : body.decode(const.CHARSET, errors="replace")
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

                on_event(event)


if __name__ == '__main__':
    pass
