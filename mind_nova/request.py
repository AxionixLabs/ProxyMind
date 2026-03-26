# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import time
import httpx
import typing
import asyncio
import platform
import mimetypes
from pathlib import Path
from loguru import logger
from engine.channel import Channel
from engine.tinker import StreamTyperLogger
from mind_nova import (
    const, craft
)


async def cap_request(req: httpx.Request) -> None:
    """Cap Request"""
    a = req.headers.get("Authorization", "")
    logger.debug(
        f"[MCP] {req.method} {req.url} auth={'OK' if a.startswith('Bearer ') else 'MISSING'}"
    )


async def cap_response(response: httpx.Response) -> None:
    """Cap Response"""
    if response.status_code >= 400:
        try:
            response.extensions["error_body"] = await response.aread()
        except Exception as e:
            _ = e
            response.extensions["error_body"] = b""


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
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
    """Streaming"""

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
    """Post function tool output back to the server loop."""
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
    """Stream stable turn events for chat and fast modes."""
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
        match event.get("type"):
            case "ping":
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
    """Stream plan events."""
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
        match event.get("type"):
            case "ping":
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
    slog: typing.Optional[StreamTyperLogger] = None,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """Stream stable heal events."""
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
                    await slog.feed(message, display=StreamTyperLogger.BLOCK)
                else:
                    logger.debug(message)
                continue

            case "heal.failed":
                error = str(event.get("error") or "unknown heal error")
                if slog:
                    await slog.feed(error, display=StreamTyperLogger.BLOCK)
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
    """Stream stable rule events."""
    headers = Channel.make_headers()
    payload = {
        "mode"      : mode,
        "llm_conf"  : model_api,
        "message"   : message,
        "metadata"  : metadata,
        "extras"    : {"context" : context}
    }

    async for event in streaming(const.STREAM_RULE_URL, headers, payload, timeout):
        match event.get("type"):
            case "ping":
                continue

        yield event


class EventReport(object):
    """事件上报器（Strong Ordering）"""

    PROTO_BY_MODE: dict[str, str] = {
        "chat": "mind.chat",
        "fast": "mind.chat",
        "plan": "mind.plan",
    }

    def __init__(
        self,
        mode: typing.Literal["chat", "fast", "plan"],
        cid: str,
        sid: str,
        proto: typing.Optional[str] = None,
    ):
        self.mode = mode

        self.cid = cid
        self.sid = sid
        self.proto = proto or self.PROTO_BY_MODE.get(mode, f"mind.{mode}")
        self.turn_id = craft.short_uid(12)
        self.round = 1

        self.timeout: float = 30.0

        self.seq = 0
        self.q: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue(maxsize=2000)

        self.stop = asyncio.Event()
        self.worker: typing.Optional[asyncio.Task] = None

    def begin_turn(
        self,
        turn_id: typing.Optional[str] = None,
        *,
        round_no: typing.Optional[int] = None
    ) -> str:
        self.turn_id = str(turn_id or craft.short_uid(12))
        if isinstance(round_no, int) and round_no > 0:
            self.round = round_no
        return self.turn_id

    def set_round(self, round_no: typing.Any) -> None:
        if isinstance(round_no, int) and round_no > 0:
            self.round = round_no

    def bind_event(self, event: dict[str, typing.Any]) -> None:
        if not isinstance(event, dict):
            return None

        if proto := event.get("proto"):
            self.proto = str(proto)
        if turn_id := event.get("turn_id"):
            self.turn_id = str(turn_id)
        self.set_round(event.get("round"))

    def emit(self, event: dict[str, typing.Any]) -> None:
        """
        非阻塞投递事件。
        - 自动注入 cid/sid/ts/seq
        - 队列满则丢弃（避免拖死主链路）
        """
        try:
            self.seq += 1
            ev = dict(event or {})
            ev.setdefault("ts", time.time())
            ev.setdefault("proto", self.proto)
            ev["cid"] = self.cid
            ev["sid"] = self.sid
            ev.setdefault("turn_id", self.turn_id)
            ev.setdefault("round", self.round)
            ev.setdefault("seq", self.seq)

            self.q.put_nowait(ev)
        except asyncio.QueueFull:
            logger.debug(f"[events] drop(queue_full) type={event.get('type')}")
        except RuntimeError:
            logger.debug(f"[events] drop(no_loop) type={event.get('type')}")

    async def open(self) -> None:
        """启动后台发送 worker（建议在 pack_start 前调用）"""
        if self.worker and not self.worker.done():
            return None
        self.worker = asyncio.create_task(self.work())

    async def work(self) -> None:
        """单 worker：严格按队列顺序发送"""
        while True:
            if self.stop.is_set() and self.q.empty():
                return None

            try:
                ev = await asyncio.wait_for(self.q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                await post_stream_event(self.mode, self.cid, self.sid, ev, timeout=self.timeout)
            except Exception as e:
                # 上报失败：不影响主流程
                logger.debug(
                    f"[events] post fail: {e!r} type={ev.get('type')} seq={ev.get('seq')}"
                )
            finally:
                self.q.task_done()

    async def flush(self) -> None:
        """等待队列清空（所有已 emit 的事件都发完）"""
        await self.q.join()

    async def close(self) -> None:
        """优雅停止：先 flush，再退出 worker"""
        await self.flush()
        self.stop.set()
        if self.worker:
            await self.worker


if __name__ == '__main__':
    pass
