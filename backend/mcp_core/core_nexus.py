#   ____                 _   _
#  / ___|___  _ __ ___  | \ | | _____  ___   _ ___
# | |   / _ \| '__/ _ \ |  \| |/ _ \ \/ / | | / __|
# | |__| (_) | | |  __/ | |\  |  __/>  <| |_| \__ \
#  \____\___/|_|  \___| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import time
import typing
import asyncio
import httpx
import websockets
from dataclasses import dataclass, field
from backend.utilities import const


@dataclass
class SseEvent:
    event: typing.Optional[str] = None
    data: str = ""
    id: typing.Optional[str] = None


@dataclass
class StepResult:
    name: str
    type: str
    ok: bool
    elapsed_ms: int
    detail: dict[str, typing.Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    ok: bool
    started_ms: int
    finished_ms: int
    payload: dict[str, typing.Any]
    final_ctx: dict[str, typing.Any]
    steps: list[StepResult]


def ms_now() -> int:
    return int(time.time() * 1000)


def ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def url_join(base_url: typing.Optional[str], url: str) -> str:
    if not base_url:
        return url
    return base_url.rstrip("/") + "/" + url.lstrip("/")


def tmpl(x: typing.Any, ctx: dict[str, typing.Any]) -> typing.Any:
    if isinstance(x, str):
        s = x
        for k, v in ctx.items():
            s = s.replace("{{" + k + "}}", str(v))
        return s
    if isinstance(x, list):
        return [tmpl(i, ctx) for i in x]
    if isinstance(x, dict):
        return {k: tmpl(v, ctx) for k, v in x.items()}
    return x


def sse_block(block: str) -> typing.Optional[SseEvent]:
    raw = block.strip("\r\n")
    if not raw.strip(): return None

    ev = SseEvent(event=None, data="", id=None)
    data_lines: list[str] = []

    for ln in raw.splitlines():
        if ln.startswith(":") or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        key = k.strip()
        val = v.lstrip()
        if key == "event":
            ev.event = val
        elif key == "data":
            data_lines.append(val)
        elif key == "id":
            ev.id = val

    ev.data = "\n".join(data_lines)
    return ev


class Nexus(object):
    """Nexus class."""

    agent_id: str = "nexus"

    def __init__(self):
        self.runs: dict[str, RunRecord] = {}

    @staticmethod
    def step_dict(s: StepResult) -> dict[str, typing.Any]:
        return {
            "name"       : s.name,
            "type"       : s.type,
            "ok"         : s.ok,
            "elapsed_ms" : s.elapsed_ms,
            "detail"     : s.detail
        }

    @staticmethod
    async def request(
        method: str,
        url: str,
        *,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = url_join(base_url, url)
        headers = headers or {}

        t0 = time.perf_counter()

        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text
                    )
                    elapsed_ms = ms_since(t0)

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    ok = 200 <= int(resp.status_code) < 400

                    return {
                        "text"        : f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        "attachments" : [],
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"  : method,
                                "url"     : url,
                                "headers" : headers,
                                "params"  : params,
                                "timeout" : timeout,
                                "retries" : retries
                            },
                            "response": {
                                "status"     : resp.status_code,
                                "headers"    : dict(resp.headers),
                                "elapsed_ms" : elapsed_ms,
                                "body_text"  : resp.text,
                                "body_json"  : body_json
                            },
                        },
                        "logs": []
                    }

                except (httpx.TimeoutException, httpx.RequestError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)

        return {
            "text"        : f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok": False,
                "request": {
                    "method"  : method,
                    "url"     : url,
                    "headers" : headers,
                    "params"  : params,
                    "timeout" : timeout,
                    "retries" : retries,
                },
                "error"      : last_err,
                "elapsed_ms" : elapsed_ms
            },
            "logs": []
        }

    @staticmethod
    async def sse(
        url: str,
        *,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        timeout: float = 30.0,
        max_events: int = 10
    ) -> dict[str, typing.Any]:

        url     = url_join(base_url, url)
        headers = headers or {}

        t0 = time.perf_counter()

        events: list[dict[str, typing.Any]] = []
        status: typing.Optional[int] = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", url, headers=headers, params=params) as resp:
                    status = resp.status_code
                    if status != 200:
                        return {
                            "text"        : f"SSE {url} -> {status}",
                            "attachments" : [],
                            "data": {
                                "ok": False, "status": status, "url": url
                            },
                            "logs": []
                        }

                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            raw, buf = buf.split("\n\n", 1)
                            ev = sse_block(raw)
                            if not ev: continue
                            events.append({"event": ev.event, "id": ev.id, "data": ev.data})

                            if len(events) >= int(max_events):
                                elapsed_ms = ms_since(t0)
                                return {
                                    "text"        : f"SSE {url} ok events={len(events)} ({elapsed_ms}ms)",
                                    "attachments" : [],
                                    "data": {
                                        "ok"         : True,
                                        "url"        : url,
                                        "status"     : status,
                                        "elapsed_ms" : elapsed_ms,
                                        "events"     : events
                                    },
                                    "logs": []
                                }

            elapsed_ms = ms_since(t0)
            ok = (status == 200 and len(events) > 0)
            return {
                "text"        : f"SSE {url} {'ok' if ok else 'ERROR'} events={len(events)} ({elapsed_ms}ms)",
                "attachments" : [],
                "data": {
                    "ok"         : ok,
                    "url"        : url,
                    "status"     : status,
                    "elapsed_ms" : elapsed_ms,
                    "events"     : events
                },
                "logs": []
            }

        except (httpx.TimeoutException, httpx.RequestError) as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)
        return {
            "text"        : f"SSE {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok"         : False,
                "url"        : url,
                "status"     : status,
                "elapsed_ms" : elapsed_ms,
                "error"      : last_err,
                "events"     : events
            },
            "logs": []
        }

    @staticmethod
    async def ws(
        url: str,
        *,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 30.0,
        max_messages: int = 10
    ) -> dict[str, typing.Any]:

        t0 = time.perf_counter()

        headers = headers or {}
        sends = sends or []
        recv: list[str] = []
        last_err: typing.Optional[str] = None

        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=timeout) as ws:
                for s in sends:
                    await ws.send(s)

                for _ in range(int(max_messages)):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except websockets.exceptions.ConnectionClosedOK:
                        break
                    recv.append(msg if isinstance(msg, str) else msg.decode(const.CHARSET, const.IGNORE))

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.WebSocketException,
                OSError,
                asyncio.TimeoutError) as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)

        ok = (last_err is None) or bool(recv)

        text = (
            f"WS {url} {'ok' if ok else 'ERROR'} "
            f"msgs={len(recv)} ({elapsed_ms}ms){'' if ok else ' ' + (last_err or '')}"
        )

        return {
            "text"        : text,
            "attachments" : [],
            "data": {
                "ok"         : ok,
                "url"        : url,
                "elapsed_ms" : elapsed_ms,
                "messages"   : recv,
                "error"      : (None if ok else last_err)
            },
            "logs": []
        }

    async def mission(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:

        started_ms = ms_now()
        run_id = f"nexus_{started_ms}"

        env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        base_url = str(env.get("base_url") or "")
        base_headers = dict(env.get("headers") or {})
        base_timeout = float(env.get("timeout", 30.0))

        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        fail_fast = bool(options.get("fail_fast", True))

        ctx: dict[str, typing.Any] = {}
        if isinstance(payload.get("vars"), dict):
            ctx.update(payload["vars"])

        steps_in = list(payload.get("steps") or [])
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def run_one(i: int, st: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name = str(st.get("name") or f"step_{i + 1:03d}")
                typ = str(st.get("type") or "http").lower()
                req = st.get("request") or {}

                req_r = tmpl(req, ctx)
                t0 = time.perf_counter()

                if typ == "http":
                    pack = await self.request(
                        method=str(req_r.get("method", "GET")),
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        json_body=req_r.get("json"),
                        body_text=req_r.get("body"),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = pack.get("data") or {}
                    return i, StepResult(
                        name=name,
                        type="http",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={"request": data.get("request"), "response": data.get("response")},
                    )

                if typ == "sse":
                    pack = await self.sse(
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        max_events=int(req_r.get("max_events", 10)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = pack.get("data") or {}
                    return i, StepResult(
                        name=name,
                        type="sse",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "url": data.get("url"),
                            "status": data.get("status"),
                            "events": data.get("events") or [],
                            "elapsed_ms": data.get("elapsed_ms"),
                        },
                    )

                if typ == "ws":
                    pack = await self.ws(
                        url=str(req_r.get("url", "")),
                        headers={**base_headers, **(req_r.get("headers") or {})},
                        sends=list(req_r.get("sends") or []),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        max_messages=int(req_r.get("max_messages", 10)),
                    )
                    elapsed_ms = ms_since(t0)
                    data = pack.get("data") or {}
                    return i, StepResult(
                        name=name,
                        type="ws",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "url"        : data.get("url"),
                            "messages"   : data.get("messages") or [],
                            "elapsed_ms" : data.get("elapsed_ms")
                        },
                    )

                elapsed_ms = ms_since(t0)
                return i, StepResult(
                    name=name,
                    type=typ,
                    ok=False,
                    elapsed_ms=elapsed_ms,
                    detail={"error": "unknown_step_type"},
                )

        tasks = [asyncio.create_task(run_one(i, st)) for i, st in enumerate(steps_in)]

        if fail_fast:
            pending = set(tasks)
            done_ordered: list[tuple[int, StepResult]] = []
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    idx, sr = await t
                    done_ordered.append((idx, sr))
                    if not sr.ok:
                        for p in pending:
                            p.cancel()
                        pending = set()
                        break
            done_ordered.sort(key=lambda x: x[0])
            step_results = [sr for _, sr in done_ordered]
        else:
            out = await asyncio.gather(*tasks, return_exceptions=False)
            step_results = [sr for _, sr in out]

        ok_run = all(s.ok for s in step_results) if step_results else False
        finished_ms = ms_now()

        self.runs[run_id] = RunRecord(
            run_id=run_id,
            ok=ok_run,
            started_ms=started_ms,
            finished_ms=finished_ms,
            payload=payload,
            final_ctx=ctx,
            steps=step_results,
        )

        text = f"nexus_mission ok={ok_run} steps={len(step_results)} run_id={run_id}"

        return {
            "text"        : text,
            "attachments" : [],
            "data": {
                "ok": ok_run,
                "run_id": run_id,
                "summary": {
                    "total"   : len(step_results),
                    "pass"    : sum(1 for s in step_results if s.ok),
                    "fail"    : sum(1 for s in step_results if not s.ok),
                    "cost_ms" : finished_ms - started_ms
                },
                "steps"     : [self.step_dict(s) for s in step_results],
                "final_ctx" : ctx,
                "payload"   : payload,
                "evidence"  : {"steps": [self.step_dict(s) for s in step_results]},
            },
            "logs": []
        }

    async def nexus_go(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:

        mode = str(payload.get("mode") or payload.get("type") or "flow").lower()

        env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        base_url = env.get("base_url")
        base_headers = env.get("headers") if isinstance(env.get("headers"), dict) else {}
        base_timeout = float(env.get("timeout", 30.0))

        if mode == "http":
            return await self.request(
                method=str(payload.get("method", "GET")),
                url=str(payload.get("url", "")),
                base_url=str(payload.get("base_url") or base_url) or None,
                headers={**base_headers, **(payload.get("headers") or {})},
                params=payload.get("params"),
                json_body=payload.get("json") or payload.get("json_body"),
                body_text=payload.get("body") or payload.get("body_text"),
                timeout=float(payload.get("timeout", base_timeout)),
                retries=int(payload.get("retries", 0)),
                follow_redirects=bool(payload.get("follow_redirects", True)),
            )

        if mode == "sse":
            return await self.sse(
                url=str(payload.get("url", "")),
                base_url=str(payload.get("base_url") or base_url) or None,
                headers={**base_headers, **(payload.get("headers") or {})},
                params=payload.get("params"),
                timeout=float(payload.get("timeout", base_timeout)),
                max_events=int(payload.get("max_events", 10)),
            )

        if mode == "ws":
            return await self.ws(
                url=str(payload.get("url", "")),
                headers={**base_headers, **(payload.get("headers") or {})},
                sends=list(payload.get("sends") or []),
                timeout=float(payload.get("timeout", base_timeout)),
                max_messages=int(payload.get("max_messages", 10)),
            )

        return await self.mission(payload, concurrency)


if __name__ == '__main__':
    pass
