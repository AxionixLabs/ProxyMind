#   ____                 _   _
#  / ___|___  _ __ ___  | \ | | _____  ___   _ ___
# | |   / _ \| '__/ _ \ |  \| |/ _ \ \/ / | | / __|
# | |__| (_) | | |  __/ | |\  |  __/>  <| |_| \__ \
#  \____\___/|_|  \___| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
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


class Nexus(object):
    """Nexus class."""

    agent_id: str = "nexus"

    def __init__(self):
        self.runs: dict[str, RunRecord] = {}

    @staticmethod
    def ms_now() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def ms_since(t0: float) -> int:
        return int((time.perf_counter() - t0) * 1000)

    @staticmethod
    def url_join(base_url: typing.Optional[str], url: str) -> str:
        if not base_url:
            return url
        return base_url.rstrip("/") + "/" + url.lstrip("/")

    @staticmethod
    def template(x: typing.Any, ctx: dict[str, typing.Any]) -> typing.Any:
        if isinstance(x, str):
            s = x
            for k, v in ctx.items():
                s = s.replace("{{" + k + "}}", str(v))
            return s
        if isinstance(x, list):
            return [Nexus.template(i, ctx) for i in x]
        if isinstance(x, dict):
            return {k: Nexus.template(v, ctx) for k, v in x.items()}
        return x

    @staticmethod
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
    def pick(data: typing.Any, path: str) -> typing.Any:
        """按 a.b.0.c 路径取值"""
        if not path: return data

        cur = data
        for seg in str(path).split("."):
            if seg == "": continue
            if isinstance(cur, dict):
                if seg not in cur:
                    raise KeyError(seg)
                cur = cur[seg]
                continue

            if isinstance(cur, list):
                idx = int(seg)
                cur = cur[idx]
                continue

            raise KeyError(seg)

        return cur

    @staticmethod
    def safe_pick(data: typing.Any, path: str) -> tuple[bool, typing.Any]:
        try:
            return True, Nexus.pick(data, path)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    @staticmethod
    def compare(actual: typing.Any, op: str, expected: typing.Any = None) -> bool:
        match str(op or "").strip().lower():
            case "eq":
                return actual == expected
            case "ne":
                return actual != expected
            case "gt":
                return actual > expected
            case "ge":
                return actual >= expected
            case "lt":
                return actual < expected
            case "le":
                return actual <= expected
            case "contains":
                return str(expected) in str(actual)
            case "in":
                return actual in expected
            case "exists":
                return True
            case "empty":
                return actual in (None, "", [], {}, ())
            case "not_empty":
                return actual not in (None, "", [], {}, ())
            case "regex":
                return re.search(str(expected), str(actual or "")) is not None
            case _:
                raise ValueError(f"unsupported op: {op}")

    @staticmethod
    def apply_extract_assert(
        source: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:
        """
        对 source 做字段提取和断言校验。

        source:
          一般就是 pack["data"]

        extract:
          {"alias": "response.body_json.code"}

        asserts:
          [{"path":"response.status","op":"eq","value":200}]
        """
        extract = extract or {}
        asserts = asserts or []

        logs: list[str] = []
        extracted: dict[str, typing.Any] = {}

        for alias, path in extract.items():
            ok_pick, value = Nexus.safe_pick(source, str(path))
            if ok_pick:
                extracted[alias] = value
            else:
                extracted[alias] = None
                logs.append(f"extract[{alias}] {path} -> {value}")

        results: list[dict[str, typing.Any]] = []
        fail_count = 0

        for rule in asserts:
            if not isinstance(rule, dict):
                continue

            path = str(rule.get("path") or "").strip()
            op = str(rule.get("op") or "eq").strip().lower()
            expected = rule.get("value")

            ok_pick, actual = Nexus.safe_pick(source, path)

            if op == "exists":
                passed = bool(ok_pick)
                result = {
                    "path"     : path,
                    "op"       : op,
                    "expected" : None,
                    "actual"   : (actual if ok_pick else None),
                    "ok"       : passed,
                    "error"    : (None if ok_pick else actual)
                }
            else:
                if not ok_pick:
                    passed = False
                    result = {
                        "path"     : path,
                        "op"       : op,
                        "expected" : expected,
                        "actual"   : None,
                        "ok"       : False,
                        "error"    : actual
                    }
                else:
                    try:
                        passed = Nexus.compare(actual, op, expected)
                        result = {
                            "path"     : path,
                            "op"       : op,
                            "expected" : expected,
                            "actual"   : actual,
                            "ok"       : passed,
                            "error"    : None
                        }
                    except Exception as e:
                        passed = False
                        result = {
                            "path"     : path,
                            "op"       : op,
                            "expected" : expected,
                            "actual"   : actual,
                            "ok"       : False,
                            "error"    : f"{type(e).__name__}: {e}"
                        }

            if not passed:
                fail_count += 1

            results.append(result)

        total = len(results)
        return {
            "ok"      : (fail_count == 0),
            "extract" : extracted,
            "asserts" : results,
            "summary": {
                "total" : total,
                "pass"  : total - fail_count,
                "fail"  : fail_count
            },
            "logs": logs
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
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Nexus.url_join(base_url, url)
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
                    elapsed_ms = Nexus.ms_since(t0)

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    ok = 200 <= int(resp.status_code) < 400

                    pack =  {
                        "text"        : f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        "attachments" : [],
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"           : method,
                                "url"              : url,
                                "headers"          : headers,
                                "params"           : params,
                                "json"             : json_body,
                                "body_text"        : body_text,
                                "timeout"          : timeout,
                                "retries"          : retries,
                                "follow_redirects" : follow_redirects,
                                "form"             : form,
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

                    checked = Nexus.apply_extract_assert(
                        pack["data"], extract=extract, asserts=asserts
                    )

                    pack["data"]["extract"] = checked["extract"]
                    pack["data"]["asserts"] = checked["asserts"]
                    pack["data"]["assert_summary"] = checked["summary"]
                    pack["data"]["assert_ok"] = bool(checked["ok"])
                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                    pack["logs"].extend(checked["logs"])

                    if extract or asserts:
                        pack["text"] += (
                            f" extract={len(checked['extract'])}"
                            f" fail={checked['summary']['fail']}"
                        )

                    return pack

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

                finally:
                    for fp in opened_files:
                        try:
                            fp.close()
                        except OSError:
                            pass

        elapsed_ms = Nexus.ms_since(t0)

        pack = {
            "text"        : f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok": False,
                "request": {
                    "method"           : method,
                    "url"              : url,
                    "headers"          : headers,
                    "params"           : params,
                    "json"             : json_body,
                    "body_text"        : body_text,
                    "timeout"          : timeout,
                    "retries"          : retries,
                    "follow_redirects" : follow_redirects,
                    "form"             : form,
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
                    "status"     : None,
                    "headers"    : {},
                    "elapsed_ms" : elapsed_ms,
                    "body_text"  : None,
                    "body_json"  : None
                },
                "error": last_err
            },
            "logs": []
        }

        checked = Nexus.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

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
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
    ) -> dict[str, typing.Any]:

        method  = (method or "GET").upper()
        url     = Nexus.url_join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = Nexus.files_payload(files)

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

                        status     = resp.status_code
                        elapsed_ms = Nexus.ms_since(t0)

                        if status != 200:
                            pack = {
                                "text"        : f"SSE {method} {url} -> {status} ({elapsed_ms}ms)",
                                "attachments" : [],
                                "data": {
                                    "ok": False,
                                    "request": {
                                        "method"           : method,
                                        "url"              : url,
                                        "headers"          : headers,
                                        "params"           : params,
                                        "json"             : json_body,
                                        "body_text"        : body_text,
                                        "timeout"          : timeout,
                                        "retries"          : retries,
                                        "follow_redirects" : follow_redirects,
                                        "form"             : form,
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
                                        "status"     : status,
                                        "headers"    : dict(resp.headers),
                                        "elapsed_ms" : elapsed_ms,
                                        "events"     : events
                                    }
                                },
                                "logs": []
                            }

                            checked = Nexus.apply_extract_assert(
                                pack["data"], extract=extract, asserts=asserts
                            )

                            pack["data"]["extract"] = checked["extract"]
                            pack["data"]["asserts"] = checked["asserts"]
                            pack["data"]["assert_summary"] = checked["summary"]
                            pack["data"]["assert_ok"] = bool(checked["ok"])
                            pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                            pack["logs"].extend(checked["logs"])

                            if extract or asserts:
                                pack["text"] += (
                                    f" extract={len(checked['extract'])}"
                                    f" fail={checked['summary']['fail']}"
                                )

                            return pack

                        buf = ""
                        async for chunk in resp.aiter_text():
                            buf += chunk
                            buf = buf.replace("\r\n", "\n")

                            while "\n\n" in buf:
                                raw, buf = buf.split("\n\n", 1)
                                ev = Nexus.sse_block(raw)
                                if not ev: continue

                                events.append({
                                    "event" : ev.event,
                                    "id"    : ev.id,
                                    "data"  : ev.data
                                })

                                if max_events and 0 < int(max_events) <= len(events):
                                    elapsed_ms = Nexus.ms_since(t0)
                                    pack = {
                                        "text"        : f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                                        "attachments" : [],
                                        "data": {
                                            "ok": True,
                                            "request": {
                                                "method"           : method,
                                                "url"              : url,
                                                "headers"          : headers,
                                                "params"           : params,
                                                "json"             : json_body,
                                                "body_text"        : body_text,
                                                "timeout"          : timeout,
                                                "retries"          : retries,
                                                "follow_redirects" : follow_redirects,
                                                "form"             : form,
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
                                                "status"     : status,
                                                "headers"    : dict(resp.headers),
                                                "elapsed_ms" : elapsed_ms,
                                                "events"     : events
                                            }
                                        },
                                        "logs": []
                                    }

                                    checked = Nexus.apply_extract_assert(
                                        pack["data"], extract=extract, asserts=asserts
                                    )

                                    pack["data"]["extract"] = checked["extract"]
                                    pack["data"]["asserts"] = checked["asserts"]
                                    pack["data"]["assert_summary"] = checked["summary"]
                                    pack["data"]["assert_ok"] = bool(checked["ok"])
                                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                                    pack["logs"].extend(checked["logs"])

                                    if extract or asserts:
                                        pack["text"] += (
                                            f" extract={len(checked['extract'])}"
                                            f" fail={checked['summary']['fail']}"
                                        )

                                    return pack

                        if tail := Nexus.sse_block(buf):
                            events.append({"event": tail.event, "id": tail.id, "data": tail.data})

                    elapsed_ms = Nexus.ms_since(t0)
                    ok = (status == 200 and len(events) > 0)

                    pack = {
                        "text"        : f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                        "attachments" : [],
                        "data": {
                            "ok": ok,
                            "request": {
                                "method"           : method,
                                "url"              : url,
                                "headers"          : headers,
                                "params"           : params,
                                "json"             : json_body,
                                "body_text"        : body_text,
                                "timeout"          : timeout,
                                "retries"          : retries,
                                "follow_redirects" : follow_redirects,
                                "form"             : form,
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
                                "status"     : status,
                                "headers"    : dict(resp.headers),
                                "elapsed_ms" : elapsed_ms,
                                "events"     : events
                            }
                        },
                        "logs": []
                    }

                    checked = Nexus.apply_extract_assert(
                        pack["data"], extract=extract, asserts=asserts
                    )

                    pack["data"]["extract"] = checked["extract"]
                    pack["data"]["asserts"] = checked["asserts"]
                    pack["data"]["assert_summary"] = checked["summary"]
                    pack["data"]["assert_ok"] = bool(checked["ok"])
                    pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
                    pack["logs"].extend(checked["logs"])

                    if extract or asserts:
                        pack["text"] += (
                            f" extract={len(checked['extract'])}"
                            f" fail={checked['summary']['fail']}"
                        )

                    return pack

                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = Nexus.ms_since(t0)
        pack = {
            "text"        : f"SSE {method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            "attachments" : [],
            "data": {
                "ok": False,
                "request": {
                    "method"           : method,
                    "url"              : url,
                    "headers"          : headers,
                    "params"           : params,
                    "json"             : json_body,
                    "body_text"        : body_text,
                    "timeout"          : timeout,
                    "retries"          : retries,
                    "follow_redirects" : follow_redirects,
                    "form"             : form,
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
                    "status"     : None,
                    "headers"    : {},
                    "elapsed_ms" : elapsed_ms,
                    "events"     : []
                },
                "error": last_err
            },
            "logs": []
        }

        checked = Nexus.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

    @staticmethod
    async def ws(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
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

        elapsed_ms = Nexus.ms_since(t0)
        ok = (last_err is None) or bool(recv)

        pack =  {
            "text"        : f"WS {url} msgs={len(recv)} ({elapsed_ms}ms)",
            "attachments" : [],
            "data": {
                "ok": ok,
                "request": {
                    "url"          : url,
                    "headers"      : headers,
                    "sends"        : sends,
                    "timeout"      : timeout,
                    "max_messages" : max_messages
                },
                "response": {
                    "elapsed_ms" : elapsed_ms,
                    "messages"   : recv,
                    "error"     : (None if ok else last_err)
                }
            },
            "logs": []
        }

        checked = Nexus.apply_extract_assert(
            pack["data"], extract=extract, asserts=asserts
        )

        pack["data"]["extract"] = checked["extract"]
        pack["data"]["asserts"] = checked["asserts"]
        pack["data"]["assert_summary"] = checked["summary"]
        pack["data"]["assert_ok"] = bool(checked["ok"])
        pack["data"]["ok"] = bool(pack["data"]["ok"]) and bool(checked["ok"])
        pack["logs"].extend(checked["logs"])

        if extract or asserts:
            pack["text"] += (
                f" extract={len(checked['extract'])}"
                f" fail={checked['summary']['fail']}"
            )

        return pack

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
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None
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
            follow_redirects=follow_redirects,
            extract=extract,
            asserts=asserts
        )

        data      = pack.get("data") or {}
        resp      = data.get("response") or {}
        body_json = resp.get("body_json") if isinstance(resp, dict) else None

        gql_errors = None
        gql_ok     = bool(data.get("ok", False))

        if isinstance(body_json, dict):
            gql_errors = body_json.get("errors")
            if gql_errors:
                gql_ok = False

        data["graphql"] = {
            "query"          : query,
            "variables"      : variables or {},
            "operation_name" : operation_name,
            "errors"         : gql_errors
        }
        data["ok"] = gql_ok

        pack["data"] = data

        if gql_ok:
            pack["text"] = (
                f"GQL POST {Nexus.url_join(base_url, url)} "
                f"-> {resp.get('status')} ({resp.get('elapsed_ms')}ms)"
            )
        else:
            pack["text"] = (
                f"GQL POST {Nexus.url_join(base_url, url)} "
                f"-> FAIL ({resp.get('elapsed_ms')}ms)"
            )

        if extract or asserts:
            pack["text"] += (
                f" extract={len(data.get('extract') or {})}"
                f" fail={(data.get('assert_summary') or {}).get('fail', 0)}"
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
          items?: [
            {
              name?,
              request:{method?,url,base_url?,headers?,params?,json?,json_body?,body?,body_text?,form?,files?,timeout?,retries?,follow_redirects?},
              extract?: {alias: path},
              asserts?: [{path: str, op: str, value?: any}]
            }
          ]
          # files item: {field,path?|filename?|content_type?|text?|bytes?}
          # 单请求也允许直接放在顶层：method/url/base_url?/headers?/params?/json?/json_body?/body?/body_text?/form?/files?/timeout?/retries?/follow_redirects?/extract?/asserts?...
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
          items?: [
            {
              name?,
              request:{
                method?, url, base_url?, headers?, params?,
                json?, json_body?, body?, body_text?,
                form?, files?,
                timeout?, retries?, follow_redirects?,
                max_events?
              },
              extract?: {alias: path},
              asserts?: [{path: str, op: str, value?: any}]
            }
          ]
          # files item: {field,path?|filename?|content_type?|text?|bytes?}
          # 单请求也允许直接放在顶层：
          # method/url/base_url?/headers?/params?/json?/json_body?/body?/body_text?/form?/files?/timeout?/retries?/follow_redirects?/max_events?/extract?/asserts?...
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
          items?: [
            {
              name?,
              request:{url,headers?,sends?,timeout?,max_messages?},
              extract?: {alias: path},
              asserts?: [{path: str, op: str, value?: any}]
            }
          ]
          # 单请求也允许直接放在顶层：url/headers?/sends?/timeout?/max_messages?/extract?/asserts?...
        """
        return await self.task_sequence(payload, concurrency, kind="ws")

    # workflow: ==== MCP Tool ====
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
          items?: [
            {
              name?,
              request:{url,query,variables?,operation_name?,operationName?,base_url?,headers?,params?,timeout?,retries?,follow_redirects?},
              extract?: {alias: path},
              asserts?: [{path: str, op: str, value?: any}]
            }
          ]
          # 单请求也允许直接放在顶层：url/query/variables?/operation_name?/operationName?/base_url?/headers?/params?/timeout?/retries?/follow_redirects?/extract?/asserts?...
        """
        return await self.task_sequence(payload, concurrency, kind="graphql")

    async def task_sequence(
        self,
        payload: dict[str, typing.Any],
        concurrency: int,
        *,
        kind: typing.Literal["http", "sse", "ws", "graphql"]
    ) -> dict[str, typing.Any]:

        started_ms = Nexus.ms_now()
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
            raw_items = [{
                "name": payload.get("name"),
                "request": {
                    k: v for k, v in dict(payload).items()
                    if k not in {"name", "extract", "asserts", "items", "env", "vars", "options"}
                },
                "extract": payload.get("extract"),
                "asserts": payload.get("asserts")
            }]

        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def mission_once(i: int, item: dict[str, typing.Any]) -> tuple[int, StepResult]:
            async with sem:
                name  = str(item.get("name") or f"{kind}_{i+1:03d}")
                req   = item.get("request")
                req   = req if isinstance(req, dict) else {}
                req_r = Nexus.template(req, ctx)

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
                        extract=item.get("extract") if isinstance(item.get("extract"), dict) else None,
                        asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else None
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Nexus.ms_since(t0)
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
                            "assert_ok"      : data.get("assert_ok")
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
                        extract=item.get("extract") if isinstance(item.get("extract"), dict) else None,
                        asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else None
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Nexus.ms_since(t0)
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
                            "assert_ok"      : data.get("assert_ok")
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
                        extract=item.get("extract") if isinstance(item.get("extract"), dict) else None,
                        asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else None
                    )
                    data = pack.get("data") or {}
                    elapsed_ms = Nexus.ms_since(t0)
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
                            "assert_ok"      : data.get("assert_ok")
                        }
                    )

                # ws
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
                    extract=item.get("extract") if isinstance(item.get("extract"), dict) else None,
                    asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else None
                )
                data = pack.get("data") or {}
                elapsed_ms = Nexus.ms_since(t0)
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
                        "assert_ok"      : data.get("assert_ok")
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
        finished_ms = Nexus.ms_now()

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
