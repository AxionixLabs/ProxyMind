#  ____                            _
# |  _ \ ___  __ _ _   _  ___  ___| |_
# | |_) / _ \/ _` | | | |/ _ \/ __| __|
# |  _ <  __/ (_| | |_| |  __/\__ \ |_
# |_| \_\___|\__, |\__,_|\___||___/\__|
#               |_|
#

import json
import time
import httpx
import typing
import asyncio
import mimetypes
from pathlib import Path
from loguru import logger
from engine.channel import Channel
from engine.tinker import StreamTyperLogger
from mindnova import const


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
    cid: str,
    sid: str,
    event: dict[str, typing.Any],
    *,
    timeout: float = 30.0
) -> None:
    """事件上报：把一条事件写入服务端缓存并广播给 SSE 订阅者。"""

    url = f"https://api.appserverx.com/events-ingest"
    headers = Channel.make_headers()

    payload = {
        "cid"   : cid,
        "sid"   : sid,
        "event" : event
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
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

    url = "https://api.appserverx.com/upload"
    headers = Channel.make_headers()
    headers.pop("Content-Type", None)

    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    with p.open("rb") as f:
        data = {
            "agent_id": agent_id, "prefix": prefix
        }
        files = {"file": (p.name, f, ctype)}
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, data=data, files=files)
            r.raise_for_status()
            return r.json()


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: typing.Union[ None, bool, int, float, str, list[typing.Any], dict[str, typing.Any]]
) -> None:
    """Post tool result"""

    url = f"https://api.appserverx.com/tool-result"
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
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()


async def stream_plan(
    mode: str,
    model: str,
    apikey: str,
    message: str,
    openai_tools: list[dict],
    extras: typing.Optional[dict[str, typing.Any]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """Stream Planner"""

    url = f"https://api.appserverx.com/mind-plan"
    headers = Channel.make_headers()
    payload = {
        "mode"    : mode,
        "model"   : model,
        "apikey"  : apikey,
        "message" : message,
        "tools"   : openai_tools,
        "extras"  : extras,
        **kwargs
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


async def stream_chat(
    mode: str,
    model: str,
    apikey: str,
    message: str,
    openai_tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """Stream Chat"""

    url = f"https://api.appserverx.com/mind-chat"
    headers = Channel.make_headers()
    payload = {
        "mode"    : mode,
        "model"   : model,
        "apikey"  : apikey,
        "message" : message,
        "tools"   : openai_tools,
        **kwargs
    }
    if attachments:
        payload["attachments"] = attachments

    async for event in streaming(url, headers, payload, timeout):
        match event.get("type"):
            case "thinking":
                logger.debug(event["content"])
                continue
            case "done":
                logger.debug("Chat done ...")
                continue

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
    slog: typing.Optional[StreamTyperLogger] = None,
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
                if slog: await slog.feed(f"\n{event['content']}\n")
                else: logger.debug(event["content"])
                continue
            case "done":
                if slog: await slog.feed(f"\nHeal done ...\n")
                else: logger.debug("Heal done ...")
                continue
            case "heal":
                if slog: await slog.feed(f"\n{event['content']}\n")
                else: logger.debug(event["content"])

        yield event


class EventReport(object):
    """事件上报器（Strong Ordering）"""

    def __init__(
        self,
        mode: typing.Literal["chat", "fast", "plan"],
        cid: str,
        sid: str
    ):
        self.mode = mode
        
        self.cid = cid
        self.sid = sid

        self.timeout: float = 30.0

        self.seq = 0
        self.q: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue(maxsize=2000)

        self.stop = asyncio.Event()
        self.worker: typing.Optional[asyncio.Task] = None

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
            ev["mode"] = self.mode
            
            ev["cid"] = self.cid
            ev["sid"] = self.sid
            ev["seq"] = self.seq

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
                await post_stream_event(self.cid, self.sid, ev, timeout=self.timeout)
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
