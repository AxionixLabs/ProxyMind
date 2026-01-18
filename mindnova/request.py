#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#
# Notes: ✦ Mind ✦ Copyright (c) 2026.
# Notes: Licensed use only · Redistribution requires explicit permission and approval.

import json
import httpx
import typing
from loguru import logger
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client
from mindnova import (
    authentic, const
)


async def stream_planner(
    payload: dict[str, typing.Any],
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
                logger.error(f"🔴 Error {event_dict.get('message')}")

    url = f"https://api.appserverx.com/planner"
    headers = {
        "Accept": "text/event-stream", "Content-Type": "application/json"
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


async def stream_session_call(
    model: str,
    apikey: str,
    message: str
) -> typing.AsyncGenerator[tuple[ClientSession, dict], None]:
    """Stream Session Call"""

    async def capture_error(response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                response.extensions["error_body"] = await response.aread()
            except Exception as e:
                _ = e; response.extensions["error_body"] = b""

    url = "http://127.0.0.1:3333/mcp"
    headers = {
        "Authorization": f"Bearer {authentic.manufacture_token()}"
    }

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

            for tool in openai_tools:
                logger.debug(f"⚙️ Tool {tool['function']['name']}")

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
