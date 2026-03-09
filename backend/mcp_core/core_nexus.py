#   ____                 _   _
#  / ___|___  _ __ ___  | \ | | _____  ___   _ ___
# | |   / _ \| '__/ _ \ |  \| |/ _ \ \/ / | | / __|
# | |__| (_) | | |  __/ | |\  |  __/>  <| |_| \__ \
#  \____\___/|_|  \___| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import time
import httpx
import typing
import asyncio
import websockets
from pathlib import Path
from loguru import logger
from dataclasses import (
    dataclass, field
)
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
    mission_id: str
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


def template(x: typing.Any, ctx: dict[str, typing.Any]) -> typing.Any:
    if isinstance(x, str):
        s = x
        for k, v in ctx.items():
            s = s.replace("{{" + k + "}}", str(v))
        return s
    if isinstance(x, list):
        return [template(i, ctx) for i in x]
    if isinstance(x, dict):
        return {k: template(v, ctx) for k, v in x.items()}
    return x


def sse_block(block: str) -> typing.Optional[SseEvent]:
    if not (raw := block.strip("\r\n")).strip():
        return None

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
    def step_dict(step: StepResult) -> dict[str, typing.Any]:
        return {
            "name"       : step.name,
            "type"       : step.type,
            "ok"         : step.ok,
            "elapsed_ms" : step.elapsed_ms,
            "detail"     : step.detail
        }

    @staticmethod
    def files_payload(
        items: typing.Optional[list[dict[str, typing.Any]]]
    ) -> typing.Optional[list[tuple[str, typing.Any]]]:
        if not items: return None
        payload: list[tuple[str, typing.Any]] = []

        for it in items:
            if not isinstance(it, dict):
                continue

            f = str(it.get("field") or "file")
            filename = str(it.get("filename") or "upload.bin")
            content_type = str(it.get("content_type") or "application/octet-stream")

            if it.get("path"):
                # 处理文件路径
                p = Path(str(it["path"])).expanduser()
                try:
                    # 确保文件路径存在，并尝试读取
                    with p.open("rb") as file:
                        file_content = file.read()  # 读取文件内容
                        payload.append((f, (filename or p.name, file_content, content_type)))
                except Exception as e:
                    logger.error(f"Error reading file {p}: {e}")
                    continue

            elif it.get("text") is not None:
                # 处理文本数据
                data = str(it.get("text") or "").encode(const.CHARSET)
                payload.append((f, (filename, data, content_type)))

            elif it.get("bytes") is not None:
                # 处理字节数据
                raw = it.get("bytes")
                if isinstance(raw, bytes):
                    payload.append((f, (filename, raw, content_type)))
                elif isinstance(raw, str):
                    payload.append((f, (filename, raw.encode(const.CHARSET), content_type)))

        return payload or None

    @staticmethod
    async def request(
        *,
        method: str,
        url: str,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                opened_files: list[typing.IO[bytes]] = []
                try:
                    files_payload = Nexus.files_payload(files)

                    if files_payload:
                        for _, val in files_payload:
                            if isinstance(val, tuple) and len(val) >= 2 and hasattr(val[1], "read"):
                                opened_files.append(val[1])

                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
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
                                "retries" : retries,
                                "form"    : form,
                                "files": [
                                    {
                                        "field"        : x.get("field"),
                                        "filename"     : x.get("filename"),
                                        "content_type" : x.get("content_type"),
                                        "path"         : x.get("path")
                                    }
                                    for x in (files or []) if isinstance(x, dict)
                                ]
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

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

                finally:
                    for fp in opened_files:
                        try:
                            fp.close()
                        except OSError:
                            pass

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
                    "form"    : form,
                    "files": [
                        {
                            "field"        : x.get("field"),
                            "filename"     : x.get("filename"),
                            "content_type" : x.get("content_type"),
                            "path"         : x.get("path")
                        }
                        for x in (files or []) if isinstance(x, dict)
                    ]
                },
                "error"      : last_err,
                "elapsed_ms" : elapsed_ms
            },
            "logs": []
        }

    @staticmethod
    async def sse(
        *,
        url: str,
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
                                "ok"     : False,
                                "status" : status,
                                "url"    : url
                            },
                            "logs": []
                        }

                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        buf = buf.replace("\r\n", "\n")

                        while "\n\n" in buf:
                            raw, buf = buf.split("\n\n", 1)
                            ev = sse_block(raw)
                            if not ev: continue

                            events.append({
                                "event" : ev.event,
                                "id"    : ev.id,
                                "data"  : ev.data
                            })

                            if len(events) >= int(max_events):
                                elapsed_ms = ms_since(t0)
                                return {
                                    "text"        : f"SSE {url} events={len(events)} ({elapsed_ms}ms)",
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
                "text"        : f"SSE {url} events={len(events)} ({elapsed_ms}ms)",
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
        *,
        url: str,
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

        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.WebSocketException,
            OSError,
            asyncio.TimeoutError
        ) as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ms_since(t0)
        ok = (last_err is None) or bool(recv)

        return {
            "text"        : f"WS {url} msgs={len(recv)} ({elapsed_ms}ms)",
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

    @staticmethod
    async def graphql(
        *,
        url: str,
        query: str,
        variables: typing.Optional[dict[str, typing.Any]] = None,
        operation_name: typing.Optional[str] = None,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True
    ) -> dict[str, typing.Any]:

        hd = dict(headers or {})
        hd.setdefault("Content-Type", "application/json")

        payload = {
            "query"     : query,
            "variables" : variables or {}
        }
        if operation_name:
            payload["operationName"] = operation_name

        pack = await Nexus.request(
            method="POST",
            url=url,
            base_url=base_url,
            headers=hd,
            params=params,
            json_body=payload,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects
        )

        data      = pack.get("data") or {}
        resp      = data.get("response") or {}
        body_json = resp.get("body_json") if isinstance(resp, dict) else None

        gql_ok     = bool(data.get("ok"))
        gql_errors = None

        if isinstance(body_json, dict):
            gql_errors = body_json.get("errors")
            if gql_errors:
                gql_ok = False

        data["ok"] = gql_ok
        data["graphql"] = {
            "query"          : query,
            "variables"      : variables or {},
            "operation_name" : operation_name,
            "errors"         : gql_errors
        }
        pack["data"] = data

        if gql_ok:
            pack["text"] = (
                f"GQL POST {url_join(base_url, url)} "
                f"-> {resp.get('status')} ({resp.get('elapsed_ms')}ms)"
            )
        else:
            pack["text"] = (
                f"GQL POST {url_join(base_url, url)} "
                f"-> FAIL ({resp.get('elapsed_ms')}ms)"
            )

        return pack

    # workflow: ==== MCP Tool ====
    async def nexus_http(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        """
        payload:
          env?: {base_url?: str, headers?: dict, timeout?: float}
          vars?: dict
          options?: {fail_fast?: bool}
          items?: [ {name?, request:{method?,url,base_url?,headers?,params?,json?,json_body?,body?,body_text?,form?,files?,timeout?,retries?,follow_redirects?}} ]
          # files item: {field,path?|filename?|content_type?|text?|bytes?}
          # 单请求也允许直接放在顶层：method/url/base_url?/headers?/params?/json?/json_body?/body?/body_text?/form?/files?/timeout?/retries?/follow_redirects?...
        """
        return await self.task_sequence(payload, concurrency, kind="http")

    # workflow: ==== MCP Tool ====
    async def nexus_sse(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        """
        payload:
          env?: {base_url?: str, headers?: dict, timeout?: float}
          vars?: dict
          options?: {fail_fast?: bool}
          items?: [ {name?, request:{url,headers?,params?,timeout?,max_events?}} ]
          # 单请求也允许直接放在顶层：url/params/max_events/...
        """
        return await self.task_sequence(payload, concurrency, kind="sse")

    # workflow: ==== MCP Tool ====
    async def nexus_ws(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        """
        payload:
          env?: {headers?: dict, timeout?: float}
          vars?: dict
          options?: {fail_fast?: bool}
          items?: [ {name?, request:{url,headers?,sends?,timeout?,max_messages?}} ]
          # 单请求也允许直接放在顶层：url/sends/max_messages/...
        """
        return await self.task_sequence(payload, concurrency, kind="ws")

    async def nexus_graphql(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        """
        payload:
          env?: {base_url?: str, headers?: dict, timeout?: float}
          vars?: dict
          options?: {fail_fast?: bool}
          items?: [ {name?, request:{url,query,variables?,operation_name?,headers?,params?,timeout?,retries?,follow_redirects?}} ]
          # 单请求也允许直接放在顶层：url/query/variables/...
        """
        return await self.task_sequence(payload, concurrency, kind="graphql")

    async def task_sequence(
        self,
        payload: dict[str, typing.Any],
        concurrency: int,
        *,
        kind: typing.Literal["http", "sse", "ws", "graphql"]
    ) -> dict[str, typing.Any]:

        started_ms = ms_now()
        mission_id = f"nexus_{started_ms}"

        env          = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        base_url     = str(env.get("base_url") or "")
        base_headers = dict(env.get("headers") or {})
        base_timeout = float(env.get("timeout", 30.0))

        options   = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        fail_fast = bool(options.get("fail_fast", True))

        ctx: dict[str, typing.Any] = {}
        if isinstance(payload.get("vars"), dict):
            ctx.update(payload["vars"])

        items = payload.get("items")
        if isinstance(items, list) and items:
            raw_items = [x for x in items if isinstance(x, dict)]
        else:
            raw_items = [{"name": payload.get("name"), "request": dict(payload)}]

        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def mission_once(i: int, item: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name  = str(item.get("name") or f"{kind}_{i+1:03d}")
                req   = item.get("request")
                req   = req if isinstance(req, dict) else {}
                req_r = template(req, ctx)

                t0 = time.perf_counter()

                if kind == "http":
                    pack = await self.request(
                        method=str(req_r.get("method", "GET")),
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        json_body=req_r.get("json") or req_r.get("json_body"),
                        body_text=req_r.get("body") or req_r.get("body_text"),
                        form=req_r.get("form") if isinstance(req_r.get("form"), dict) else None,
                        files=req_r.get("files") if isinstance(req_r.get("files"), list) else None,
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True))
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="http",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"  : data.get("request"),
                            "response" : data.get("response")
                        }
                    )

                if kind == "sse":
                    pack = await self.sse(
                        url=str(req_r.get("url", "")),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        max_events=int(req_r.get("max_events", 10))
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="sse",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "url"        : data.get("url"),
                            "status"     : data.get("status"),
                            "events"     : data.get("events") or [],
                            "elapsed_ms" : data.get("elapsed_ms")
                        }
                    )

                if kind == "graphql":
                    pack = await self.graphql(
                        url=str(req_r.get("url", "")),
                        query=str(req_r.get("query", "")),
                        variables=req_r.get("variables") if isinstance(req_r.get("variables"), dict) else {},
                        operation_name=req_r.get("operation_name") or req_r.get("operationName"),
                        base_url=str(req_r.get("base_url") or base_url) or None,
                        headers={**base_headers, **dict(req_r.get("headers") or {})},
                        params=req_r.get("params"),
                        timeout=float(req_r.get("timeout", base_timeout)),
                        retries=int(req_r.get("retries", 0)),
                        follow_redirects=bool(req_r.get("follow_redirects", True))
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="graphql",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"  : data.get("request"),
                            "response" : data.get("response"),
                            "graphql"  : data.get("graphql")
                        },
                    )

                # ws
                pack = await self.ws(
                    url=str(req_r.get("url", "")),
                    headers={**base_headers, **dict(req_r.get("headers") or {})},
                    sends=list(req_r.get("sends") or []),
                    timeout=float(req_r.get("timeout", base_timeout)),
                    max_messages=int(req_r.get("max_messages", 10)),
                )
                data = pack.get("data") or {}
                elapsed_ms = ms_since(t0)
                return i, StepResult(
                    name=name,
                    type="ws",
                    ok=bool(data.get("ok")),
                    elapsed_ms=elapsed_ms,
                    detail={
                        "url"        : data.get("url"),
                        "messages"   : data.get("messages") or [],
                        "elapsed_ms" : data.get("elapsed_ms")
                    }
                )

        tasks = [
            asyncio.create_task(mission_once(i, it)) for i, it in enumerate(raw_items)
        ]

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
            out.sort(key=lambda x: x[0])
            step_results = [sr for _, sr in out]

        ok_run = all(s.ok for s in step_results) if step_results else False
        finished_ms = ms_now()

        rec = RunRecord(
            mission_id=mission_id,
            ok=ok_run,
            started_ms=started_ms,
            finished_ms=finished_ms,
            payload=payload,
            final_ctx=ctx,
            steps=step_results
        )
        self.runs[mission_id] = rec

        text = f"kind={kind} total={len(step_results)} mission_id={mission_id}"

        return {
            "text"        : text,
            "attachments" : [],
            "data": {
                "ok"         : ok_run,
                "mission_id" : mission_id,
                "kind"       : kind,
                "summary": {
                    "total"   : len(step_results),
                    "pass"    : sum(1 for s in step_results if s.ok),
                    "fail"    : sum(1 for s in step_results if not s.ok),
                    "cost_ms" : finished_ms - started_ms
                },
                "steps"     : [self.step_dict(s) for s in step_results],
                "final_ctx" : ctx,
                "payload"   : payload,
                "evidence"  : {"steps": [self.step_dict(s) for s in step_results]}
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
