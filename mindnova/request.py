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
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client
from mindnova import (
    authentic, const
)


async def streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, typing.Any],
    payload: dict[str, typing.Any],
    on_event: typing.Callable,
) -> typing.AsyncGenerator[dict, None]:

    async with client.stream("POST", url, headers=headers, json=payload) as resp:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            body = await resp.aread()
            yield {
                "type": "error",
                "code": resp.status_code,
                "tips": body.decode(const.CHARSET, errors="replace")
            }
            return

        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue

            try:
                event = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue

            yield event; on_event(event)


async def stream_planner(
    payload: dict[str, typing.Any],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:

    def handle_event(event: dict) -> None:
        match event.get("type"):
            case "thinking":
                logger.info(f"🟣 {event['content']}")
            case "plan":
                if steps := event.get("steps"):
                    for step in steps: logger.info(f"🔵 {step['action']}")
                else:
                    logger.warning(f"🟠 {event}")
            case "done":
                logger.info(f"🟢 Plan done ...")
            case "error":
                logger.error(f"🔴 Error {event.get('message')}")

    url = f"https://api.appserverx.com/planner"
    headers = {
        "Accept": "text/event-stream", "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async for data in streaming(client, url, headers, payload, handle_event):
            yield data


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

    def handle_event(event: dict) -> None:
        match event.get("type"):
            case "thinking":
                logger.info(f"🟣 {event['content']}")
            case "heal":
                logger.info(f"🔵 {event.get('message')}")
            case "done":
                logger.info(f"🟢 Heal done ...")
            case "error":
                logger.error(f"🔴 Error {event.get('message')}")

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
        async for data in streaming(client, url, headers, payload, handle_event):
            yield data


async def stream_session_call(
    model: str,
    apikey: str,
    message: str
) -> typing.AsyncGenerator[tuple[ClientSession, dict], None]:

    url = "http://127.0.0.1:3333/mcp"
    headers = {
        "Authorization": f"Bearer {authentic.manufacture_token()}"
    }

    async def capture_error(response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                response.extensions["error_body"] = await response.aread()
            except Exception as e:
                _ = e; response.extensions["error_body"] = b""

    http_client = httpx.AsyncClient(headers=headers, event_hooks={"response": [capture_error]})

    async with streamable_http_client(url, http_client=http_client) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            list_tools: ListToolsResult = await session.list_tools()

            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description" : tool.description,
                        "parameters"  : tool.inputSchema
                    }
                }
                for tool in list_tools.tools
            ]

            for tool in openai_tools: logger.debug(f"⚙️ Tool {tool['function']['name']}")

            payload = {
                "model"   : model,
                "apikey"  : apikey,
                "message" : message,
                "tools"   : openai_tools
            }

            yield session, payload

    await http_client.aclose()


if __name__ == '__main__':
    pass
