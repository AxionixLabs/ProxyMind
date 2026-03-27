# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import httpx
import typing
import platform
import mimetypes
from pathlib import Path
from loguru import logger
from engine.channel import Channel
from mind_app.stream_ui import StreamUI
from mind_nova import const


async def cap_request(req: httpx.Request) -> None:
    """记录请求摘要，便于排查鉴权和链路问题。"""
    a = req.headers.get("Authorization", "")
    logger.debug(
        f"[MCP] {req.method} {req.url} auth={'OK' if a.startswith('Bearer ') else 'MISSING'}"
    )


async def cap_response(response: httpx.Response) -> None:
    """捕获失败响应体，便于后续调试。"""
    if response.status_code >= 400:
        try:
            response.extensions["error_body"] = await response.aread()
        except Exception as e:
            _ = e
            response.extensions["error_body"] = b""


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = Channel.make_headers()
    params  = Channel.make_params() | {
        "station" : sys.platform,
        "arch"    : platform.machine()
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.request("GET", const.MANIFEST_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

    except Exception as e:
        return logger.debug(f"[Manifest] fetch failed: {type(e).__name__}: {e}")

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


async def streaming(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """按 SSE `data:` 行读取并解析事件流。"""
    async with httpx.AsyncClient(timeout=timeout, event_hooks={"response": [cap_response]}) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue

                yield event


async def post_stream_event(
    mode: typing.Literal["chat", "fast", "plan"],
    cid: str,
    sid: str,
    event: dict[str, typing.Any],
    *,
    timeout: float = 30.0
) -> None:
    """事件上报：把一条事件写入服务端缓存并广播给 SSE 订阅者。"""
    headers = Channel.make_headers()
    payload = {
        "mode"  : mode,
        "cid"   : cid,
        "sid"   : sid,
        "event" : event
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(const.STREAM_EVENT_URL, headers=headers, json=payload)
        r.raise_for_status()


async def upload_file_stream(
    path: str,
    agent_id: str,
    prefix: str = "uploads",
    timeout: float = 60.0
) -> dict[str, typing.Any]:
    """流式上传本地文件到服务端 /upload（服务端再流式转发到 R2）。"""
    if not (p := Path(path).expanduser()).exists() or not p.is_file():
        raise RuntimeError(f"upload_file_stream: file not exists: {p}")

    headers = Channel.make_headers()
    headers.pop("Content-Type", None)

    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    with p.open("rb") as f:
        data = {
            "agent_id": agent_id, "prefix": prefix
        }
        files = {"file": (p.name, f, ctype)}
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(const.FILE_STREAM_URL, headers=headers, data=data, files=files)
            r.raise_for_status()
            return r.json()


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: typing.Union[
        None,
        str,
        int,
        bool,
        float,
        list[typing.Any],
        dict[str, typing.Any]
    ]
) -> None:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()
    payload = {
        "cid"     : cid,
        "sid"     : sid,
        "call_id" : call_id,
        "name"    : name,
        "ok"      : ok,
        "result"  : result
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(const.TOOL_RESULT_URL, headers=headers, json=payload)
        r.raise_for_status()


async def stream_chat(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """流式获取 chat/fast 模式事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"     : mode,
        "llm_conf" : model_api,
        "message"  : message,
        "tools"    : openai_tools,
        **kwargs
    }
    if attachments:
        payload["attachments"] = attachments

    async for event in streaming(const.STREAM_CHAT_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type == "ping":
            continue

        yield event


async def stream_plan(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict],
    extras: typing.Optional[dict[str, typing.Any]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """流式获取静态规划事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"     : mode,
        "llm_conf" : model_api,
        "message"  : message,
        "tools"    : openai_tools,
        "extras"   : extras,
        **kwargs
    }

    async for event in streaming(const.STREAM_PLAN_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type in ["plan.done", "ping"]:
            continue

        yield event


async def stream_heal(
    model_api: dict[str, typing.Any],
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
        "llm_conf"   : model_api,
        "app_id"     : const.APP_DESC,
        "page_id"    : page_id,
        "platform"   : station,
        "locator"    : locator,
        "page_dump"  : page_dump,
        "screenshot" : f"data:image/png;base64,{screenshot_base64}",
        "wm_size"    : wm_size,
        "context"    : kwargs
    }

    async for event in streaming(const.STREAM_HEAL_URL, headers, payload, timeout):
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


async def stream_rule(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    context: dict[str, typing.Any],
    metadata: dict[str, typing.Any],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """流式获取规则执行事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"      : mode,
        "llm_conf"  : model_api,
        "message"   : message,
        "metadata"  : metadata,
        "extras"    : {"context" : context}
    }

    async for event in streaming(const.STREAM_RULE_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type in {"turn.thinking", "ping"}:
            continue

        yield event


if __name__ == '__main__':
    pass
