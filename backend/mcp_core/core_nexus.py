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
from backend.mcp_utils.utils_nexus import (
    StepResult, RunRecord, Tools, Build
)
from backend.utilities import const


class Nexus(object):
    """Nexus class."""

    agent_id: str = "nexus"

    def __init__(self):
        self.runs: dict[str, RunRecord] = {}

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
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Tools.url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        body_text_view: typing.Optional[str] = None
        body_json: typing.Any = None
        body_bytes: bytes = b""

        status: typing.Optional[int] = None
        resp_headers: dict[str, typing.Any] = {}
        resp_ct: typing.Optional[str] = None
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        ok: bool = False

        request_data = Build.build_request_http_like(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            body_text=body_text,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=form,
            files=files
        )

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = Tools.files_payload(files)

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
                    elapsed_ms = Tools.ms_since(t0)

                    status       = resp.status_code
                    resp_headers = dict(resp.headers)
                    resp_ct      = str(resp.headers.get("content-type") or "")
                    body_bytes    = resp.content

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    try:
                        body_text_view = resp.text
                    except (TypeError, ValueError, AttributeError):
                        body_text_view = None

                    # 如果 body 本身就是 image/video，则不保留 body_text/body_json
                    if Tools.detect_media_kind(resp_ct):
                        body_json = None
                        body_text_view = None

                    media_list, attachments, media_logs = await Tools.collect_media(
                        source_kind="http_body",
                        source=body_bytes,
                        content_type=resp_ct,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="http_media",
                        timeout=timeout
                    )

                    response_data = Build.build_response_http_like(
                        status=status,
                        headers=resp_headers,
                        elapsed_ms=elapsed_ms,
                        body_text=body_text_view,
                        body_json=body_json,
                        content_type=resp_ct,
                        content_length=len(body_bytes),
                        media=media_list
                    )

                    ok = 200 <= int(resp.status_code) < 400

                    pack = Build.build_pack(
                        text=f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        ok=ok,
                        request=request_data,
                        response=response_data,
                        attachments=attachments,
                        logs=media_logs[:],
                        error=last_err
                    )
                    return Build.finalize_pack(pack, extract=extract, asserts=asserts)

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)

        request_data = Build.build_request_http_like(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            body_text=body_text,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=form,
            files=files
        )

        response_data = Build.build_response_http_like(
            status=status,
            headers=resp_headers,
            elapsed_ms=elapsed_ms,
            body_text=body_text_view,
            body_json=body_json,
            content_type=resp_ct,
            content_length=len(body_bytes),
            media=media_list
        )

        pack = Build.build_pack(
            text=f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            ok=ok,
            request=request_data,
            response=response_data,
            attachments=attachments,
            logs=media_logs[:],
            error=last_err
        )
        return Build.finalize_pack(pack, extract=extract, asserts=asserts)

    @staticmethod
    async def sse(
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
        timeout: float = 60.0,
        retries: int = 0,
        follow_redirects: bool = True,
        max_events: typing.Optional[int] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Tools.url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        events: list[dict[str, typing.Any]] = []

        status: typing.Optional[int] = None
        resp_headers: dict[str, typing.Any] = {}
        resp_ct: typing.Optional[str] = None
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        ok: bool = False

        request_data = Build.build_request_http_like(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            body_text=body_text,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=form,
            files=files
        )

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = Tools.files_payload(files)

                    events: list[dict[str, typing.Any]] = []
                    status: typing.Optional[int] = None

                    async with client.stream(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
                    ) as resp:

                        status       = resp.status_code
                        resp_headers = dict(resp.headers)
                        resp_ct      = str(resp.headers.get("content-type") or "")
                        elapsed_ms   = Tools.ms_since(t0)

                        if status != 200:
                            response_data = Build.build_response_sse(
                                status=status,
                                headers=resp_headers,
                                elapsed_ms=elapsed_ms,
                                events=events,
                                content_type=resp_ct,
                                content_length=None,
                                media=media_list
                            )

                            pack = Build.build_pack(
                                text=f"SSE {method} {url} -> {status} ({elapsed_ms}ms)",
                                ok=ok,
                                request=request_data,
                                response=response_data,
                                attachments=attachments,
                                logs=media_logs[:],
                                error=last_err
                            )
                            return Build.finalize_pack(pack, extract=extract, asserts=asserts)

                        buf = ""
                        async for chunk in resp.aiter_text():
                            buf += chunk
                            buf = buf.replace("\r\n", "\n")

                            while "\n\n" in buf:
                                raw, buf = buf.split("\n\n", 1)
                                ev = Tools.sse_block(raw)
                                if not ev: continue

                                events.append({
                                    "event" : ev.event,
                                    "id"    : ev.id,
                                    "data"  : ev.data
                                })

                                if max_events and 0 < int(max_events) <= len(events):
                                    elapsed_ms = Tools.ms_since(t0)
                                    ok = (status == 200 and len(events) > 0)

                                    media_list, attachments, media_logs = await Tools.collect_media(
                                        source_kind="sse_events",
                                        source=events,
                                        media_index=media_index,
                                        media_path=media_path,
                                        save_response=save_response,
                                        save_dir=save_dir,
                                        tool="sse_media",
                                        timeout=timeout
                                    )

                                    response_data = Build.build_response_sse(
                                        status=status,
                                        headers=resp_headers,
                                        elapsed_ms=elapsed_ms,
                                        events=events,
                                        content_type=resp_ct,
                                        content_length=None,
                                        media=media_list
                                    )

                                    pack = Build.build_pack(
                                        text=f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                                        ok=ok,
                                        request=request_data,
                                        response=response_data,
                                        attachments=attachments,
                                        logs=media_logs[:],
                                        error=last_err
                                    )
                                    return Build.finalize_pack(pack, extract=extract, asserts=asserts)

                        if tail := Tools.sse_block(buf):
                            events.append({"event": tail.event, "id": tail.id, "data": tail.data})

                    elapsed_ms = Tools.ms_since(t0)
                    ok = (status == 200 and len(events) > 0)

                    media_list, attachments, media_logs = await Tools.collect_media(
                        source_kind="sse_events",
                        source=events,
                        media_index=media_index,
                        media_path=media_path,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="sse_media",
                        timeout=timeout
                    )

                    response_data = Build.build_response_sse(
                        status=status,
                        headers=resp_headers,
                        elapsed_ms=elapsed_ms,
                        events=events,
                        content_type=resp_ct,
                        content_length=None,
                        media=media_list
                    )

                    pack = Build.build_pack(
                        text=f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                        ok=ok,
                        request=request_data,
                        response=response_data,
                        attachments=attachments,
                        logs=media_logs[:],
                        error=last_err
                    )
                    return Build.finalize_pack(pack, extract=extract, asserts=asserts)

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)

        response_data = Build.build_response_sse(
            status=status,
            headers=resp_headers,
            elapsed_ms=elapsed_ms,
            events=events,
            content_type=resp_ct,
            content_length=None,
            media=media_list
        )

        pack = Build.build_pack(
            text=f"SSE {method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            ok=ok,
            request=request_data,
            response=response_data,
            attachments=attachments,
            logs=media_logs[:],
            error=last_err
        )
        return Build.finalize_pack(pack, extract=extract, asserts=asserts)

    @staticmethod
    async def ws(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
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

        elapsed_ms = Tools.ms_since(t0)
        ok = (last_err is None) or bool(recv)

        msg_target: list[typing.Any] = []
        for x in recv:
            try:
                msg_target.append(json.loads(x))
            except (TypeError, ValueError, json.JSONDecodeError):
                msg_target.append(x)

        media_list, attachments, media_logs = await Tools.collect_media(
            source_kind="ws_messages",
            source=msg_target,
            media_index=media_index,
            media_path=media_path,
            save_response=save_response,
            save_dir=save_dir,
            tool="ws_media",
            timeout=timeout
        )

        request_data = Build.build_request_ws(
            url=url,
            headers=headers,
            sends=sends,
            timeout=timeout,
            max_messages=max_messages
        )

        response_data = Build.build_response_ws(
            elapsed_ms=elapsed_ms,
            messages=recv,
            error=None if ok else last_err,
            media=media_list
        )

        pack = Build.build_pack(
            text=f"WS {url} msgs={len(recv)} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            response=response_data,
            attachments=attachments,
            logs=media_logs[:],
            error=last_err
        )
        return Build.finalize_pack(pack, extract=extract, asserts=asserts)

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
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        gql_method  = "POST"
        gql_url     = Tools.url_join(base_url, url)
        gql_headers = dict(headers or {})
        gql_headers.setdefault("Content-Type", "application/json")

        gql_payload: dict[str, typing.Any] = {
            "query": query,
            "variables": variables or {}
        }
        if operation_name:
            gql_payload["operationName"] = operation_name

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        body_text_view: typing.Optional[str] = None
        body_json: typing.Any = None
        body_bytes: bytes = b""
        gql_errors: typing.Any = None

        status: typing.Optional[int] = None
        resp_headers: dict[str, typing.Any] = {}
        resp_ct: typing.Optional[str] = None
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        ok: bool = False

        request_data = Build.build_request_http_like(
            method=gql_method,
            url=gql_url,
            headers=gql_headers,
            params=params,
            json_body=gql_payload,
            body_text=None,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=None,
            files=None
        )

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    resp = await client.request(
                        gql_method,
                        gql_url,
                        headers=gql_headers,
                        params=params,
                        json=gql_payload
                    )
                    elapsed_ms = Tools.ms_since(t0)

                    status       = resp.status_code
                    resp_headers = dict(resp.headers)
                    resp_ct      = str(resp.headers.get("content-type") or "")
                    body_bytes   = resp.content

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    try:
                        body_text_view = resp.text
                    except (TypeError, ValueError, AttributeError):
                        body_text_view = None

                    if Tools.detect_media_kind(resp_ct):
                        body_json = None
                        body_text_view = None

                    media_list, attachments, media_logs = await Tools.collect_media(
                        source_kind="json_body",
                        source=body_json,
                        media_path=media_path,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="graphql_media",
                        timeout=timeout
                    )

                    http_ok = 200 <= int(status) < 400

                    if isinstance(body_json, dict):
                        gql_errors = body_json.get("errors")
                    else:
                        gql_errors = None

                    ok = bool(http_ok) and not bool(gql_errors)

                    response_data = Build.build_response_http_like(
                        status=status,
                        headers=resp_headers,
                        elapsed_ms=elapsed_ms,
                        body_text=body_text_view,
                        body_json=body_json,
                        content_type=resp_ct,
                        content_length=len(body_bytes),
                        media=media_list
                    )

                    pack = Build.build_pack(
                        text=(
                            f"GQL POST {gql_url} -> {status} ({elapsed_ms}ms)"
                            if ok else
                            f"GQL POST {gql_url} -> FAIL ({elapsed_ms}ms)"
                        ),
                        ok=ok,
                        request=request_data,
                        response=response_data,
                        attachments=attachments,
                        logs=media_logs[:],
                        error=None,
                        extra_data=Build.build_gql_extra(
                            query=query,
                            variables=variables or {},
                            operation_name=operation_name,
                            errors=gql_errors
                        )
                    )
                    return Build.finalize_pack(pack, extract=extract, asserts=asserts)

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Tools.ms_since(t0)

        response_data = Build.build_response_http_like(
            status=status,
            headers=resp_headers,
            elapsed_ms=elapsed_ms,
            body_text=body_text_view,
            body_json=body_json,
            content_type=resp_ct,
            content_length=len(body_bytes),
            media=media_list
        )

        pack = Build.build_pack(
            text=f"GQL POST {gql_url} -> ERROR ({elapsed_ms}ms) {last_err}",
            ok=ok,
            request=request_data,
            response=response_data,
            attachments=attachments,
            logs=media_logs[:],
            error=last_err,
            extra_data=Build.build_gql_extra(
                query=query,
                variables=variables or {},
                operation_name=operation_name,
                errors=gql_errors
            )
        )
        return Build.finalize_pack(pack, extract=extract, asserts=asserts)

    # workflow: ==== Nexus MCP Tool ====
    async def nexus_http(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="http")

    # workflow: ==== Nexus MCP Tool ====
    async def nexus_sse(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="sse")

    # workflow: ==== Nexus MCP Tool ====
    async def nexus_ws(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="ws")

    # workflow: ==== Nexus MCP Tool ====
    async def nexus_graphql(
        self,
        payload: dict[str, typing.Any],
        concurrency: int = 1
    ) -> dict[str, typing.Any]:
        return await self.task_sequence(payload, concurrency, kind="graphql")

    async def task_sequence(
        self,
        payload: dict[str, typing.Any],
        concurrency: int,
        *,
        kind: typing.Literal["http", "sse", "ws", "graphql"]
    ) -> dict[str, typing.Any]:
        if not isinstance(payload, dict):
            return {
                "text"        : f"kind={kind} invalid payload",
                "attachments" : [],
                "data": {
                    "ok"      : False,
                    "kind"    : kind,
                    "payload" : payload
                },
                "logs": []
            }

        started_ms = Tools.ms_now()
        mission_id = f"nexus_{started_ms}"

        # 1) vars -> ctx
        ctx: dict[str, typing.Any] = {}
        if isinstance(payload.get("vars"), dict):
            ctx.update(payload["vars"])

        # 2) env / options 也走模板
        env   = payload.get("env") if isinstance(payload.get("env"), dict) else {}
        env_r = Tools.template(env, ctx) if env else {}

        base_url     = str(env_r.get("base_url") or "")
        base_headers = dict(env_r.get("headers") or {})
        base_timeout = float(env_r.get("timeout", 30.0))

        options   = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        options_r = Tools.template(options, ctx) if options else {}
        fail_fast = bool(options_r.get("fail_fast", True))

        items = payload.get("items")
        if isinstance(items, list) and items:
            raw_items = [x for x in items if isinstance(x, dict)]
        else:
            single_req = {
                k: v for k, v in dict(payload).items()
                if k not in {"name", "extract", "asserts", "items", "env", "vars", "options", "request"}
            }

            if isinstance(payload.get("request"), dict):
                legacy_req = dict(payload["request"])
                single_req = {**legacy_req, **single_req}

            raw_items = [{
                "name"    : payload.get("name"),
                "request" : single_req,
                "extract" : payload.get("extract"),
                "asserts" : payload.get("asserts")
            }]

        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def mission_once(i: int, item: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name = str(item.get("name") or f"{kind}_{i + 1:03d}")

                req = item.get("request")
                req = req if isinstance(req, dict) else {}

                if "url" not in req and isinstance(req.get("request"), dict):
                    req = dict(req["request"])

                req_r = Tools.template(req, ctx)

                item_extract = item.get("extract") if isinstance(item.get("extract"), dict) else None
                item_asserts = item.get("asserts") if isinstance(item.get("asserts"), list) else None

                extract_r = Tools.template(item_extract, ctx) if item_extract else None
                asserts_r = Tools.template(item_asserts, ctx) if item_asserts else None

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
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        extract=extract_r,
                        asserts=asserts_r,
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="http",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request"),
                            "response"       : data.get("response"),
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
                        }
                    )

                if kind == "sse":
                    pack = await self.sse(
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
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        max_events=(None if req_r.get("max_events", None) is None else int(req_r.get("max_events"))),
                        extract=extract_r,
                        asserts=asserts_r,
                        media_index=(None if req_r.get("media_index") is None else int(req_r.get("media_index"))),
                        media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="sse",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request") or {},
                            "response"       : data.get("response") or {},
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
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
                        follow_redirects=bool(req_r.get("follow_redirects", True)),
                        extract=extract_r,
                        asserts=asserts_r,
                        media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                        save_response=bool(req_r.get("save_response", False)),
                        save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Tools.ms_since(t0)
                    return i, StepResult(
                        name=name,
                        type="graphql",
                        ok=bool(data.get("ok")),
                        elapsed_ms=elapsed_ms,
                        detail={
                            "request"        : data.get("request"),
                            "response"       : data.get("response"),
                            "extract"        : data.get("extract"),
                            "asserts"        : data.get("asserts"),
                            "assert_summary" : data.get("assert_summary"),
                            "assert_ok"      : data.get("assert_ok"),
                            "attachments"    : pack.get("attachments")
                        }
                    )

                raw_sends = req_r.get("sends")
                if isinstance(raw_sends, list):
                    sends_v = [str(x) for x in raw_sends]
                elif raw_sends is None:
                    sends_v = []
                else:
                    sends_v = [str(raw_sends)]

                pack = await self.ws(
                    url=str(req_r.get("url", "")),
                    headers={**base_headers, **dict(req_r.get("headers") or {})},
                    sends=sends_v,
                    timeout=float(req_r.get("timeout", base_timeout)),
                    max_messages=int(req_r.get("max_messages", 10)),
                    extract=extract_r,
                    asserts=asserts_r,
                    media_index=(None if req_r.get("media_index") is None else int(req_r.get("media_index"))),
                    media_path=(str(req_r.get("media_path")) if req_r.get("media_path") else None),
                    save_response=bool(req_r.get("save_response", False)),
                    save_dir=(str(req_r.get("save_dir")) if req_r.get("save_dir") else None)
                )
                data = pack.get("data") or {}
                elapsed_ms = Tools.ms_since(t0)
                return i, StepResult(
                    name=name,
                    type="ws",
                    ok=bool(data.get("ok")),
                    elapsed_ms=elapsed_ms,
                    detail={
                        "request"        : data.get("request") or {},
                        "response"       : data.get("response") or {},
                        "extract"        : data.get("extract"),
                        "asserts"        : data.get("asserts"),
                        "assert_summary" : data.get("assert_summary"),
                        "assert_ok"      : data.get("assert_ok"),
                        "attachments"    : pack.get("attachments")
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
        finished_ms = Tools.ms_now()

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
        all_attachments: list[dict[str, typing.Any]] = []
        for s in step_results:
            all_attachments.extend((s.detail or {}).get("attachments") or [])

        return {
            "text"        : text,
            "attachments" : all_attachments,
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
                "steps"     : [Tools.step_dict(s) for s in step_results],
                "final_ctx" : ctx,
                "payload"   : payload,
                "evidence"  : {"steps": [Tools.step_dict(s) for s in step_results]}
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
